# -*- coding: utf-8 -*-
"""
검색·근거 답변 (Phase 4) — 하이브리드 검색 + 메타필터 + 인용 답변.

하이브리드: 벡터(dense, BGE-M3/pgvector) + 키워드(BM25, kiwipiepy 형태소) → RRF 융합.
  한국어는 Postgres FTS 토크나이저가 없어 BM25 를 앱단에서 돌린다(초성·은어 대응).
메타필터: 마켓/종류/감성 컬럼으로 후보를 좁힌 뒤 검색.
답변: 검색된 렌더링 근거만 인용. 소스(디시/인스타)를 평균내지 않고 소스별로 드러낸다.
      근거는 저장된 구조화 렌더링만 쓴다(검색 청크 단위와 일치). 원문 전문은 표시 대상이
      아니라 링크로 간다 — ADR-0013.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import connect
from . import index, source_links, consolidated_view   # TONE: 요약 말투를 한 곳에서만 정한다
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


# dense·sparse 가 공유하는 기본 SELECT 컬럼. 위치가 아니라 **이름**으로 묶는다 —
# 예전엔 `r[6]`(tokens)·`r[:6]` 처럼 위치를 하드코딩해, 중간에 컬럼 하나만 끼워 넣어도
# 예외 없이 BM25 가 엉뚱한 값을 먹었다. 이름 기반이면 그런 조용한 파손이 불가능해진다.
_BASE_COLS = ("id", "source", "market", "product", "evidence", "attributes", "source_ref")
_BASE_SELECT = ", ".join(_BASE_COLS)


def _hit(cols: tuple[str, ...], row) -> dict:
    """행 튜플 → 이름 붙은 dict. 길이가 어긋나면 조용히 잘리지 않고 즉시 터진다.

    `zip` 은 기본적으로 짧은 쪽에 맞춰 **말없이 절단**한다 — SELECT 만 늘리고 cols 를 안 늘리면
    컬럼이 사라진 채로 검색이 계속 돈다. strict=True(Py3.11+, CI 도 3.11)로 그걸 예외로 바꾼다.
    """
    if len(cols) != len(row):
        raise ValueError(f"SELECT 컬럼 수 불일치: cols={len(cols)} row={len(row)}")
    return dict(zip(cols, row, strict=True))


def _dense(qvec, where: str, wparams: list, k: int, conn) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT {_BASE_SELECT},
               1 - (embedding <=> %s) AS score
        FROM reviews {where}
        ORDER BY embedding <=> %s
        LIMIT %s
        """,
        [qvec, *wparams, qvec, k],
    ).fetchall()
    cols = (*_BASE_COLS, "score")
    return [_hit(cols, r) for r in rows]


def _sparse(query: str, where: str, wparams: list, k: int, conn, cap: int = 500) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_BASE_SELECT}, tokens FROM reviews {where} LIMIT %s",
        [*wparams, cap],
    ).fetchall()
    if not rows:
        return []
    cols = (*_BASE_COLS, "tokens")
    hits = [_hit(cols, r) for r in rows]
    from rank_bm25 import BM25Okapi
    bm25 = BM25Okapi([h["tokens"] or [] for h in hits])
    scores = bm25.get_scores(index._tokenize(query))
    ranked = sorted(zip(hits, scores, strict=True), key=lambda x: x[1], reverse=True)[:k]
    # tokens 는 BM25 입력일 뿐 소비자(융합·인용)에겐 노이즈 → 여기서 떨군다.
    return [{**{c: h[c] for c in _BASE_COLS}, "score": float(s)} for h, s in ranked]


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
출처를 하나로 평균내지 마라: 디시(amos)와 인스타를 출처별로 구분해 보여라.
각 주장 끝에 근거 번호 [n] 을 단다. 간결한 한국어.
{tone}""".format(tone=consolidated_view.TONE)


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
        return Answer("근거가 될 후기를 찾지 못했어요.", [])
    prompt = f"질문: {query}\n\n근거:\n{_format_context(chunks)}\n\n위 근거만으로 답하라."
    text = LLM().complete(prompt, system=_ANSWER_SYSTEM,
                          model=settings.model_judge, label="search.answer")
    # url: 근거를 원문에서 검증할 수 있게 한다(AI 요약 불신 대응). `.get` 이라 옛 청크 모양이
    # 섞여 들어와도 예외 없이 '링크 없음'으로 degrade — 틀린 링크보다 링크 없음이 낫다.
    citations = [{"n": i, "source": c["source"], "market": c["market"],
                  "product": c["product"], "evidence": c["evidence"],
                  "url": source_links.permalink(c.get("source_ref"))}
                 for i, c in enumerate(chunks, 1)]
    return Answer(text, citations)


if __name__ == "__main__":
    # Docker pgvector + 색인 데이터 + OPENAI_API_KEY 필요.
    a = answer("빈짱 한줌 향 어때?", filters={"market": "빈짱"})
    print(a.text)
    print("\n근거:")
    for c in a.citations:
        print(f"  [{c['n']}] {c['source']}/{c['market']}/{c['product']}: {c['evidence']}")
