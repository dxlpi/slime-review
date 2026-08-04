# -*- coding: utf-8 -*-
"""
색인 (Phase 4) — 추출·연결된 후기 → BGE-M3 임베딩 → pgvector 적재.

청킹: 제품 항목 1개 = 1청크(1행).
무재배포: 원문 본문을 저장하지 않는다. 구조화 필드로 만든 '렌더링 텍스트'를
         임베딩·보관하고, 이 텍스트가 곧 검색 대상이자 인용 근거가 된다.
메타필터용으로 마켓/종류/감성을 컬럼으로 승격, 원본 제품 객체는 attributes(jsonb)에.
"""

from __future__ import annotations

import json

from .config import settings
from .db import connect
from . import linking

_SENT_KO = {"pos": "좋음", "neu": "보통", "neg": "아쉬움"}

# 무거운 의존성(BGE-M3, kiwipiepy)은 첫 사용 시에만 로드한다.
_model = None
_kiwi = None


def _embedder():
    global _model
    if _model is None:
        from FlagEmbedding import BGEM3FlagModel
        _model = BGEM3FlagModel(settings.embedding_model, use_fp16=True)
    return _model


def embed(texts: list[str]):
    """BGE-M3 dense 임베딩(1024차원) 리스트. pgvector 가 numpy 배열을 그대로 적재."""
    return list(_embedder().encode(texts, batch_size=8, max_length=512)["dense_vecs"])


def _tokenize(text: str) -> list[str]:
    """kiwipiepy 형태소 토큰(BM25용). Postgres FTS 엔 한국어 토크나이저가 없어 앱단에서."""
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return [t.form for t in _kiwi.tokenize(text)]


def render_review(market: str | None, product: str | None, review: dict) -> str:
    """제품 항목 → 검색·인용용 한국어 렌더링. 있는 속성만 자연어 조각으로."""
    parts = [f"[{market or '마켓미상'} {product or '제품미상'}]"]

    def phrase(label: str, block: dict, desc: str) -> str:
        # 서술이 비면 evidence 스니펫으로 폴백(검색 recall↑). 공백 정리.
        desc = desc or (block.get("evidence") or "")
        return " ".join(f"{label}: {desc} {_SENT_KO.get(block.get('sentiment',''),'')}".split())

    if (s := review.get("scent")):
        parts.append(phrase("향", s, s.get("perceived") or ""))
    if (t := review.get("texture")):
        feel = ", ".join(t.get("feel") or []) or t.get("feel_other") or ""
        parts.append(phrase("질감", t, feel))
    if (sd := review.get("sound")):
        parts.append(phrase("소리", sd, sd.get("notes") or ""))
    if (lv := review.get("longevity")):
        parts.append(phrase("지속력", lv, lv.get("notes") or ""))
    if (v := review.get("value")) and (v.get("krw") or v.get("sentiment")):
        krw = f"{v['krw']}원" if v.get("krw") else ""
        parts.append(phrase("가격", v, krw))
    if (o := review.get("overall")) and o.get("summary"):
        parts.append(f"총평: {o['summary']}")
    return " / ".join(p for p in parts if p)


def _sent(review: dict, block: str) -> str | None:
    b = review.get(block)
    return b.get("sentiment") if b else None


def index_post(doc: dict, *, source: str, post_id: str | None = None,
               aliases: dict[str, str] | None = None, review_class: str = "genuine",
               relevance_meta: dict | None = None, conn=None) -> int:
    """
    추출 후기 1건(doc) → 제품별로 연결·렌더·임베딩 후 reviews 테이블에 적재.
    review_class='genuine'|'promo' — 홍보성 후기는 종합뷰에서 실사용과 분리 집계된다.
    relevance_meta — 관련성 게이트 판정(있으면). 소스 조각(post_id) 단위 속성이라
    제품별 팬아웃 행 전체에 그대로 복제된다.
    반환: 적재한 행 수.
    """
    kb = linking.load_kb()
    links = linking.link_post(doc, kb=kb, aliases=aliases)
    reviews = doc.get("reviews", [])
    # lk.product = 약칭 정규화된 제품명(aliases 적용분). raw mentioned_product 가 아니라
    # 이걸 써야 색인·렌더·조인이 KB 정규 제품명으로 통일된다.
    texts = [render_review(lk.market, lk.product, r)
             for r, lk in zip(reviews, links)]
    if not texts:
        return 0
    vecs = embed(texts)

    from psycopg.types.json import Jsonb
    rel_meta = Jsonb(relevance_meta) if relevance_meta else None
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            for r, lk, text, vec in zip(reviews, links, texts, vecs):
                cur.execute(
                    """
                    INSERT INTO reviews
                      (source, post_id, market, market_confidence, product,
                       slime_type, scent_sentiment, texture_sentiment,
                       sound_sentiment, overall_sentiment, review_class,
                       attributes, evidence, tokens, embedding, relevance_meta)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (source, post_id, lk.market, lk.market_confidence,
                     lk.product,
                     (r.get("texture") or {}).get("type_mentioned"),
                     _sent(r, "scent"), _sent(r, "texture"),
                     _sent(r, "sound"), (r.get("overall") or {}).get("model_sentiment"),
                     review_class,
                     Jsonb(r), text, _tokenize(text), vec, rel_meta),
                )
        conn.commit()
        return len(texts)
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    # 골드 1건을 색인(모델 다운로드 + Docker pgvector 필요).
    from .config import ROOT
    gold = json.loads((ROOT / "eval" / "layer2_gold.json").read_text(encoding="utf-8"))
    doc = gold["records"][0]["expected"]
    n = index_post(doc, source="amos", post_id=gold["records"][0]["id"])
    print(f"적재 행 수: {n}")
