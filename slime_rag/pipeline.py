# -*- coding: utf-8 -*-
"""
파이프라인 오케스트레이터 (Phase 4~6 글루) — 샘플로 end-to-end 를 한 번에.

흐름: 스키마 적용 → 1층 specs 적재(fixture/시드 KB) → 2층 후기 색인(골드)
      → specs↔reviews 조인(spec_id) → 검색·근거답변 → 종합뷰+편향집계.

설계 메모:
- 1층(specs)은 KB market_word 를 키로 적재 → linking 이 정규화한 reviews.market 과 바로 조인.
- 멱등: specs 는 UNIQUE(market,product) upsert, reviews 는 post_id 존재 시 색인 스킵.
- 소스→플랫폼 매핑(amos→dcinside)은 여기 한 곳에서. 종합뷰는 플랫폼 라벨로 편향을 본다.
- UI(app/ui.py)는 이 모듈의 list_*/consolidated_for/answer 만 호출(DB SQL 캡슐화).
"""

from __future__ import annotations

import json
import logging

from .config import settings, ROOT
from .db import connect, apply_schema
from . import layer1, index, linking, search
from . import consolidated_view as cv
from .llm_ops import LLM, summary

log = logging.getLogger("pipeline")

# 수집 소스 → 편향 집계용 플랫폼 키. 종합뷰/요약이 이 라벨로 소스를 가른다.
SOURCE_PLATFORM = {"amos": "dcinside", "instagram": "instagram"}


# ---------------------------------------------------------------- 1층 적재
def _upsert_spec(cur, market, product, scent, base_combo, stype, beads=None,
                 source_permalink=None) -> None:
    """specs (market,product) upsert — fixture 시드와 판매자 자동추출이 공유하는 단일 경로.

    beads: 비즈/토핑 구성요소 리스트(오픈 어휘). None/빈 → []. 제품행에 붙는 부가 메타이며,
    비즈 단독으로는 제품이 되지 않는다(호출부 백스톱이 scent/base_combo/slime_type 로 제품성 판정).
    source_permalink: 공식 스펙 출처 인스타 게시물 URL(없으면 None). COALESCE 로 기존 값 보존 —
    나중 upsert 가 URL 을 안 넘겨도(None) 이미 저장된 URL 을 지우지 않는다.
    """
    cur.execute(
        """
        INSERT INTO specs (market, product, scent, base_combo, slime_type, beads, source_permalink)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (market, product) DO UPDATE SET
          scent=EXCLUDED.scent, base_combo=EXCLUDED.base_combo,
          slime_type=EXCLUDED.slime_type, beads=EXCLUDED.beads,
          source_permalink=COALESCE(EXCLUDED.source_permalink, specs.source_permalink)
        """,
        (market, product, scent, base_combo, stype, list(beads or []), source_permalink),
    )


def load_specs(conn, kb: dict | None = None) -> int:
    """fixture 로 시드한 KB products → specs 테이블 upsert. 반환: 적재 행 수."""
    if kb is None:
        kb = json.loads(settings.kb_demo_path.read_text(encoding="utf-8"))
    layer1.seed_kb_products(kb["markets"])           # in-place: products[] 채움
    rows = list(layer1.iter_specs(kb["markets"]))
    with conn.cursor() as cur:
        for market, product, scent, base_combo, stype, beads, permalink in rows:
            _upsert_spec(cur, market, product, scent, base_combo, stype, beads, permalink)
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
                post_tags = set(extract.hashtags_in(sp.text))
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
                    # beads 는 제품성 판정에 포함하지 않는다(구성요소일 뿐) → 위 백스톱 통과분에만 부가.
                    _upsert_spec(cur, market, p["product"], p.get("scent"),
                                 p.get("base_combo"), p.get("slime_type"), p.get("beads"),
                                 sp.url)                # 공식 스펙 출처 = 판매자 게시물 URL
                    n_spec += 1
        conn.commit()
    if n_skip_nohash:
        log.info("판매자 글 중 제품 해시태그 없음 %d건 스킵(비매품/공지)", n_skip_nohash)

    n_genuine = n_promo = 0
    for u in users:                                  # 실사용/홍보성 → 2층 색인
        rc = u.meta.get("review_class", "genuine")
        doc = extract.extract_review(u.text, llm, settings.model_extract)
        index.index_post(doc, source="instagram",
                         post_id=u.meta.get("shortcode"), review_class=rc)
        if rc == "promo":
            n_promo += 1
        else:
            n_genuine += 1

    with connect() as conn:
        n_join = join_specs(conn)
    counts = {"collected": len(raws), "seller_specs": n_spec,
              "seller_no_hashtag": n_skip_nohash,
              "promo": n_promo, "genuine": n_genuine, "joined_now": n_join,
              "gate_suspect": n_suspect, "llm_calls_saved": n_saved}
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
    n_rows = 0
    with connect() as conn:
        for i, (raw, doc) in enumerate(pairs):
            # post_id 는 조각별로 달라야 한다 — 스레드 배치라도 귀속은 조각 단위(AC12).
            # 댓글 URL 은 스레드 안에서 전부 `…#cmt` 로 같으므로(수집기가 앵커를 안 붙인다)
            # 순번을 넣지 않으면 같은 스레드 댓글들이 한 post_id 로 뭉개진다.
            post_id = raw.url if raw.meta.get("type") == "post" else \
                f"{raw.url}:{raw.meta.get('parent_no')}:{i}"
            n_rows += index.index_post(doc, source="amos", post_id=post_id, conn=conn)
        conn.commit()
    counts["indexed_rows"] = n_rows
    counts["llm"] = {k: summary()[k] for k in ("calls", "input_tokens", "cached_tokens")}
    log.info("ingest_dcinside 완료: %s", counts)
    return counts


# ---------------------------------------------------------------- 2층 색인(골드)
def index_gold(conn) -> int:
    """eval/layer2_gold.json 의 후기들을 색인(멱등: 같은 post_id 있으면 스킵)."""
    gold = json.loads((ROOT / "eval" / "layer2_gold.json").read_text(encoding="utf-8"))
    n = 0
    for rec in gold["records"]:
        pid = rec["id"]
        if conn.execute("SELECT 1 FROM reviews WHERE post_id=%s LIMIT 1", [pid]).fetchone():
            log.info("색인 스킵(이미 있음): %s", pid)
            continue
        n += index.index_post(rec["expected"], source="amos", post_id=pid, conn=conn)
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


def list_products(market: str) -> list[dict]:
    """해당 마켓의 1층 제품 스펙 리스트."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT product, scent, base_combo, slime_type, beads, source_permalink FROM specs "
            "WHERE market=%s ORDER BY product", [market]).fetchall()
    return [{"product": p, "official_scent": s, "base_combo": b, "slime_type": t,
             "beads": list(beads or []), "source_permalink": url}
            for p, s, b, t, beads, url in rows]


def _records_for(conn, market: str, product: str | None) -> list[dict]:
    """DB 후기 → 종합뷰 입력 레코드(소스→platform 주입)."""
    sql = "SELECT source, attributes, review_class FROM reviews WHERE market=%s"
    params: list = [market]
    if product:
        sql += " AND product=%s"
        params.append(product)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for src, attrs, review_class in rows:
        rec = dict(attrs)                       # attributes = 추출 후기 항목 원본
        rec["source"] = {"platform": SOURCE_PLATFORM.get(src, src)}
        rec["review_class"] = review_class      # genuine/promo → 종합뷰가 분리 집계
        out.append(rec)
    return out


def consolidated_for(market: str, product: str, *, with_summary: bool = True) -> dict:
    """1층 스펙 + 2층 후기 → 종합 뷰(소스별·갭·향불일치 + 인스타/디시/통합 리뷰 요약)."""
    with connect() as conn:
        spec_row = conn.execute(
            "SELECT product, scent, base_combo, slime_type, beads, source_permalink FROM specs "
            "WHERE market=%s AND product=%s", [market, product]).fetchone()
        records = _records_for(conn, market, product)
    official_spec = None
    if spec_row:
        official_spec = {"product": spec_row[0], "official_scent": spec_row[1],
                         "base_combo": spec_row[2], "slime_type": spec_row[3],
                         "beads": list(spec_row[4] or []), "source_permalink": spec_row[5]}
    sectionize = None
    if with_summary and records:
        # 소스별(인스타/디시/통합/서포터) 향/질감/장단점 구조화 요약(structured outputs).
        sectionize = lambda prompt, schema: LLM().complete(
            prompt, model=settings.model_judge, schema=schema, label="consolidated.section")
    return cv.build_consolidated({"market": market, "product": product},
                                 official_spec, records, llm_sectionize=sectionize)


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
