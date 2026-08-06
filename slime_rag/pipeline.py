# -*- coding: utf-8 -*-
"""
파이프라인 오케스트레이터 (Phase 4~6 글루) — 샘플로 end-to-end 를 한 번에.

흐름: 스키마 적용 → 1층 specs 적재(fixture/시드 KB) → 2층 후기 색인(골드)
      → specs↔reviews 조인(spec_id) → 검색·근거답변 → 종합뷰+편향집계.

설계 메모:
- 1층(specs)은 KB market_word 를 키로 적재 → linking 이 정규화한 reviews.market 과 바로 조인.
- 멱등: specs 는 UNIQUE(market,product) upsert, reviews 는 post_id 존재 시 색인 스킵.
- 소스→플랫폼 매핑(amos→dcinside)은 여기 한 곳에서. 종합뷰는 플랫폼 라벨로 편향을 본다.
- 표시 계층은 이 모듈의 list_*/consolidated_for/answer 만 호출(DB SQL 캡슐화).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

from .config import settings, ROOT
from .db import connect, apply_schema
from . import layer1, index, linking, search, source_links
from . import consolidated_view as cv
from .llm_ops import LLM, summary

log = logging.getLogger("pipeline")

# 수집 소스 → 편향 집계용 플랫폼 키. 종합뷰/요약이 이 라벨로 소스를 가른다.
SOURCE_PLATFORM = {"amos": "dcinside", "instagram": "instagram"}


# ---------------------------------------------------------------- 1층 적재
@lru_cache(maxsize=None)
def _tag_exclusions(market_word: str) -> frozenset[str]:
    """마켓 표시어 → 제품명 후보에서 뺄 자기이름 집합(KB 조회, 프로세스 캐시).

    `linking.load_kb()` 는 호출마다 KB 파일을 다시 파싱한다 — 판매자 게시물 루프 안에서 부르면
    게시물 수만큼 재파싱된다. 마켓당 한 번만 계산하면 되는 값이라 여기서 캐시한다.
    """
    from . import extract                    # 모듈 최상위 import 아님(다른 함수들과 동일 규칙)
    kb_market = next((m for m in linking.load_kb().markets
                      if market_word in (m.get("market_word"), m.get("market"))), None)
    return frozenset(extract.market_tag_exclusions(kb_market) if kb_market else {market_word})


def _upsert_spec(cur, market, product, scent, base_combo, stype, official_texture=None,
                 beads=None, source_permalink=None) -> None:
    """specs (market,product) upsert — fixture 시드와 판매자 자동추출이 공유하는 단일 경로.

    official_texture: 판매자가 캡션에 쓴 질감 서술의 요약(없으면 None). slime_type 과 별개 칸이다 —
    종류어('폼볼')는 분류지 질감 설명이 아니라서, 이게 없으면 화면의 '질감' 줄이 분류코드만 보여준다.
    beads: 비즈/토핑 구성요소 리스트(오픈 어휘). None/빈 → []. 제품행에 붙는 부가 메타이며,
    비즈 단독으로는 제품이 되지 않는다(호출부 백스톱이 scent/base_combo/slime_type 로 제품성 판정).
    source_permalink: 공식 스펙 출처 인스타 게시물 URL(없으면 None). COALESCE 로 기존 값 보존 —
    나중 upsert 가 URL 을 안 넘겨도(None) 이미 저장된 URL 을 지우지 않는다.
    """
    cur.execute(
        """
        INSERT INTO specs (market, product, scent, base_combo, slime_type,
                           official_texture, beads, source_permalink)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (market, product) DO UPDATE SET
          scent=EXCLUDED.scent, base_combo=EXCLUDED.base_combo,
          slime_type=EXCLUDED.slime_type, beads=EXCLUDED.beads,
          official_texture=COALESCE(EXCLUDED.official_texture, specs.official_texture),
          source_permalink=COALESCE(EXCLUDED.source_permalink, specs.source_permalink)
        """,
        (market, product, scent, base_combo, stype, official_texture,
         list(beads or []), source_permalink),
    )


def load_specs(conn, kb: dict | None = None) -> int:
    """fixture 로 시드한 KB products → specs 테이블 upsert. 반환: 적재 행 수."""
    if kb is None:
        kb = json.loads(settings.kb_demo_path.read_text(encoding="utf-8"))
    layer1.seed_kb_products(kb["markets"])           # in-place: products[] 채움
    rows = list(layer1.iter_specs(kb["markets"]))
    with conn.cursor() as cur:
        for market, product, scent, base_combo, stype, texture, beads, permalink in rows:
            _upsert_spec(cur, market, product, scent, base_combo, stype, texture,
                         beads, permalink)
    conn.commit()
    log.info("specs 적재 %d행", len(rows))
    return len(rows)


def join_specs(conn) -> int:
    """reviews.market+product 가 specs 와 일치하면 spec_id 연결. 반환: 연결된 행 수."""
    cur = conn.execute(
        """
        UPDATE reviews r SET spec_id = s.id
        FROM specs s
        WHERE r.spec_id IS NULL AND r.market = s.market AND r.product = s.product
        """
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------- 해시태그 인제스트(2층 라이브 글루)
def ingest_hashtag(keywords: list[str], *, limit: int = 30) -> dict:
    """
    인스타 해시태그 라이브 수집 → 편향 분리 → 판매자는 1층 specs 로, 실사용/홍보성은 2층 색인.

    흐름(계획 §3 Step 6):
      1) ApifyHashtagSource.collect(keywords) → raws
      2) bias.partition(raws, kb) → (sellers, users)  — 판매자 우선, 나머지는 review_class 태깅
      3) sellers → extract_spec → specs upsert(market=핸들매핑, 멱등)
      4) users  → extract_review → index_post(review_class=genuine|promo)
      5) join_specs
    반환: 관측성 카운트(수집/판매자→스펙/홍보성/실사용/조인).

    APIFY_TOKEN 없으면 수집 0 → 모든 카운트 0(회복력). 색인은 DB(compose 55432)+BGE-M3 필요.
    """
    from .sources import ApifyHashtagSource
    from . import bias, extract

    kb = json.loads(settings.kb_demo_path.read_text(encoding="utf-8"))
    src = ApifyHashtagSource(token=settings.apify_token,
                             results_per_hashtag=settings.apify_results_per_hashtag)
    raws = list(src.collect(keywords, limit=limit))
    llm = LLM()
    # 홍보성 판정은 게이트(recall)→LLM(verdict) 캐스케이드. 값싼 게이트가 '홍보 의심'만 통과시키고,
    # 명백한 실사용은 즉시 genuine 단락(LLM 미호출) → 호출 수를 크게 줄인다. precision 은 LLM 몫.
    gate_terms = bias.load_gate_terms()
    promo_detector = bias.make_gated_llm_promo_detector(llm, settings.model_extract, terms=gate_terms)
    sellers, users = bias.partition(raws, kb, promo_detector=promo_detector)
    # 관측성: 게이트 통계는 판매자 제외(non-seller) 대상. 게이트통과=LLM호출, 단락=절감.
    n_suspect = sum(1 for u in users if bias.promo_gate(u.text, gate_terms))
    n_saved = len(users) - n_suspect
    log.info("해시태그 수집 %d건 → 판매자 %d, 유저후기 %d "
             "(게이트통과 %d / genuine단락 %d / 절감 LLM호출 %d)",
             len(raws), len(sellers), len(users), n_suspect, n_saved, n_saved)
    n_spec = n_skip_nohash = 0
    with connect() as conn:
        with conn.cursor() as cur:
            for sp in sellers:                       # 판매자 캡션 → 1층 공식 스펙
                market = sp.meta.get("seller_market")
                # 결정적 게이트: 해시태그가 하나도 없으면 비매품/공지글 → 스킵(사용자 규칙).
                # LLM 판정에 맡기지 않는다 — 비매품 설명(예: '8mm디폼')을 제품으로 오추출하기 때문.
                # 제품명 후보는 **마켓 자기이름·광역어를 뺀** 태그다(2026-08-06 사용자 규칙).
                # 안 빼면 `#슬라임지나` 같은 마켓 태그가 통과해 마켓마다 유령 제품 행이 생긴다.
                excl = _tag_exclusions(market)
                post_tags = set(extract.product_hashtags(sp.text, exclude=excl))
                if not post_tags:
                    n_skip_nohash += 1
                    continue
                spec = extract.extract_spec(sp.text, llm, settings.model_extract)
                for p in spec.get("products", []):
                    if not (market and p.get("product")):
                        continue                     # 제품명 없으면 스펙 행 못 만듦 → 스킵
                    # 결정적 제품 게이트: 제품명은 반드시 그 캡션의 해시태그여야 한다(사용자 규칙:
                    # 제품=제품 고유 해시태그). LLM 이 향료·재료어(에그노그 등)를 유령 제품으로
                    # 지어내도 #태그가 없으면 드롭 → run 간 비결정 유령 제거.
                    if p["product"].strip() not in post_tags:
                        continue
                    if not any(p.get(k) for k in ("scent", "base_combo", "slime_type")):
                        continue                     # 스펙 필드 전부 null → 비즈·토핑 노이즈, 제품 아님
                    # beads·official_texture 는 제품성 판정에 넣지 않는다(각각 구성요소·서술일 뿐,
                    # 스펙 세 칸이 전부 비었는데 질감 얘기만 있는 글은 제품 안내가 아니다)
                    # → 위 백스톱 통과분에만 부가.
                    _upsert_spec(cur, market, p["product"], p.get("scent"),
                                 p.get("base_combo"), p.get("slime_type"),
                                 p.get("official_texture"), p.get("beads"),
                                 sp.url)                # 공식 스펙 출처 = 판매자 게시물 URL
                    n_spec += 1
        conn.commit()
    if n_skip_nohash:
        log.info("판매자 글 중 제품 해시태그 없음 %d건 스킵(비매품/공지)", n_skip_nohash)

    n_genuine = n_promo = 0
    n_ref = n_noref = 0                              # 관측성: 원문 링크 식별자 유/무 행수
    for u in users:                                  # 실사용/홍보성 → 2층 색인
        rc = u.meta.get("review_class", "genuine")
        doc = extract.extract_review(u.text, llm, settings.model_extract)
        ref = source_links.build_source_ref("instagram", u.url, u.meta)
        rows = index.index_post(doc, source="instagram",
                                post_id=u.meta.get("shortcode"), review_class=rc,
                                relevance_meta=u.meta.get("relevance"), source_ref=ref,
                                raw=u)                 # 원문 본문·작성 메타 동반 적재(ADR-0013)
        if ref:
            n_ref += rows
        else:
            n_noref += rows
        if rc == "promo":
            n_promo += 1
        else:
            n_genuine += 1

    with connect() as conn:
        n_join = join_specs(conn)
    counts = {"collected": len(raws), "seller_specs": n_spec,
              "seller_no_hashtag": n_skip_nohash,
              "promo": n_promo, "genuine": n_genuine, "joined_now": n_join,
              "gate_suspect": n_suspect, "llm_calls_saved": n_saved,
              "rows_with_source_ref": n_ref, "rows_without_source_ref": n_noref}
    log.info("ingest_hashtag 완료: %s", counts)
    return counts


# ---------------------------------------------------------------- 2층 색인(디시 실수집)
def ingest_dcinside(slime: str, market: str | None = None, aliases: list[str] | None = None,
                    limit: int = 30, comment_pages: int = 1, dry_run: bool = False) -> dict:
    """
    디시 실수집 → 관련성 게이트 → **스레드 배치 추출** → 색인 (계획 C-4).

    `extract_collected` 은 진작 있었지만 파이프라인에 연결돼 있지 않았다(§1-G) — 인스타 경로만
    `ingest_hashtag` 로 이어져 있었다. 지금 연결하는 이유는 지금이 **추출 단위를 정하기 가장 싼
    시점**이기 때문이다. 나중에 per-comment 로 굳은 뒤 뜯는 것보다 배치 단위로 처음부터 잇는 게 싸다.

    dry_run=True 면 LLM·DB 를 건드리지 않고 수집·게이트까지만 돌려 카운트를 돌려준다(키 없이 점검용).
    반환: 카운트 요약.
    """
    from . import extract
    from .sources import DCInsideSource, collect_all, expand_queries

    src = DCInsideSource(gallery_id="amos", comment_pages=comment_pages)
    queries = expand_queries(slime, aliases=aliases or [], market_word=market)
    target = {"market": market, "slime": slime}
    raws = collect_all([src], keywords=queries, per_source_limit=limit, target=target)
    n_post = sum(1 for r in raws if r.meta.get("type") == "post")
    counts = {"collected": len(raws), "posts": n_post, "comments": len(raws) - n_post,
              "queries": queries}
    if dry_run or not raws:
        counts["dry_run"] = True
        log.info("ingest_dcinside(dry) 완료: %s", counts)
        return counts

    llm = LLM()
    pairs = extract.extract_collected(raws, llm, settings.model_extract)
    n_rows = n_ref = n_noref = 0                      # 관측성: 원문 링크 식별자 유/무 행수
    with connect() as conn:
        for i, (raw, doc) in enumerate(pairs):
            # post_id 는 조각별로 달라야 한다 — 스레드 배치라도 귀속은 조각 단위(AC12).
            # 댓글 URL 은 스레드 안에서 전부 `…#cmt` 로 같으므로(수집기가 앵커를 안 붙인다)
            # 순번을 넣지 않으면 같은 스레드 댓글들이 한 post_id 로 뭉개진다.
            post_id = raw.url if raw.meta.get("type") == "post" else \
                f"{raw.url}:{raw.meta.get('parent_no')}:{i}"
            # 링크용 식별자는 post_id 와 별개다 — post_id 가 담는 건 댓글 id 가 아니라 런 전체의
            # enumerate 위치라 원문 주소로 되돌릴 수 없다(그래서 별도 컬럼, ADR-0009).
            ref = source_links.build_source_ref("dcinside", raw.url, raw.meta)
            rows = index.index_post(doc, source="amos", post_id=post_id, conn=conn,
                                    relevance_meta=raw.meta.get("relevance"), source_ref=ref,
                                    raw=raw)           # 원문 본문·작성 메타 동반 적재(ADR-0013)
            n_rows += rows
            if ref:
                n_ref += rows
            else:
                n_noref += rows
        conn.commit()
    counts["indexed_rows"] = n_rows
    counts["rows_with_source_ref"] = n_ref
    counts["rows_without_source_ref"] = n_noref
    counts["llm"] = {k: summary()[k] for k in ("calls", "input_tokens", "cached_tokens")}
    log.info("ingest_dcinside 완료: %s", counts)
    return counts


# ---------------------------------------------------------------- 2층 색인(골드)
def index_gold(conn) -> int:
    """eval/layer2_gold.json 의 후기들을 색인(멱등: 같은 post_id 있으면 스킵).

    골드 레코드의 `source.url` 이 있으면 원문 링크 식별자로 넘긴다(없으면 링크 없이 색인).
    ⚠️ 이미 색인된 행은 스킵되므로 `source.url` 을 나중에 채워도 `setup(reset=False)` 로는
    반영되지 않는다 — 데모 DB 는 `setup(reset=True)` 로 재적재한다(ADR-0009 백필 정책).
    """
    gold = json.loads((ROOT / "eval" / "layer2_gold.json").read_text(encoding="utf-8"))
    n = 0
    for rec in gold["records"]:
        pid = rec["id"]
        if conn.execute("SELECT 1 FROM reviews WHERE post_id=%s LIMIT 1", [pid]).fetchone():
            log.info("색인 스킵(이미 있음): %s", pid)
            continue
        ref = source_links.build_source_ref("dcinside", (rec.get("source") or {}).get("url"))
        n += index.index_post(rec["expected"], source="amos", post_id=pid, conn=conn,
                              source_ref=ref)
    return n


# ---------------------------------------------------------------- 셋업(전체)
def setup(reset: bool = False) -> dict:
    """스키마→1층→2층→조인 일괄. UI/데모 진입점. 반환: 카운트 요약."""
    apply_schema()
    with connect() as conn:
        if reset:
            conn.execute("TRUNCATE reviews, specs RESTART IDENTITY CASCADE")
            conn.commit()
        n_specs = load_specs(conn)
        n_rev = index_gold(conn)
        n_join = join_specs(conn)
        counts = {
            "specs": conn.execute("SELECT count(*) FROM specs").fetchone()[0],
            "reviews": conn.execute("SELECT count(*) FROM reviews").fetchone()[0],
            "indexed_now": n_rev, "joined_now": n_join, "specs_loaded": n_specs,
        }
    log.info("setup 완료: %s", counts)
    return counts


# ---------------------------------------------------------------- UI 데이터 접근
def list_markets() -> list[str]:
    """specs 에 1층이 있는 마켓(정규 market_word) 목록."""
    with connect() as conn:
        rows = conn.execute("SELECT DISTINCT market FROM specs ORDER BY market").fetchall()
    return [r[0] for r in rows]


_KB_CACHE: linking.KB | None = None


def market_logo(market_word: str | None) -> dict:
    """정규 market_word → 그릴 로고 자산(ADR-0010). **DB 미접촉** — KB JSON 만 읽는다.

    UI 가 `pipeline` 만 알면 되도록 여기 둔다(백엔드 글루 캡슐화 규칙). KB 는 리런마다
    다시 파싱할 이유가 없어 프로세스 캐시 — 재실행형 프런트엔드는 요청마다 스크립트를 통째로
    재실행하므로 캐시가 없으면 매 상호작용에 13마켓 JSON 을 다시 읽는다.

    판단은 전부 `source_links.logo_asset` 이 한다(순수·CI 게이트 대상). 여기는 조회만.
    """
    global _KB_CACHE
    if _KB_CACHE is None:
        _KB_CACHE = linking.load_kb()
    return source_links.logo_asset(_KB_CACHE.market_by_word(market_word))


def list_products(market: str) -> list[dict]:
    """해당 마켓의 1층 제품 스펙 리스트."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT product, scent, base_combo, slime_type, official_texture, beads, "
            "source_permalink FROM specs WHERE market=%s ORDER BY product", [market]).fetchall()
    return [{"product": p, "official_scent": s, "base_combo": b, "slime_type": t,
             "official_texture": tx, "beads": list(beads or []), "source_permalink": url}
            for p, s, b, t, tx, beads, url in rows]


def _records_for(conn, market: str, product: str | None) -> list[dict]:
    """DB 후기 → 종합뷰 입력 레코드(소스→platform 주입).

    product=None 이면 마켓 전체(제품 연결 보류 행 포함) — 마켓 단위 종합뷰 입력.
    product_ref 는 행의 정규화 product 를 담는다(마켓 모드 요약이 제품 라벨로 씀).
    source_ref 는 원문 링크 식별자 — 요약 프롬프트가 아니라 근거 목록 표시에만 쓰인다.

    ⚠️ 안전 속성(깨뜨리지 말 것): `consolidated_view._source_material` 이 `ATTR_FIELDS`/`_SALIENT`
    키만 통과시키는 화이트리스트라, 여기서 rec 에 무엇을 더 넣든 **섹션 요약 프롬프트엔 닿지
    않는다**. `source_ref`(URL·id)가 LLM 입력으로 새지 않는 근거가 이 화이트리스트 하나다 —
    payload 를 '남는 키 전부 통과'로 넓히는 순간 이 보장이 사라진다.
    """
    sql = ("SELECT source, product, attributes, review_class, source_ref "
           "FROM reviews WHERE market=%s")
    params: list = [market]
    if product:
        sql += " AND product=%s"
        params.append(product)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for src, prod, attrs, review_class, source_ref in rows:
        rec = dict(attrs)                       # attributes = 추출 후기 항목 원본
        rec["source"] = {"platform": SOURCE_PLATFORM.get(src, src)}
        rec["review_class"] = review_class      # genuine/promo → 종합뷰가 분리 집계
        rec["product_ref"] = {"market": market, "product": prod}
        rec["source_ref"] = source_ref          # 원문 링크 식별자(조각 단위, 팬아웃 복제됨)
        out.append(rec)
    return out


# 커뮤니티 리뷰 패널 정렬 — **DB 에 있는 컬럼으로만** 만든다.
# 디자인의 좋아요/조회/추천순은 수집기(RawReview.meta)엔 있지만 `reviews` 테이블에 없다.
# 없는 축을 메뉴에 띄우면 정렬을 누른 사용자에게 거짓말이 되므로 넣지 않는다 —
# 컬럼이 생기면 여기 dict 에 한 줄 추가하는 것으로 켜진다.
# ⚠️ '최근 수집순'은 작성일이 아니라 **수집일**(reviews.created_at) 기준이다. 원문 작성일은
# 수집기(RawReview.posted_at)엔 있지만 테이블에 없다 — 없는 걸 '최신순'이라 부르면 거짓말이라
# 이름·화면 라벨 양쪽에 '수집'을 남긴다.
REVIEW_SORTS: dict[str, str] = {
    "최근 수집순": "created_at DESC NULLS LAST, id DESC",
    "긍정 먼저":   "CASE overall_sentiment WHEN 'pos' THEN 0 WHEN 'neu' THEN 1 ELSE 2 END, id DESC",
    "부정 먼저":   "CASE overall_sentiment WHEN 'neg' THEN 0 WHEN 'neu' THEN 1 ELSE 2 END, id DESC",
}

# 소스별 표시 라벨(디자인 카피). 플랫폼 키 → 패널 제목.
PLATFORM_LABELS = {"instagram": "인스타그램", "dcinside": "디시인사이드 아모스 갤러리"}

# `reviews.evidence` 는 `index.render_review` 산출물이라 '[마켓 제품] / 향: … / 질감: …' 꼴이다.
# 앞의 [마켓 제품] 은 BM25 용 앵커라 카드에선 제품 라벨과 겹친다 — 표시할 때만 떼어낸다.
_EVIDENCE_ANCHOR = re.compile(r"^\s*\[[^\]]*\]\s*/\s*")


def _display_evidence(evidence: str | None) -> str | None:
    """근거 스니펫을 카드용으로 다듬는다 — 색인 앵커만 제거하고 내용은 그대로 둔다."""
    if not evidence:
        return None
    return _EVIDENCE_ANCHOR.sub("", evidence).strip() or None


def list_reviews(market: str | None = None, product: str | None = None, *,
                 platform: str | None = None,
                 sort: str = "최근 수집순", limit: int = 30) -> list[dict]:
    """커뮤니티 리뷰 패널용 개별 후기 목록 — 실사용(genuine)만, 조각 단위 중복 제거.

    반환 항목: {platform, market, product, evidence, sentiment, url, is_comment, collected_at,
                body, title, author, posted_at, likes, views, comment_count, votes}.
    `body` 는 **서버에서 자른 발췌**다(`source_links.excerpt`, ADR-0013 §3) — 전문이 아니다.

    **market 은 선택이다.** `market=None, product="빠코볼"` 이면 마켓을 묻지 않고 제품명으로만
    조회한다. 실측 근거(2026-08-06, 아모스갤 '빠코볼' 25건 수집): 원문 17조각 중 **10개가
    마켓을 아예 언급하지 않았고**, 등장한 `ㅈㄴ` 6건도 대부분 마켓(슬라임지나)이 아니라 부사
    '존나'였다. 갤러리 이용자는 제품명만 쓰고 마켓을 생략한다. 그래서 개체연결이 (정상적으로)
    보류하면 `market` 이 NULL 로 남고, 마켓 필수 조회로는 **후기가 있어도 화면에 0건**이 된다.

    ⚠️ 대가: 다른 마켓에 **같은 이름의 제품**이 있으면 섞인다. 그래서 각 항목에 그 행의
       `market`(보류면 None)을 함께 실어 보낸다 — 호출자가 라벨을 붙이거나 걸러낼 수 있게.
       근본 해결은 KB `products` 에 제품→마켓을 등록해 개체연결이 마켓을 붙이는 것이다
       (지금 13개 마켓 전부 `products: []`).

    ⚠️ `evidence` 는 **원문이 아니라 근거 스니펫**(~15자)이다.

    📌 ADR-0013 이후 이 자리는 **원문 본문의 서버 발췌**로 바뀐다 — 아직 아니다.
       `reviews` 에 본문 컬럼이 없어서(규칙이 스키마에서 버렸다) 재수집·backfill 이 선행이다.
       바뀔 때 자르는 코드는 **반드시 여기** 있어야 한다: 전문을 반환하고 프런트에서
       `line-clamp` 로 접으면 전문이 이미 브라우저에 도달한 것이라 발췌가 아니다.
       공개 전환 시 길이를 줄이는 스위치도 같은 자리에 둔다(ADR-0013 §5).

    ⚠️ `source_ref` 는 조각 단위 속성이라 제품별 팬아웃 행마다 복제돼 있다. 그대로 그리면
    한 조각이 제품 수만큼 카드로 도배되므로 `source_links.evidence_group_key` 로 접는다
    (근거 목록이 쓰는 것과 같은 키 — 링크를 세는 규칙이 화면마다 갈리면 안 된다).
    """
    if not market and not product:
        # 둘 다 비면 조건 없는 전량 조회가 된다 — 화면이 실수로 테이블을 통째로 긁는 걸 막는다.
        raise ValueError("list_reviews: market 과 product 중 최소 하나는 필요하다")
    order = REVIEW_SORTS.get(sort) or REVIEW_SORTS["최근 수집순"]
    # ⚠️ 모르는 platform 은 **예외**다. 예전엔 조용히 None 으로 떨어져 필터가 통째로 꺼졌고,
    #    그러면 '아모스갤만' 을 요청한 화면에 인스타 후기가 섞여 나온다 — 소스 미평균(1급 규칙)이
    #    조용히 깨지는 경로다. 디자인 탭 라벨이 '아모스갤'이라 오타가 아니어도 밟기 쉽다.
    src = None
    if platform:
        src = {v: k for k, v in SOURCE_PLATFORM.items()}.get(platform)
        if src is None:
            raise ValueError(
                f"list_reviews: 알 수 없는 platform {platform!r} "
                f"(가능: {sorted(SOURCE_PLATFORM.values())}). 필터를 끄려면 platform=None.")
    sql = ("SELECT source, market, product, evidence, overall_sentiment, source_ref, created_at, id, "
           "body, title, author, posted_at, likes, views, comment_count, votes_up "
           "FROM reviews WHERE review_class='genuine'")
    params: list = []
    if market:
        sql += " AND market=%s"
        params.append(market)
    if product:
        sql += " AND product=%s"
        params.append(product)
    if src:
        sql += " AND source=%s"
        params.append(src)
    sql += f" ORDER BY {order}"                 # order 는 위 화이트리스트 값만 (사용자 입력 아님)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    out, seen = [], set()
    for (source, mkt, prod, evidence, sentiment, source_ref, created_at, _id,
         body, title, author, posted_at, likes, views, n_comment, votes) in rows:
        key = source_links.evidence_group_key(source_ref)
        # 링크 식별자가 없는 행은 접을 키도 없다 — 행 자체를 키로 써서 최소한 자기끼리는 안 겹치게.
        key = key or ("_norow", _id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "platform": SOURCE_PLATFORM.get(source, source),
            "market": mkt,                          # 개체연결 보류면 None — 호출자가 라벨링/필터
            "product": prod,
            "evidence": _display_evidence(evidence),
            "sentiment": sentiment,
            "url": source_links.permalink(source_ref),
            "is_comment": source_links.is_comment(source_ref),
            "collected_at": created_at.strftime("%Y.%m.%d") if created_at else None,
            # ADR-0013 §3: 브라우저로 나가는 본문은 **여기서 자른 발췌**가 전부다.
            "body": source_links.excerpt(body),
            "title": title,
            "author": author,
            "posted_at": posted_at.strftime("%Y.%m.%d") if posted_at else None,
            "likes": likes, "views": views, "comment_count": n_comment, "votes": votes,
        })
        if len(out) >= limit:
            break
    return out


def consolidated_for(market: str, product: str, *, with_summary: bool = True) -> dict:
    """1층 스펙 + 2층 후기 → 종합 뷰(소스별·갭·향불일치 + 인스타/디시/통합 리뷰 요약)."""
    with connect() as conn:
        spec_row = conn.execute(
            "SELECT product, scent, base_combo, slime_type, official_texture, beads, "
            "source_permalink FROM specs WHERE market=%s AND product=%s",
            [market, product]).fetchone()
        records = _records_for(conn, market, product)
    official_spec = None
    if spec_row:
        official_spec = {"product": spec_row[0], "official_scent": spec_row[1],
                         "base_combo": spec_row[2], "slime_type": spec_row[3],
                         "official_texture": spec_row[4], "beads": list(spec_row[5] or []),
                         "source_permalink": spec_row[6]}
    sectionize = None
    if with_summary and records:
        # 소스별(인스타/디시/통합/서포터) 향/질감/장단점 구조화 요약(structured outputs).
        sectionize = lambda prompt, schema: LLM().complete(
            prompt, model=settings.model_judge, schema=schema, label="consolidated.section")
    return cv.build_consolidated({"market": market, "product": product},
                                 official_spec, records, llm_sectionize=sectionize)


# ---------------------------------------------------------------- 요약 생성·저장(미리 만들어 두기)
def generate_summaries(market: str, product: str) -> dict:
    """리뷰 요약을 **한 번 생성해 DB 에 저장**한다. 반환: 저장한 payload.

    왜 저장하나(2026-08-06 사용자 결정): 화면이 열릴 때마다 LLM 을 부르면 방문마다 과금된다.
    발표용 데모라 요약은 미리 만들어 두고 화면은 읽기만 한다 — `stored_summaries()`.

    ⚠️ 유료 호출이다. 근거 후기가 늘어 다시 만들고 싶을 때만 부를 것(멱등 upsert).
    ⚠️ `market` 은 **DB 마켓 키**다 — 화면 표시명이 아니다(`지나` O / `슬라임지나` X).

    근거 0건이면 저장하지 않고 예외를 낸다. 예전엔 조용히 빈 payload 를 저장했는데, 그러면
    `stored_summaries` 는 '요약 있음'으로 읽고 화면엔 영영 '아직 생성하지 않았어요'가 뜬다 —
    원인이 화면 어디에도 안 보인다. 실제로 표시명으로 불러 그 행을 만든 적이 있다(2026-08-06).
    """
    from psycopg.types.json import Jsonb
    view = consolidated_for(market, product, with_summary=True)
    payload = view.get("review_summaries") or {}
    n = view.get("n_reviews") or 0
    if n == 0:
        # 오타인지 마켓 키가 틀린 건지 여기서 갈린다 — 같은 제품을 가진 실제 키를 함께 보여준다.
        with connect() as conn:
            keys = [r[0] for r in conn.execute(
                "SELECT DISTINCT market FROM reviews WHERE product=%s AND market IS NOT NULL "
                "ORDER BY market", (product,)).fetchall()]
        raise ValueError(
            f"'{market}/{product}' 에 근거 후기가 0건이라 요약을 저장하지 않았다"
            + (f" — 이 제품의 실제 마켓 키: {keys}" if keys else " — 이 제품 자체가 DB 에 없다")
            + ". market 은 DB 키이고 화면 표시명이 아니다."
        )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO review_summaries (market, product, payload, model, n_reviews, generated_at)
            VALUES (%s,%s,%s,%s,%s, now())
            ON CONFLICT (market, product) DO UPDATE SET
              payload=EXCLUDED.payload, model=EXCLUDED.model,
              n_reviews=EXCLUDED.n_reviews, generated_at=now()
            """,
            (market, product, Jsonb(payload), settings.model_judge, n),
        )
        conn.commit()
    log.info("요약 생성·저장: %s/%s (근거 %d건)", market, product, n)
    return payload


def stored_summaries(market: str, product: str) -> dict | None:
    """저장된 요약 → `{payload, model, n_reviews, generated_at}`. 없으면 None(=아직 미생성).

    화면은 **이 함수만** 쓴다. 여기서 없다고 생성으로 넘어가면 결국 로드마다 과금된다.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, model, n_reviews, generated_at FROM review_summaries "
            "WHERE market=%s AND product=%s", [market, product]).fetchone()
    if not row:
        return None
    return {"payload": row[0], "model": row[1], "n_reviews": row[2],
            "generated_at": row[3].isoformat() if row[3] else None}


def consolidated_for_market(market: str, *, with_summary: bool = True) -> dict:
    """마켓 단위 종합 뷰 — 이 마켓의 후기 전 행(제품 연결 보류 행 포함) 집계.

    official_spec 은 제품 단위 개념이라 None(스펙은 UI 의 1층 패널이 제품별로 따로 보여준다).
    범위 주의(ADR-0007): 수집이 제품 앵커(ACTIVE scope=product)라 이 뷰는 '마켓 후기 전체'가
    아니라 '이 마켓에서 추적 중인 제품들의 후기 + 게이트를 통과한 마켓 단위 후기' 집계다 —
    UI 라벨도 이 범위로 표기할 것.
    """
    with connect() as conn:
        records = _records_for(conn, market, None)
    sectionize = None
    if with_summary and records:
        sectionize = lambda prompt, schema: LLM().complete(
            prompt, model=settings.model_judge, schema=schema, label="consolidated.section")
    return cv.build_consolidated({"market": market, "product": None},
                                 None, records, llm_sectionize=sectionize)


# ---------------------------------------------------------------- 데모 실행
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    counts = setup(reset=True)
    print("\n=== setup ===")
    print(json.dumps(counts, ensure_ascii=False, indent=2))

    print("\n=== 1층 specs (마켓별 제품) ===")
    for mk in list_markets():
        for p in list_products(mk):
            print(f"  {mk} / {p['product']}  향={p['official_scent']}  종류={p['slime_type']}")

    print("\n=== 검색·근거답변 ===")
    a = search.answer("빈짱 한글과자한줌 향이랑 비즈 어때?", filters={"market": "빈짱"})
    print(a.text)
    for c in a.citations:
        print(f"  [{c['n']}] {c['source']}/{c['market']}/{c['product']}: {c['evidence']}")

    print("\n=== 종합뷰 (빈짱 / 한글과자한줌) ===")
    view = consolidated_for("빈짱", "한글과자한줌")
    print(json.dumps({k: v for k, v in view.items() if k != "review_summaries"},
                     ensure_ascii=False, indent=2))
    print("\n리뷰 요약(인스타/디시/통합):")
    print(json.dumps(view.get("review_summaries"), ensure_ascii=False, indent=2))
