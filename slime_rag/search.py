# -*- coding: utf-8 -*-
"""
검색·근거 답변 (Phase 4) — 하이브리드 검색 + 메타필터 + 인용 답변.

하이브리드: 벡터(dense, BGE-M3/pgvector) + 키워드(BM25, kiwipiepy 형태소) → RRF 융합.
  한국어는 Postgres FTS 토크나이저가 없어 BM25 를 앱단에서 돌린다(초성·은어 대응).
메타필터: 마켓/종류/감성 컬럼으로 후보를 좁힌 뒤 검색.
답변: 검색된 렌더링 근거만 인용. 소스(디시/인스타)를 평균내지 않고 소스별로 드러낸다.
      원문 미재배포 — 저장된 구조화 렌더링만 근거로 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import connect
from . import index
from .llm_ops import LLM
from .config import settings

# 메타필터에 허용하는 컬럼(화이트리스트 — SQL 인젝션 방지).
# product 필터 주의: linking 이 보류(abstain)한 행은 product=NULL 이라 제품 필터에서 빠진다 —
# 배송/CS 같은 마켓 단위 후기는 '특정 제품' 검색엔 안 나오는 게 의도된 범위다.
_FILTERABLE = {"market", "product", "slime_type", "scent_sentiment", "texture_sentiment",
               "sound_sentiment", "overall_sentiment", "source"}
_RRF_K = 60


@dataclass
class Answer:
    text: str
    citations: list[dict] = field(default_factory=list)


def _where(filters: dict | None) -> tuple[str, list]:
    if not filters:
        return "", []
    clauses, params = [], []
    for col, val in filters.items():
        if col not in _FILTERABLE:
            raise ValueError(f"필터 불가 컬럼: {col}")
        clauses.append(f"{col} = %s")
        params.append(val)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _dense(qvec, where: str, wparams: list, k: int, conn) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT id, source, market, product, evidence, attributes,
               1 - (embedding <=> %s) AS score
        FROM reviews {where}
        ORDER BY embedding <=> %s
        LIMIT %s
        """,
        [qvec, *wparams, qvec, k],
    ).fetchall()
    cols = ("id", "source", "market", "product", "evidence", "attributes", "score")
    return [dict(zip(cols, r)) for r in rows]


def _sparse(query: str, where: str, wparams: list, k: int, conn, cap: int = 500) -> list[dict]:
    rows = conn.execute(
        f"SELECT id, source, market, product, evidence, attributes, tokens "
        f"FROM reviews {where} LIMIT %s",
        [*wparams, cap],
    ).fetchall()
    if not rows:
        return []
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi([r[6] or [] for r in rows])
    scores = bm25.get_scores(index._tokenize(query))
    ranked = sorted(zip(rows, scores), key=lambda x: x[1], reverse=True)[:k]
    cols = ("id", "source", "market", "product", "evidence", "attributes")
    return [dict(zip(cols, r[:6]), score=float(s)) for r, s in ranked]


def _rrf(dense: list[dict], sparse: list[dict], top_k: int) -> list[dict]:
    """Reciprocal Rank Fusion — 점수 스케일이 다른 두 랭킹을 순위로만 융합."""
    fused: dict[int, dict] = {}
    for ranking in (dense, sparse):
        for rank, hit in enumerate(ranking):
            slot = fused.setdefault(hit["id"], {**hit, "rrf": 0.0})
            slot["rrf"] += 1.0 / (_RRF_K + rank)
    return sorted(fused.values(), key=lambda h: h["rrf"], reverse=True)[:top_k]


def search(query: str, *, filters: dict | None = None, top_k: int = 8) -> list[dict]:
    """하이브리드(dense+BM25) + 메타필터. 융합 순위로 청크 리스트 반환."""
    where, wparams = _where(filters)
    qvec = index.embed([query])[0]
    with connect() as conn:
        dense = _dense(qvec, where, wparams, top_k, conn)
        sparse = _sparse(query, where, wparams, top_k, conn)
    return _rrf(dense, sparse, top_k)


# ---------------------------------------------------------------- 근거 답변
_ANSWER_SYSTEM = """\
너는 슬라임 후기 검색 결과만 근거로 답한다. 근거에 없는 내용은 말하지 마라(모르면 모른다고).
소스를 하나로 평균내지 마라: 디시(amos)와 인스타를 소스별로 구분해 보여라.
각 주장 끝에 근거 번호 [n] 을 단다. 간결한 한국어."""


def _format_context(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        lines.append(f"[{i}] (source={c['source']}, market={c['market']}, "
                     f"product={c['product']}) {c['evidence']}")
    return "\n".join(lines)


def answer(query: str, *, filters: dict | None = None, top_k: int = 8) -> Answer:
    """검색 → 근거만으로 소스 구분 답변(인용 번호 포함)."""
    chunks = search(query, filters=filters, top_k=top_k)
    if not chunks:
        return Answer("근거가 될 후기를 찾지 못했습니다.", [])
    prompt = f"질문: {query}\n\n근거:\n{_format_context(chunks)}\n\n위 근거만으로 답하라."
    text = LLM().complete(prompt, system=_ANSWER_SYSTEM,
                          model=settings.model_judge, label="search.answer")
    citations = [{"n": i, "source": c["source"], "market": c["market"],
                  "product": c["product"], "evidence": c["evidence"]}
                 for i, c in enumerate(chunks, 1)]
    return Answer(text, citations)


if __name__ == "__main__":
    # Docker pgvector + 색인 데이터 + OPENAI_API_KEY 필요.
    a = answer("빈짱 한줌 향 어때?", filters={"market": "빈짱"})
    print(a.text)
    print("\n근거:")
    for c in a.citations:
        print(f"  [{c['n']}] {c['source']}/{c['market']}/{c['product']}: {c['evidence']}")
