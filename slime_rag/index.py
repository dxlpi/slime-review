# -*- coding: utf-8 -*-
"""
색인 (Phase 4) — 추출·연결된 후기 → BGE-M3 임베딩 → pgvector 적재.

청킹: 제품 항목 1개 = 1청크(1행).
임베딩 대상: 구조화 필드로 만든 '렌더링 텍스트'. 이 텍스트가 검색 대상이자 인용 근거다
         (원문 본문도 ADR-0013 이후 별도 컬럼에 보관하지만, 검색은 렌더링 텍스트로 한다
          — 추출된 항목 단위로 끊겨 있어 청크 경계가 명확하기 때문).
메타필터용으로 마켓/종류/감성을 컬럼으로 승격, 원본 제품 객체는 attributes(jsonb)에.
"""

from __future__ import annotations

import json
import logging
import re

from .config import settings
from .db import connect
from . import linking

log = logging.getLogger("index")

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
    if (sc := review.get("shipping_cs")):
        parts.append(phrase("배송·CS", sc, sc.get("notes") or ""))
    if (o := review.get("overall")) and o.get("summary"):
        parts.append(f"총평: {o['summary']}")
    return " / ".join(p for p in parts if p)


def _int(v) -> int | None:
    """'조회 428' · '댓글 4' · '1,234' · 1234 → 428 · 4 · 1234 · 1234. 숫자 없으면 None.

    ⚠️ 라벨을 함께 벗겨야 한다. 디시 `.gall_count`/`.gall_comment` 는 숫자만이 아니라
       **'조회 428' / '댓글 4'** 처럼 라벨이 붙은 텍스트를 준다(2026-08-06 실측). 반면
       `.up_num` 은 순수 숫자다 — 그래서 추천만 들어오고 조회·댓글이 통째로 NULL 이 되는
       조용한 결손이 났었다. 천 단위 쉼표도 함께 처리한다.
       파싱 실패를 0 으로 접지 않는다 — '0회 조회'와 '모름'은 다르다.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    m = re.search(r"-?\d[\d,]*", str(v))
    return int(m.group().replace(",", "")) if m else None


def _ts(v) -> str | None:
    """작성일 문자열 → psycopg 가 TIMESTAMPTZ 로 받을 수 있는 형태. 못 읽으면 None.

    디시 `.gall_date` 의 `title` 속성은 '2026-08-06 14:23:45', 인스타(Apify)는 ISO 8601 이다.
    텍스트 폴백('08.06', '14:23')은 연도가 없어 **버린다** — 틀린 날짜가 빈 날짜보다 나쁘다.
    """
    if not v:
        return None
    s = str(v).strip()
    from datetime import datetime
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y.%m.%d %H:%M:%S", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            pass
    try:                                   # ISO 8601(인스타). 'Z' 는 파이썬이 3.11+ 에서만 받는다.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def post_columns(raw) -> dict:
    """RawReview → `reviews` 의 원문·작성 메타 컬럼(ADR-0013). 순수 함수(무DB·무네트워크).

    수집기는 **원래부터** 이 값들을 들고 있었다. 예전 규칙이 색인 단계에서 버렸을 뿐이라
    수집기는 손댈 필요가 없고, 여기서 플랫폼별 키 이름만 하나로 모은다.

    ⚠️ 여기 담기는 `body` 는 **원문 전문**이다. 자르는 건 이 층의 일이 아니다 —
       표시 직전(`pipeline.list_reviews`)에서 자른다(ADR-0013 §3). 저장은 전문, 표시는 발췌.
    """
    meta = getattr(raw, "meta", None) or {}
    return {
        "body": getattr(raw, "text", None),
        "title": getattr(raw, "raw_title", None) or meta.get("parent_title"),
        # 디시=nick(익명이면 'ㅇㅇ') · 인스타=owner_username. 표시 여부는 화면이 정한다.
        "author": meta.get("nick") or meta.get("owner_username"),
        "posted_at": _ts(getattr(raw, "posted_at", None)),
        "likes": _int(meta.get("likes")),
        "views": _int(meta.get("views")),
        "comment_count": _int(meta.get("comment_count") or meta.get("comments")),
        "votes_up": _int(meta.get("recommend_up")),
        "votes_down": _int(meta.get("recommend_down")),
    }


_POST_COLS = ("body", "title", "author", "posted_at",
              "likes", "views", "comment_count", "votes_up", "votes_down")


def existing_post_ids(conn, source: str, post_ids: list[str]) -> set[str]:
    """이미 색인된 조각 식별자만 골라 돌려준다. **추출 전 컷 전용** — 한 번의 왕복.

    색인 키를 소유한 모듈이 그 키의 조회도 소유한다. 호출부가 `reviews` 를 직접 SELECT 하면
    `post_id` 조립 규칙이 두 곳으로 갈리고, 그때 컷은 **조용히 아무것도 안 걸러진다**
    (틀린 키로 조회하면 결과가 빈 집합이라 '전부 처음 보는 조각'처럼 보인다).

    ⚠️ 이 판정은 `reviews` 에 **행이 남은** 조각만 잡는다. 추출 결과가 `reviews: []` 인 조각은
      행을 안 남기므로 다음 런에 다시 유료 추출된다 — 구조적 구멍이고, 근본 해결은 '봤지만 안
      남긴' 조각의 툼스톤 원장이다(계획 §8 에서 의도적으로 미해결).
    """
    if not post_ids:
        return set()                          # 빈 목록에 ANY(ARRAY[]) 를 던지지 않는다
    rows = conn.execute(
        "SELECT DISTINCT post_id FROM reviews WHERE source=%s AND post_id = ANY(%s)",
        (source, list(post_ids))).fetchall()
    return {r[0] for r in rows}


def _sent(review: dict, block: str) -> str | None:
    b = review.get(block)
    return b.get("sentiment") if b else None


def index_post(doc: dict, *, source: str, post_id: str | None = None,
               aliases: dict[str, str] | None = None, review_class: str = "genuine",
               relevance_meta: dict | None = None, source_ref: dict | None = None,
               raw=None, conn=None) -> int:
    """
    추출 후기 1건(doc) → 제품별로 연결·렌더·임베딩 후 reviews 테이블에 적재.
    review_class='genuine'|'promo' — 홍보성 후기는 종합뷰에서 실사용과 분리 집계된다.
    relevance_meta — 관련성 게이트 판정(있으면). 소스 조각(post_id) 단위 속성이라
    제품별 팬아웃 행 전체에 그대로 복제된다.
    source_ref — 원문 조각 식별자(`slime_rag.source_links` 참조). relevance_meta 와 같은
    규칙으로 팬아웃 행 전체에 복제되지만 **소비 방식이 다르다**: relevance_meta 는 행 단위
    집계 입력이고, 이건 식별자라 읽는 쪽에서 중복 제거가 필요하다
    (`source_links.evidence_group_key`). 안 하면 한 조각이 제품 수만큼 링크로 도배된다.
    raw — 원본 RawReview(선택). 주면 원문 본문·작성 메타를 함께 적재한다(ADR-0013).
    안 주면 해당 컬럼은 NULL — 골드 시드처럼 RawReview 가 없는 경로가 그렇다.
    반환: **실제로 적재된** 행 수(중복 스킵분 제외).

    멱등성은 `UNIQUE (source, post_id, product)` + `ON CONFLICT DO NOTHING` 이 **DB에서**
    보장한다. 예전엔 파이프라인 독스트링만 '멱등'이라 주장하고 강제하는 장치가 없어서,
    수집 배치를 두 번 돌린 결과 인스타 80행 중 28행이 중복이었다(2026-08-07 정리).
    같은 글이 두 런에서 **다른 감성**으로 추출돼(LLM 비결정성) 독립된 후기 두 건처럼 쌓였고,
    그게 `criterion_stats` 의 다수/소수 판정 재료로 들어갔다.
    ⚠️ `DO UPDATE` 로 바꾸지 말 것 — 재수집마다 이미 내려진 판정을 조용히 덮어쓴다.
      원문 메타(likes·posted_at 등) 백필이 필요하면 무엇을 덮는지 명시하는 별도 함수로 할 일이지,
      수집 경로가 겸할 일이 아니다.
    ⚠️ `post_id` 가 None 이면 Postgres 가 NULL 을 서로 다른 값으로 취급해 이 제약을 빠져나간다.
      호출부가 조각 식별자를 항상 넘기는 게 전제다.
    """
    kb = linking.load_kb()
    links = linking.link_post(doc, kb=kb, aliases=aliases)
    reviews = doc.get("reviews", [])
    # shipping_cs 는 후기(주문) 단위 사실이라 doc 최상위에 산다(ADR-0005) — 제품 항목엔 없다.
    # 그런데 attributes 에 들어가는 건 제품 항목뿐이라, 복제하지 않으면 종합뷰의
    # ATTR_FIELDS['shipping_cs'] 가 행에서 그 키를 영영 못 찾아 배송·CS 섹션이 상시 빈칸이 된다.
    # relevance_meta 와 같은 규칙: 조각 단위 속성은 제품별 팬아웃 행 전체에 그대로 복제한다.
    if (ship := doc.get("shipping_cs")):
        reviews = [{**r, "shipping_cs": ship} for r in reviews]
    # lk.product = 약칭 정규화된 제품명(aliases 적용분). raw mentioned_product 가 아니라
    # 이걸 써야 색인·렌더·조인이 KB 정규 제품명으로 통일된다.
    texts = [render_review(lk.market, lk.product, r)
             for r, lk in zip(reviews, links)]
    if not texts:
        return 0
    vecs = embed(texts)

    from psycopg.types.json import Jsonb
    rel_meta = Jsonb(relevance_meta) if relevance_meta else None
    src_ref = Jsonb(source_ref) if source_ref else None
    # 조각 단위 속성이라 relevance_meta·source_ref 와 같은 규칙으로 팬아웃 행 전체에 복제된다.
    post_cols = post_columns(raw) if raw is not None else {c: None for c in _POST_COLS}
    post_vals = tuple(post_cols[c] for c in _POST_COLS)
    own = conn is None
    conn = conn or connect()
    try:
        n_ins = 0
        with conn.cursor() as cur:
            for r, lk, text, vec in zip(reviews, links, texts, vecs):
                cur.execute(
                    """
                    INSERT INTO reviews
                      (source, post_id, market, market_confidence, product,
                       slime_type, scent_sentiment, texture_sentiment,
                       sound_sentiment, overall_sentiment, review_class,
                       attributes, evidence, tokens, embedding, relevance_meta, source_ref,
                       body, title, author, posted_at,
                       likes, views, comment_count, votes_up, votes_down)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source, post_id, product) DO NOTHING
                    """,
                    (source, post_id, lk.market, lk.market_confidence,
                     lk.product,
                     (r.get("texture") or {}).get("type_mentioned"),
                     _sent(r, "scent"), _sent(r, "texture"),
                     _sent(r, "sound"), (r.get("overall") or {}).get("model_sentiment"),
                     review_class,
                     Jsonb(r), text, _tokenize(text), vec, rel_meta, src_ref,
                     *post_vals),
                )
                n_ins += cur.rowcount        # 충돌로 스킵되면 0 — 세어서 드러낸다
        conn.commit()
        if n_ins < len(texts):
            log.info("색인 중복 스킵 %d/%d행 (post_id=%s)", len(texts) - n_ins, len(texts), post_id)
        return n_ins
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
