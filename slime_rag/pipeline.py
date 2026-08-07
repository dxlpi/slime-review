# -*- coding: utf-8 -*-
"""
파이프라인 오케스트레이터 (Phase 4~6 글루) — 샘플로 end-to-end 를 한 번에.

흐름: 스키마 적용 → 1층 specs 적재(fixture/시드 KB) → 2층 후기 색인(골드)
      → specs↔reviews 조인(spec_id) → 검색·근거답변 → 종합뷰+편향집계.

설계 메모:
- 1층(specs)은 KB market_word 를 키로 적재 → linking 이 정규화한 reviews.market 과 바로 조인.
- 멱등: 양쪽 다 **DB 제약**이 강제한다 — specs 는 UNIQUE(market,product) upsert,
  reviews 는 UNIQUE(source,post_id,product) + ON CONFLICT DO NOTHING(`index.index_post`).
  ⚠️ 2026-08-07 이전 이 줄은 'reviews 는 post_id 존재 시 색인 스킵'이라고 적혀 있었지만
  그 스킵은 `index_gold` 에만 있었고 실제 수집 경로(`ingest_hashtag`·`ingest_dcinside`)는
  맨 INSERT 였다. 결과: 배치를 두 번 돌려 인스타 80행 중 28행이 중복(같은 글이 런마다
  다른 감성으로 추출돼 독립 후기처럼 집계에 들어감). 주석이 지키던 규칙을 제약으로 옮긴 이유다.
- 소스→플랫폼 매핑(amos→dcinside)은 여기 한 곳에서. 종합뷰는 플랫폼 라벨로 편향을 본다.
- 표시 계층은 이 모듈의 list_*/consolidated_for/answer 만 호출(DB SQL 캡슐화).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
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
    source_permalink: 공식 스펙 출처 인스타 게시물 URL(없으면 None).

    **모든 칸이 COALESCE 다 — 재수집이 기존 값을 지우지 않는다**(2026-08-07).
    예전엔 `source_permalink`·`official_texture` 만 보존하고 `scent`·`base_combo`·`slime_type`·
    `beads` 는 무조건 덮어썼다. 그러면 같은 제품이 향을 안 적은 다른 글에서 다시 잡힐 때
    이미 있던 향이 null 로 날아간다 — 프로필 경로는 **최신 ~12글만** 주므로 같은 제품이
    여러 글에 걸쳐 잡히는 게 정상이고, 그래서 이건 이론적 위험이 아니라 예정된 사고였다.
    수집은 **누적**이지 최신 글로의 교체가 아니다.
    ⚠️ 되돌리지 말 것. 값을 실제로 **고쳐야** 할 때(판매자가 향을 바꿨다 등)는 이 경로가 아니라
      무엇을 덮는지 명시하는 별도 갱신으로 한다 — `index_post` 의 `DO NOTHING` 과 같은 원칙이다.
    `beads` 는 배열이라 NULL 이 아니라 **빈 배열**이 '미언급'이므로 `cardinality` 로 가른다.
    """
    cur.execute(
        """
        INSERT INTO specs (market, product, scent, base_combo, slime_type,
                           official_texture, beads, source_permalink)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (market, product) DO UPDATE SET
          scent=COALESCE(EXCLUDED.scent, specs.scent),
          base_combo=COALESCE(EXCLUDED.base_combo, specs.base_combo),
          slime_type=COALESCE(EXCLUDED.slime_type, specs.slime_type),
          beads=CASE WHEN cardinality(EXCLUDED.beads) > 0
                     THEN EXCLUDED.beads ELSE specs.beads END,
          official_texture=COALESCE(EXCLUDED.official_texture, specs.official_texture),
          source_permalink=COALESCE(EXCLUDED.source_permalink, specs.source_permalink)
        """,
        (market, product, scent, base_combo, stype, official_texture,
         list(beads or []), source_permalink),
    )


# 제품성 판정 필드 — **하나라도** 차면 제품으로 인정한다.
# `official_texture` 가 여기 있는 이유(2026-08-07 사용자 규칙): 어떤 마켓은 **풀조합·향을 아예
# 안 적는다**(실측: 진통제 향 0/5 · 풀조합 1/5). 대신 종류와 질감 서술은 쓴다. 앞의 셋만 보면
# 그런 마켓의 제품이 통째로 드롭된다 — 캡션 관행 차이로 제품이 사라지는 건 데이터 문제가 아니라
# 우리 게이트의 문제다. 미언급 필드는 그냥 null 로 두고 제품 행은 만든다.
# ⚠️ `beads` 는 넣지 않는다 — 비즈는 구성요소라 그것만 있는 글은 제품 안내가 아니다.
_PRODUCTHOOD_FIELDS = ("scent", "base_combo", "slime_type", "official_texture")


def _specs_from_seller_post(cur, market: str | None, text: str, url: str | None,
                            llm) -> tuple[int, bool, int]:
    """판매자 게시물 1건 → specs 행 upsert. 반환 `(만든 행 수, 해시태그없음_스킵, 제품성탈락 수)`.

    해시태그 경로(`ingest_hashtag`)와 프로필 경로(`ingest_seller_profiles`)가 **이 한 벌을
    공유한다.** 규칙이 두 곳에 있으면 조용히 갈라진다 — 실제로 후기 경로에 같은 게이트가
    없어서 유령 제품이 생겼다(`.omc/plans/product-attribution-repair.md`).

    게이트 둘 다 **결정적**이고 LLM 판정에 맡기지 않는다:
      · 제품 해시태그가 하나도 없으면 비매품/공지글 → 통째로 스킵.
      · 제품명은 반드시 그 캡션의 해시태그여야 한다 — LLM 이 향료·재료어를 지어내도 드롭.
    """
    excl = _tag_exclusions(market) if market else frozenset()
    post_tags = set(extract_mod().product_hashtags(text, exclude=excl))
    if not post_tags:
        return 0, True, 0
    spec = extract_mod().extract_spec(text, llm, settings.model_extract)
    n = n_thin = 0
    for p in spec.get("products", []):
        if not (market and p.get("product")):
            continue                             # 제품명 없으면 스펙 행 못 만듦
        if p["product"].strip() not in post_tags:
            continue                             # 제품명 = 그 캡션의 해시태그여야 한다
        if not any(p.get(k) for k in _PRODUCTHOOD_FIELDS):
            # 네 칸 전부 null. 드롭하되 **세어서 드러낸다** — 예전엔 조용한 continue 라
            # 어떤 마켓의 제품이 통째로 사라져도 카운트 어디에도 안 보였다.
            n_thin += 1
            continue
        _upsert_spec(cur, market, p["product"], p.get("scent"),
                     p.get("base_combo"), p.get("slime_type"),
                     p.get("official_texture"), p.get("beads"), url)
        n += 1
    return n, False, n_thin


def extract_mod():
    """`extract` 지연 import — 모듈 최상위 import 아님(기존 함수들과 같은 규칙)."""
    from . import extract
    return extract


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
    n_collected = len(raws)                           # 컷 전 원본 수 — 아래에서 raws 가 줄어든다
    gate_terms = bias.load_gate_terms()               # 파일 읽기 — 무과금이라 컷보다 먼저 둔다

    # 추출 전 기보유 컷 — **`bias.partition` 앞**이다. 이 경로에서 LLM 을 먼저 쓰는 건 추출이
    # 아니라 홍보성 캐스케이드라, 컷을 `extract_review` 직전에 두면 게이트 통과분의 verdict 값을
    # 이미 치른 뒤가 된다. `partition` 앞으로 올리면 홍보성 판정분까지 같이 아낀다.
    #
    # 판매자 글이 같이 걸릴 걱정은 없다 — 필터는 `reviews`(2층) 조회인데 판매자 캡션은 `specs`
    # 로만 가고 `reviews` 에 행을 만들지 않는다. 기보유로 판정될 길 자체가 없다.
    # (판매자 경로의 재추출 억제는 `ingest_seller_profiles` 의 `skip_seen` 소관이고, 판정
    #  기준도 다르다 — 저긴 `reviews` 가 아니라 이미 본 게시물 URL 을 본다.)
    with connect() as conn:
        seen = index.existing_post_ids(
            conn, "instagram", [sc for r in raws if (sc := r.meta.get("shortcode"))])
    fresh = [r for r in raws if r.meta.get("shortcode") not in seen]
    n_seen = len(raws) - len(fresh)
    # 조각당 추출 1콜 + 게이트를 통과했을 홍보 verdict 1콜. 게이트는 순수 단어매칭이라 무과금이다.
    saved_by_dedup = n_seen + sum(1 for r in raws
                                  if r.meta.get("shortcode") in seen
                                  and bias.promo_gate(r.text, gate_terms))
    if n_seen:
        log.info("기보유 조각 %d건 스킵 → LLM 호출 %d회 절감(추출+홍보판정)", n_seen, saved_by_dedup)
    raws = fresh

    llm = LLM()
    # 홍보성 판정은 게이트(recall)→LLM(verdict) 캐스케이드. 값싼 게이트가 '홍보 의심'만 통과시키고,
    # 명백한 실사용은 즉시 genuine 단락(LLM 미호출) → 호출 수를 크게 줄인다. precision 은 LLM 몫.
    promo_detector = bias.make_gated_llm_promo_detector(llm, settings.model_extract, terms=gate_terms)
    sellers, users = bias.partition(raws, kb, promo_detector=promo_detector)
    # 관측성: 게이트 통계는 판매자 제외(non-seller) 대상. 게이트통과=LLM호출, 단락=절감.
    n_suspect = sum(1 for u in users if bias.promo_gate(u.text, gate_terms))
    n_saved = len(users) - n_suspect
    log.info("해시태그 수집 %d건(기보유 %d 제외 → 신규 %d) → 판매자 %d, 유저후기 %d "
             "(게이트통과 %d / genuine단락 %d / 절감 LLM호출 %d)",
             n_collected, n_seen, len(raws), len(sellers), len(users),
             n_suspect, n_saved, n_saved)
    n_spec = n_skip_nohash = n_thin = 0
    with connect() as conn:
        with conn.cursor() as cur:
            for sp in sellers:                       # 판매자 캡션 → 1층 공식 스펙
                # 게이트 규칙은 `_specs_from_seller_post` 한 곳에 있다 — 프로필 경로
                # (`ingest_seller_profiles`)와 공유한다.
                n, skipped, thin = _specs_from_seller_post(
                    cur, sp.meta.get("seller_market"), sp.text, sp.url, llm)
                n_spec += n
                n_skip_nohash += int(skipped)
                n_thin += thin
        conn.commit()
    if n_skip_nohash:
        log.info("판매자 글 중 제품 해시태그 없음 %d건 스킵(비매품/공지)", n_skip_nohash)

    n_genuine = n_promo = 0
    n_ref = n_noref = 0                              # 관측성: 원문 링크 식별자 유/무 행수
    # 후기 분기의 제품 게이트 재료. 판매자 분기가 먼저 돌아 specs 를 채운 **뒤에** 읽는다 —
    # 같은 런에서 방금 수집한 1층 제품도 타이브레이커로 쓸 수 있다.
    known: dict[str, set] = {}
    with connect() as conn:
        for mk, pr in conn.execute("SELECT market, product FROM specs").fetchall():
            known.setdefault(mk, set()).add(pr)
    # ⚠️ 여기서 필요한 건 **KB 객체**(`resolve_market`·`markets` 속성)지 위의 `kb` dict 가 아니다.
    #   `bias.partition` 은 원본 JSON dict 를 받고, 개체연결 쪽은 파싱된 객체를 받는다 — 같은
    #   이름으로 두 형태가 오가는 자리라 한쪽을 다른 쪽에 넘기면 AttributeError 로 죽는다.
    kb_obj = linking.load_kb()
    all_excl = frozenset().union(*(extract.market_tag_exclusions(m) for m in kb_obj.markets)) \
        if kb_obj.markets else frozenset()

    for u in users:                                  # 실사용/홍보성 → 2층 색인
        rc = u.meta.get("review_class", "genuine")
        doc = extract.extract_review(u.text, llm, settings.model_extract)
        # 유령 제품 차단 — 캡션의 **스펙 줄**(풀조합/향료)이 제품명으로 올라오는 걸 막는다.
        # 판매자 분기엔 원래 있던 게이트가 후기 분기엔 없어서 인스타 80행 중 46행이 오염됐다.
        # 규칙은 프롬프트가 아니라 코드로 강제한다(전언 차단·판매자 게이트와 같은 수법).
        mkt = _market_from_caption(kb_obj, u.text)
        doc = extract.repair_product_names(
            doc, u.text,
            exclude=_tag_exclusions(mkt) if mkt else all_excl,
            known_products=known.get(mkt, ()))
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
    counts = {"collected": n_collected, "seller_specs": n_spec,
              "seller_no_hashtag": n_skip_nohash, "seller_thin_spec": n_thin,
              "promo": n_promo, "genuine": n_genuine, "joined_now": n_join,
              # `llm_calls_saved`(홍보 게이트 단락분)와 이름을 **가른다** — 합치면 어느 절감인지
              # 사후에 못 나누고, 둘은 아끼는 대상도 다르다(판정 단락 vs 조각 자체를 안 봄).
              "gate_suspect": n_suspect, "llm_calls_saved": n_saved,
              "skipped_seen": n_seen, "llm_calls_saved_by_dedup": saved_by_dedup,
              "rows_with_source_ref": n_ref, "rows_without_source_ref": n_noref}
    log.info("ingest_hashtag 완료: %s", counts)
    return counts


# ---------------------------------------------------------------- 1층 수집(마켓 본인 피드)
def ingest_seller_profiles(markets: list[str] | None = None, *,
                           only_missing: bool = False, skip_seen: bool = True,
                           limit_per_market: int = 12, dry_run: bool = True) -> dict:
    """마켓 **본인 계정 피드** → 1층 공식 스펙. ADR-0003 이 막힌 자리의 우회 경로다.

    `business_discovery` 는 App Review 벽이지만(ADR-0003) Apify `instagram-profile-scraper`
    는 공개 게시물을 승인 없이 준다 — 같은 자리를 메우는 스크래핑 대체물이다.

    **해시태그 경로와 달리 표본 편향이 없다.** 저건 태그당 상한(기본 30)에 랭킹된 부분집합이라
    인기글이 과대표집되지만, 이건 그 계정의 최신 게시물을 액터가 주는 대로(~12개) 받는다.
    대신 **최신분만** 온다 — 옛 제품은 안 잡히므로 주기적으로 돌려 앞으로 쌓는 용도다.

    ⚠️ 가져오는 건 **판매자 글**이라 1층 전용이다. 유저 후기(2층)는 남의 계정에 있어서 이
       경로로는 영영 안 들어온다 — 그건 해시태그 경로 몫이고, 표본 편향도 거기 얘기다.

    markets: 대상 market_word 목록. None 이면 **KB 의 핸들 있는 마켓 전부**.
    only_missing=True: 그중 `specs` 가 아직 하나도 없는 마켓만. 첫 훑기용 싼 경로다.
    skip_seen=True(기본): 이미 스펙을 만든 적 있는 **게시물 URL**(`specs.source_permalink`)은
      `extract_spec` 을 다시 부르지 않는다. 여기 컷의 근거는 중복이 아니라 **순수 비용**이다 —
      `specs` 는 `UNIQUE(market, product)` upsert 라 중복 행 자체는 원래 안 생기지만, 액터가
      매번 같은 최신 ~12글을 주므로 캡션 1건당 LLM 값이 런마다 그대로 나간다.
      ⚠️ 대가: 판매자가 **캡션을 고쳐도 반영되지 않는다**(계획 R4). 고쳐야 할 때는
        `skip_seen=False` 로 강제 재추출한다 — 그래서 컷이 상수가 아니라 옵션이다.
      ⚠️ 스펙 행을 못 만든 게시물(해시태그 없음·제품성 탈락)은 URL 이 안 남아 매번 다시 본다.
        `reviews` 쪽 컷과 같은 구조적 구멍이고, 하루 규모가 유계라 의도적으로 남긴다.
    ⚠️ `only_missing` 이 **기본값이었던 게 버그였다**(~2026-08-07). 이 함수의 존재 이유가
      '액터가 최신 ~12글만 주니 주기적으로 돌려 앞으로 쌓는 것'인데, 기본 대상이
      '스펙 0개인 마켓'이면 두 번째 실행부터 대상이 **빈 목록**이 된다 — 누적하라고 만든
      경로가 누적을 못 했다. 이제 기본은 전체이고, 옛 동작은 명시 옵션으로만 쓴다.
    dry_run=True(기본): 네트워크·LLM·DB 미접촉. 대상과 예상비용만 돌려준다.
    ⚠️ dry_run=False 는 **유료**다 — Apify 프로필 요금 + 캡션 1건당 `extract_spec` LLM 호출.
    """
    from .sources import InstagramProfileSource

    kb = linking.load_kb()
    by_handle = {m["handle"]: m.get("market_word") for m in kb.markets if m.get("handle")}
    if markets is not None:
        want = set(markets)
        targets = [(w, h) for h, w in by_handle.items() if w in want]
        if unknown := want - {w for w, _h in targets}:
            # 무음 갭 금지: 오타·미등록 마켓을 조용히 건너뛰면 '수집했는데 왜 없지'가 된다.
            log.warning("KB 에 핸들이 없어 건너뛴 마켓 %d개: %s", len(unknown), ", ".join(sorted(unknown)))
    else:
        targets = [(w, h) for h, w in by_handle.items()]
        if only_missing:
            with connect() as conn:
                have = {r[0] for r in conn.execute("SELECT DISTINCT market FROM specs").fetchall()}
            targets = [(w, h) for w, h in targets if w not in have]

    cost = len(targets) / 1000 * InstagramProfileSource.COST_PER_1000
    out: dict = {"targets": [w for w, _h in targets], "handles": [h for _w, h in targets],
                 "apify_cost_usd": round(cost, 4), "dry_run": dry_run, "skip_seen": skip_seen}
    if dry_run or not targets:
        log.info("ingest_seller_profiles(dry): %d마켓 · 예상 Apify 비용 $%.4f", len(targets), cost)
        return out

    src = InstagramProfileSource(token=settings.apify_token)
    raws = list(src.collect([h for _w, h in targets], limit=limit_per_market * len(targets)))
    llm = LLM()
    n_spec = n_skip = n_nomarket = n_thin = n_seen = 0
    with connect() as conn:
        # 기보유 컷의 판정 기준은 `specs`(제품)가 아니라 **이미 스펙을 만든 게시물 URL** 이다 —
        # 같은 게시물이 다시 와도 새 제품이 나올 리 없고, 제품 기준으로 보면 한 글의 여러 제품 중
        # 하나만 남은 경우를 '봤다'로 오판한다.
        seen_urls = {r[0] for r in conn.execute(
            "SELECT DISTINCT source_permalink FROM specs WHERE source_permalink IS NOT NULL"
        ).fetchall()} if skip_seen else set()
        with conn.cursor() as cur:
            for sp in raws:
                market = by_handle.get((sp.meta or {}).get("owner_username"))
                if not market:                      # 액터가 리다이렉트된 계정을 줄 수 있다
                    n_nomarket += 1
                    continue
                if sp.url in seen_urls:             # 유료 `extract_spec` 앞에서 자른다
                    n_seen += 1
                    continue
                n, skipped, thin = _specs_from_seller_post(cur, market, sp.text, sp.url, llm)
                n_spec += n
                n_skip += int(skipped)
                n_thin += thin
        conn.commit()
    if n_seen:
        log.info("기보유 판매자 게시물 %d건 스킵 → extract_spec 호출 %d회 절감", n_seen, n_seen)
    out.update({"collected_posts": len(raws), "specs_upserted": n_spec,
                "skipped_no_hashtag": n_skip, "skipped_unknown_handle": n_nomarket,
                "skipped_thin_spec": n_thin,
                # 게시물 1건 = `extract_spec` 1콜이라 스킵 수가 곧 절감 호출 수다.
                "skipped_seen": n_seen, "llm_calls_saved_by_dedup": n_seen,
                "llm": {k: summary()[k] for k in ("calls", "input_tokens", "cached_tokens")}})
    log.info("ingest_seller_profiles 완료: %s", out)
    return out


# ---------------------------------------------------------------- 2층 색인(디시 실수집)
def dc_post_id(raw) -> str | None:
    """디시 조각 → `reviews.post_id`. 못 만들면 None(호출부가 색인을 건너뛴다).

    **런에 의존하지 않는 값만 쓴다.** 예전엔 댓글 id 자리에 그 런의 `enumerate` 위치(`i`)를
    넣었다 — 수집 결과가 한 건만 달라져도 이후 모든 댓글의 `i` 가 밀려서 **같은 댓글이 다른
    `post_id`** 로 들어갔다. 그러면 `UNIQUE(source, post_id, product)` 가 댓글에 대해 아무것도
    막지 못한다(글·인스타는 URL·shortcode 라 정상이었다). 기보유 조각을 건너뛰는 컷을
    그 위에 얹으면 **조용히 아무것도 안 걸러지므로**, 이 함수가 Phase 1 컷의 선행 조건이다.

    안정 키는 이미 수집돼 있었다 — `comment_no` 는 디시가 주는 댓글 고유 id다
    (`sources/dcinside.py` `_parse_comments`, 2026-08-06 라이브 확인). `post_id` 만 그걸 안 썼다.

    ⚠️ `ordinal` 폴백을 쓰지 말 것. 수집기가 그 값을 **일부러 다른 이름으로** 싣는다
      ("id 를 못 잡았을 때만 채우는 스레드 내 순번 폴백 — 이름을 분리해 앵커 조립에 절대
      안 쓰이게 한다"). 여기서 그걸 쓰면 방금 없앤 런 의존성이 그대로 재발한다.
    ⚠️ 반환 형식은 마이그레이션 SQL(`sql/schema.sql`)이 만드는 값과 **같아야** 한다:
      `raw.url`(= `…&no=<스레드>#cmt`) + `':'` + `comment_no`.
    """
    if raw.meta.get("type") == "post":
        return raw.url
    cno = raw.meta.get("comment_no")
    return f"{raw.url}:{cno}" if cno else None


def _max_thread_no(conn, anchors: list[str] | None = None) -> int | None:
    """이 **수집 앵커**로 이미 색인한 아모스갤 글번호의 최댓값. 없으면 None(= 전량 수집).

    ⚠️ `source='amos'` **전체** 최댓값이 아니다. 수집은 제품 앵커 키워드 검색이라
      (ADR-0007 ACTIVE scope=product), 전체 최댓값을 쓰면 **처음 수집하는 제품**의 워터마크가
      남의 제품이 올려놓은 최신 글번호가 된다 — 그 제품의 과거 글 전부가 상세 요청도 없이
      잘리고, 목록이 최신순이라 첫 페이지에서 페이징까지 끝난다. 카운트에는 '새 글 없음'과
      구분되지 않는 0 만 남으므로 **조용한 유실**이다(이 저장소가 금지하는 실패 모드).
    앵커에 해당하는 행이 없으면 None 을 돌려 전량 수집으로 떨어진다 — 페일오픈이다.
    앵커 표면형이 KB 정규명과 어긋나도 같은 방향으로 실패한다(워터마크가 낮아져 HTTP 만 더 쓴다).
    """
    sql = ("SELECT max((source_ref->>'thread_no')::bigint) FROM reviews "
           "WHERE source='amos' AND source_ref->>'thread_no' ~ '^[0-9]+$'")
    params: list = []
    if anchors:
        sql += " AND product = ANY(%s)"
        params.append(list(anchors))
    row = conn.execute(sql, params or None).fetchone()
    return int(row[0]) if row and row[0] is not None else None


# 워터마크 안전 마진 — 실제 컷은 `max(thread_no) - WATERMARK_MARGIN` 이다(계획 R5).
# 수집이 **키워드 검색** 기반이라 목록 순서와 글번호 순서가 정확히 같지 않다: 이번 런에
# 안 걸린 옛 글이 다음 런의 다른 키워드로 뒤늦게 매칭될 수 있다. 워터마크를 정확히 최댓값에
# 두면 그런 글이 영영 안 들어온다. 마진 안쪽은 Phase 1 의 조각 단위 컷이 잡으므로
# **HTTP 만 조금 더 쓰고 LLM 은 안 쓴다** — 그게 이 마진의 값이다.
# 200 은 아모스갤 신규 스레드 ~18.9건/일(실측) 기준 약 열흘치다.
WATERMARK_MARGIN = 200


def ingest_dcinside(slime: str, market: str | None = None, aliases: list[str] | None = None,
                    limit: int = 30, comment_pages: int = 1, dry_run: bool = False, *,
                    incremental: bool = True, revisit_threads: list[int] | None = None) -> dict:
    """
    디시 실수집 → 관련성 게이트 → **스레드 배치 추출** → 색인 (계획 C-4).

    `extract_collected` 은 진작 있었지만 파이프라인에 연결돼 있지 않았다(§1-G) — 인스타 경로만
    `ingest_hashtag` 로 이어져 있었다. 지금 연결하는 이유는 지금이 **추출 단위를 정하기 가장 싼
    시점**이기 때문이다. 나중에 per-comment 로 굳은 뒤 뜯는 것보다 배치 단위로 처음부터 잇는 게 싸다.

    dry_run=True 면 **LLM 을 부르지 않고** 수집·게이트·기보유 컷까지 돌려 카운트를 돌려준다
      (OpenAI 키 없이 점검용). ⚠️ 컷 판정에 `reviews` 조회가 한 번 들어가므로 **DB 는 필요하다** —
      예전엔 컷 앞에서 반환했지만, 그러면 유료 실행을 풀지 말지 정하는 데 필요한 숫자
      (`skipped_seen`·`llm_calls_saved_by_dedup`)를 dry_run 이 보여주지 못한다.
    반환: 카운트 요약.

    incremental=True(기본): **이 앵커로** 이미 색인한 글번호 최댓값에서 `WATERMARK_MARGIN` 을
      뺀 값을 워터마크로 삼아, 그보다 옛 글은 **상세 요청 자체를 보내지 않는다**(Phase 2 ·
      HTTP 절감). 앵커에 색인 이력이 없으면(= 처음 수집하는 제품) 워터마크가 없어 전량
      수집으로 떨어진다 — 그 판단을 `counts["watermark_anchors"]` 와 로그로 드러낸다.
    revisit_threads: 워터마크 아래라도 다시 볼 글번호(선택). **새 댓글은 옛 글에 달리므로
      워터마크로는 안 잡힌다** — 자동 선정은 하지 않고 호출부가 명시한다. 검색 목록에 없어도
      닿도록 수집기가 이 글번호들을 **직접 조회**한다.
    """
    from . import extract
    from .sources import DCInsideSource, expand_queries

    src = DCInsideSource(gallery_id="amos", comment_pages=comment_pages)
    queries = expand_queries(slime, aliases=aliases or [], market_word=market)
    target = {"market": market, "slime": slime}
    # 워터마크는 **앵커별**이다 — 전체 최댓값을 쓰면 처음 보는 제품의 과거 글이 통째로 잘린다.
    anchors = [slime, *(aliases or [])]
    watermark = None
    if incremental:
        with connect() as conn:
            top = _max_thread_no(conn, anchors)
        watermark = None if top is None else top - WATERMARK_MARGIN
        if watermark is None:
            log.info("워터마크 없음(앵커 %s 의 색인 이력 없음) → 전량 수집", anchors)
    # `collect_all` 을 쓰지 않는다 — 소스별 인자를 넘길 자리가 없고, 단일 소스 경로에서 수집
    # 예외를 삼키면 '조용히 0건'이 된다(그건 오류가 아니라 결과처럼 보인다).
    raws = list(src.collect(queries, limit=limit, target=target,
                            min_thread_no=watermark, revisit_threads=revisit_threads))
    n_post = sum(1 for r in raws if r.meta.get("type") == "post")
    counts = {"collected": len(raws), "posts": n_post, "comments": len(raws) - n_post,
              "queries": queries, "min_thread_no": watermark,
              # 워터마크가 무엇을 기준으로 잡혔는지 — '새 글 없음'과 '옛 글을 안 봤음'을 가른다.
              "watermark_anchors": anchors,
              # 예산 초과로 **처리 안 된** 후보 수. 기보유 컷은 수집 뒤에 도는지라 게이트 예산은
              # 이미 본 조각에도 쓰인다 — 이 값이 0 보다 크면 limit 을 올릴 신호다(침묵 절단 금지).
              "gate_unprocessed": getattr(src.last_gate, "unprocessed", 0)}
    # 추출 전 기보유 컷 — 디시는 **배치 추출**이라 컷이 `extract_collected` 앞에 있어야 한다.
    # 뒤에 두면 이미 LLM 값을 치른 뒤다. 이 경로엔 추출보다 먼저 도는 LLM 이 없다(관련성
    # 게이트가 임베딩 기반이고 `classify_fn` 은 기본 None) — 그래서 여기가 첫 유료 단계다.
    seen: set[str] = set()
    if raws:                                   # 수집 0건이면 DB 도 건드리지 않는다
        with connect() as conn:
            seen = index.existing_post_ids(
                conn, "amos", [pid for r in raws if (pid := dc_post_id(r))])
    fresh = [r for r in raws if dc_post_id(r) not in seen]
    n_seen = len(raws) - len(fresh)
    # 기보유 **글**은 그 스레드에 새 조각이 남아 있으면 배치에 **문맥으로** 남긴다.
    # 배치 추출의 존재 이유 절반이 형제 문맥이다(AC13: 제품명을 생략한 댓글의 귀속, 그리고
    # `extract_collected` 의 market 상속은 글에서 온 값을 권위로 삼는다). 글을 빼 버리면
    # 증분 런에서만 조용히 그 성질을 잃는다 — 배치는 어차피 돌아야 하므로 추가 호출은 없고,
    # 늘어나는 건 그 호출의 입력 토큰뿐이다.
    # ⚠️ 스레드 판정은 `extract.thread_key` **한 곳**에서 온다. 글의 meta 엔 스레드 번호가 없어서
    #   (`_parse_post` 는 nick·조회·추천만 싣는다) `meta["thread_no"]` 로 맞추면 글 쪽이 늘 None 이
    #   되고, 그 None 이 집합에 들어가면 **죽은 스레드의 글까지 전부 매칭**된다 — 문맥은 안 남고
    #   버릴 글에 유료 호출만 나가는 양방향 오작동이다(2026-08-07 실측).
    live_threads = {k for r in fresh if (k := extract.thread_key(r))}
    context = [r for r in raws
               if r.meta.get("type") == "post" and dc_post_id(r) in seen
               and extract.thread_key(r) in live_threads]
    batch_input = fresh + context
    # 절감량은 '거른 조각 수'가 아니라 **사라진 배치 수**다 — 조각당 1콜이 아니기 때문.
    saved_calls = extract.count_thread_batches(raws) - extract.count_thread_batches(batch_input)
    counts["skipped_seen"] = n_seen
    counts["llm_calls_saved_by_dedup"] = saved_calls
    counts["context_posts"] = len(context)     # 추출엔 들어가되 색인은 안 되는 기보유 글
    if n_seen:
        log.info("기보유 조각 %d건 스킵(문맥 유지 %d건) → 추출 호출 %d회 절감",
                 n_seen, len(context), saved_calls)

    # 컷은 무료(DB 왕복 하나)라 **dry_run 도 그 뒤에서** 끝낸다 — 유료 실행을 풀기 전에
    # 사용자가 보고 싶은 숫자가 바로 이 절감량이다. 컷 앞에서 반환하면 dry_run 이 그걸 못 보여준다.
    if dry_run:
        counts["dry_run"] = True
        log.info("ingest_dcinside(dry) 완료: %s", counts)
        return counts

    # 신규 조각이 0건이면 LLM 을 **만들지도** 않는다 — 생성자가 API 키를 요구하고, 부를 일도 없다.
    llm = LLM() if batch_input else None
    pairs = extract.extract_collected(batch_input, llm, settings.model_extract) if batch_input else []
    n_rows = n_ref = n_noref = n_no_cno = n_context_skip = 0   # 관측성 카운터
    with connect() as conn:
        for raw, doc in pairs:
            # post_id 는 조각별로 달라야 한다 — 스레드 배치라도 귀속은 조각 단위(AC12).
            # 댓글 URL 은 스레드 안에서 전부 `…#cmt` 로 같으므로(수집기가 앵커를 안 붙인다)
            # 댓글 고유 id 를 붙여야 같은 스레드 댓글들이 한 post_id 로 뭉개지지 않는다.
            post_id = dc_post_id(raw)
            if post_id is None:
                # 무음 스킵 금지 — 유실량이 보여야 판단할 수 있다(계획 R2). id 없는 댓글을
                # 순번으로 때우면 런마다 키가 흔들려 멱등성이 통째로 무효가 된다.
                log.warning("댓글 id 없음 → 색인 스킵(스레드 %s)", raw.meta.get("thread_no"))
                n_no_cno += 1
                continue
            if post_id in seen:
                # 문맥용으로만 배치에 넣은 기보유 글 — 임베딩·INSERT 를 건너뛴다.
                # 세어서 드러낸다: 아래 단언이 '문맥으로 넣은 수 = 색인 건너뛴 수'를 강제하지
                # 않으면, 문맥 계산이 바뀔 때 이 continue 가 조용한 스킵으로 변한다.
                n_context_skip += 1
                continue
            # 링크용 식별자는 post_id 와 별개다 — 이건 표시용 주소 조립 규칙이고(ADR-0009)
            # post_id 는 색인 유일성 키다. 같은 재료로 만들지만 소비처가 다르다.
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
    counts["skipped_no_comment_no"] = n_no_cno
    # 불변식: 문맥으로 넣은 글 수 == 색인에서 건너뛴 수. 양쪽을 **둘 다 내보내야** 등식이
    # 사후에도 검증 가능하다 — 입력 쪽만 내보내면 그 `continue` 는 세지 않은 스킵이 된다.
    # ⚠️ `assert` 가 아니다: 여기까지 왔다는 건 유료 추출과 커밋이 이미 끝났다는 뜻이라,
    #   예외로 죽으면 그 런의 비용 원장(`counts["llm"]`)까지 같이 사라진다. 게다가 `python -O`
    #   에선 단언 자체가 없어져 검사가 조용히 증발한다. 로그로 크게 남기고 카운트를 반환한다.
    counts["context_skipped"] = n_context_skip
    if n_context_skip != len(context):
        log.error("문맥 글 불변식 위반: 배치 투입 %d건 vs 색인 스킵 %d건 — 문맥 판정과 "
                  "색인 스킵 조건이 어긋났다(스레드 키 규칙 확인).", len(context), n_context_skip)
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


# ---------------------------------------------------------------- 제품명 귀속 복구(백필)
def _market_from_caption(kb, text: str) -> str | None:
    """캡션의 해시태그로 마켓을 푼다 — 행의 `market` 이 비었을 때만 쓰는 폴백(AC7-1).

    유일하게 확정될 때만 돌려준다. 후보가 둘 이상이면 None — 모르는 채로 두는 게
    엉뚱한 마켓의 제품 목록을 타이브레이커로 쓰는 것보다 낫다.
    """
    from . import extract

    hits = set()
    for tag in extract.hashtags_in(text or ""):
        cands, conf, _why = kb.resolve_market(tag)
        if len(cands) == 1 and conf >= 0.85:
            hits.add(cands[0].get("market_word") or cands[0].get("market"))
    return hits.pop() if len(hits) == 1 else None


def repair_product_attribution(*, dry_run: bool = True) -> dict:
    """기존 인스타 행의 `product` 를 캡션 해시태그 기준으로 복구한다(`extract.resolve_product_name`).

    **LLM 을 부르지 않는다**(AC10). 원문 캡션이 `body` 에 이미 있어 다시 읽으면 되고,
    재임베딩도 로컬 BGE-M3 라 API 비용이 0이다 — 재수집이 필요한 작업이 아니다.

    ⚠️ `render_review` 가 제품명을 텍스트에 굽기 때문에 `product` 만 바꾸면 `evidence`·`tokens`·
      `embedding` 이 옛 이름을 가리킨 채 남는다(검색이 유령 이름으로 계속 맞는다). 셋을 함께 다시 만든다.
    ⚠️ 접기 키는 `UNIQUE(source, post_id, product)` 와 **정확히 같아야** 한다. 마켓을 키에 넣으면
      제약이 안 보는 축으로 접게 되어 갱신이 제약 위반으로 죽는다.
    보류(None)는 접지 않는다 — Postgres 가 NULL 을 서로 다른 값으로 보므로 제약에도 안 걸린다.
    """
    from . import extract

    kb = linking.load_kb()
    all_excl = frozenset().union(*(extract.market_tag_exclusions(m) for m in kb.markets)) \
        if kb.markets else frozenset()
    with connect() as conn:
        known: dict[str, set] = {}
        for mk, pr in conn.execute("SELECT market, product FROM specs").fetchall():
            known.setdefault(mk, set()).add(pr)
        rows = conn.execute(
            "SELECT id, post_id, market, product, body, attributes FROM reviews "
            "WHERE source='instagram' AND body IS NOT NULL ORDER BY id").fetchall()

    # 1) 조각(post)마다 ①로 확정되는 이름을 먼저 모은다 — 그 조각이 **이미 갖고 있는 제품**은
    #    다른 이름의 흡수 대상이 될 수 없다(`taken`). 행 단위로만 보면 이 맥락이 안 보인다.
    ctx = {}                                   # post_id → (exclude, known, taken)
    for rid, post_id, market, product, body, attrs in rows:
        if post_id in ctx:
            continue
        mkt = market or _market_from_caption(kb, body)
        excl = _tag_exclusions(mkt) if mkt else all_excl   # 마켓 미상이면 전 마켓 태그를 뺀다(페일세이프)
        ctx[post_id] = (excl, known.get(mkt, ()), [])
    for _rid, post_id, _mk, product, body, _at in rows:
        excl, kn, taken = ctx[post_id]
        if extract.resolve_product_name(product, body, exclude=excl, known_products=kn)[1] == "keep":
            taken.append(product)

    # 2) 행마다 목표 제품명 판정
    plan: list[dict] = []
    for rid, post_id, market, product, body, attrs in rows:
        excl, kn, taken = ctx[post_id]
        target, why = extract.resolve_product_name(
            product, body, exclude=excl, known_products=kn, taken=taken)
        plan.append({"id": rid, "post_id": post_id, "market": market, "from": product,
                     "to": target, "why": why, "score": extract._filled_score(attrs or {})})

    # 3) 제약과 같은 키로 접기 — 같은 조각의 같은 제품은 한 행(AC3, 이중 계상 방지)
    groups: dict[tuple, list[dict]] = {}
    for p in plan:
        if p["to"] is None:
            continue                                       # 보류분은 접지 않는다
        groups.setdefault((p["post_id"], p["to"]), []).append(p)
    drops: list[dict] = []
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (-m["score"], m["id"]))  # 많이 찬 쪽을 남긴다
        drops.extend(members[1:])
    drop_ids = {d["id"] for d in drops}
    # 쓰기가 필요한 행 = 이름이 바뀌는 행 + 보류로 비우는 행. 둘 다 UPDATE 지만 성격이 달라
    # 보고는 나눈다 — 보류는 '고쳤다'가 아니라 '모른다고 표시했다'이다.
    writes = [p for p in plan if p["id"] not in drop_ids and p["to"] != p["from"]]
    renames = [p for p in writes if p["to"] is not None]
    holds = [p for p in writes if p["to"] is None]

    def _brief(p, keys):
        return {k: p[k] for k in keys}

    out = {"dry_run": dry_run, "scanned": len(plan), "writes": len(writes),
           "renames": len(renames), "folds": len(drops), "holds": len(holds),
           "unchanged": sum(1 for p in plan if p["to"] == p["from"]),
           "by_reason": {w: sum(1 for p in plan if p["why"] == w) for w in
                         sorted({p["why"] for p in plan})},
           "rename_list": [_brief(r, ("id", "post_id", "market", "from", "to", "why"))
                           for r in renames],
           "hold_list": [_brief(h, ("id", "post_id", "market", "from", "why")) for h in holds],
           "fold_list": [_brief(d, ("id", "post_id", "from", "to")) for d in drops]}
    if dry_run:
        log.info("repair_product_attribution(dry): %s", {k: out[k] for k in
                 ("scanned", "renames", "folds", "holds", "unchanged")})
        return out

    # 4) 적용 — 접기 삭제가 **먼저**다. 나중에 하면 이름 변경이 제약 위반으로 죽는다.
    changed = 0
    with connect() as conn:
        with conn.cursor() as cur:
            if drop_ids:
                cur.execute("DELETE FROM reviews WHERE id = ANY(%s)", (list(drop_ids),))
            for r in writes:
                row = cur.execute("SELECT market, attributes FROM reviews WHERE id=%s",
                                  (r["id"],)).fetchone()
                if row is None:
                    continue
                mkt, attrs = row
                text = index.render_review(mkt, r["to"], attrs or {})
                vec = index.embed([text])[0]
                cur.execute(
                    "UPDATE reviews SET product=%s, evidence=%s, tokens=%s, embedding=%s "
                    "WHERE id=%s",
                    (r["to"], text, index._tokenize(text), vec, r["id"]))
                changed += 1
        conn.commit()
    out.update({"deleted": len(drop_ids), "updated": changed})
    log.info("repair_product_attribution 완료: %s", {k: out[k] for k in
             ("scanned", "updated", "deleted", "holds")})
    return out


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


# 커뮤니티 리뷰 패널 정렬 — **값이 실제로 채워진 축으로만** 만든다.
# 디자인의 좋아요/조회/추천순은 이제 컬럼이 있고(ADR-0013 `post_columns`) `list_reviews` 도
# 반환하지만, 그 컬럼이 생기기 전에 색인된 행은 값이 NULL 이다.
# 절반이 NULL 인 축을 '추천순'이라 부르면 정렬을 누른 사용자에게 거짓말이 되므로, 값을
# 채운 뒤 여기 dict 에 한 줄 추가하는 것으로 켠다.
# ⚠️ **그냥 재수집해서는 안 채워진다**(2026-08-07 정정). 예전 주석은 '수집은 post_id 존재 시
# 스킵'이라 적어 재수집이 막히는 것처럼 읽혔지만 실제로는 맨 INSERT 라 **중복만 쌓였고**,
# 지금은 `UNIQUE(source,post_id,product)` + `ON CONFLICT DO NOTHING` 이라 **정말 스킵된다**.
# 어느 쪽이든 기존 행의 NULL 은 그대로다 — 무엇을 덮는지 명시하는 별도 백필이 필요하다.
# ⚠️ '최근 수집순'은 작성일이 아니라 **수집일**(reviews.created_at) 기준이다. 작성일은
# `posted_at` 이 따로 갖고 있으니, 정렬을 켤 때 둘을 섞지 말 것 — 없는 걸 '최신순'이라
# 부르지 않으려고 이름·화면 라벨 양쪽에 '수집'을 남겼다.
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

    ⚠️ `evidence` 는 **원문이 아니라 근거 스니펫**(~15자)이다. 원문 발췌인 `body` 와 별개 필드다.

    📌 `body` 는 ADR-0013 §3 의 **서버 발췌**다(`source_links.excerpt`). 저장은 전문
       (`index.post_columns`)이고 자르는 곳은 **여기 하나**다 — 전문을 반환하고 프런트에서
       `line-clamp` 로 접으면 전문이 이미 브라우저에 도달한 것이라 발췌가 아니다. 자르는 자리를
       옮기거나 늘리지 말 것. 공개 전환 시 길이를 줄이는 스위치도 같은 자리다(ADR-0013 §5).

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

    ⚠️ **제품 축만** 만든다(ADR-0015) — 질감·향·소리·지속력 + 장단점. 고객 응대·배송은
       `generate_market_summaries(market)` 가 마켓당 한 번 따로 만든다.
    """
    view = consolidated_for(market, product, with_summary=True)
    # 기준별 건수 스냅샷을 요약과 **같은 행에** 저장한다. 화면은 이제 이 값을 그리지 않지만
    # (배지 철회, ADR-0014), 요약 문장의 '다수/소수'가 **어느 표본에서 나왔는지**의 기록이다 —
    # 나중에 후기가 늘면 실시간 집계와 달라지므로, 그때 그 문장의 근거는 여기에만 남는다.
    payload = {**(view.get("review_summaries") or {}),
               "criterion_stats": view.get("criterion_stats") or {}}
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
    _store_summary(market, product, payload, n)
    log.info("요약 생성·저장(제품 축): %s/%s (근거 %d건)", market, product, n)
    return payload


# 요약 행은 두 종류다(ADR-0015): product IS NOT NULL = 제품 축, product IS NULL = 주문 축.
# PK 가 아니라 **부분 유니크 인덱스** 둘이라(NULL 은 PK 에 못 들어간다) upsert 도 갈린다 —
# ON CONFLICT 의 추론에 인덱스의 WHERE 절을 그대로 붙여야 맞는 인덱스를 고른다.
_UPSERT_PRODUCT = """
    INSERT INTO review_summaries (market, product, payload, model, n_reviews, generated_at)
    VALUES (%s,%s,%s,%s,%s, now())
    ON CONFLICT (market, product) WHERE product IS NOT NULL DO UPDATE SET
      payload=EXCLUDED.payload, model=EXCLUDED.model,
      n_reviews=EXCLUDED.n_reviews, generated_at=now()
"""
_UPSERT_MARKET = """
    INSERT INTO review_summaries (market, product, payload, model, n_reviews, generated_at)
    VALUES (%s,NULL,%s,%s,%s, now())
    ON CONFLICT (market) WHERE product IS NULL DO UPDATE SET
      payload=EXCLUDED.payload, model=EXCLUDED.model,
      n_reviews=EXCLUDED.n_reviews, generated_at=now()
"""


def _store_summary(market: str, product: str | None, payload: dict, n: int) -> None:
    """요약 한 행 upsert. `product=None` 이면 마켓 단위(주문 축) 행."""
    from psycopg.types.json import Jsonb
    args = ((market, product, Jsonb(payload), settings.model_judge, n) if product
            else (market, Jsonb(payload), settings.model_judge, n))
    with connect() as conn:
        conn.execute(_UPSERT_PRODUCT if product else _UPSERT_MARKET, args)
        conn.commit()


def generate_market_summaries(market: str) -> dict:
    """마켓 하나의 **주문 축** 요약(고객 응대·배송)을 생성해 저장한다(ADR-0015).

    마켓당 **한 번**이면 된다 — 그 마켓의 모든 제품 페이지가 이 한 벌을 빌려 쓴다.
    제품마다 만들던 예전 방식은 같은 주문의 배송 사실을 제품 수만큼 되풀이했고, 비용도
    제품 수만큼 들었다.

    ⚠️ 유료 호출이다(멱등 upsert). ⚠️ `market` 은 DB 마켓 키다 — 화면 표시명이 아니다.
    근거 0건이면 저장하지 않고 예외를 낸다(제품 축과 같은 이유 — 빈 payload 를 저장하면
    화면은 '요약 있음'으로 읽고 영영 빈칸이 뜬다).
    """
    view = order_view_for(market, with_summary=True)
    payload = {**(view.get("review_summaries") or {}),
               "criterion_stats": view.get("criterion_stats") or {}}
    n = view.get("n_orders") or 0
    if n == 0:
        raise ValueError(
            f"'{market}' 에 주문(배송·응대) 근거가 0건이라 요약을 저장하지 않았다 — "
            f"마켓 키가 맞는지 확인할 것(DB 키이고 화면 표시명이 아니다). "
            f"실재 키: {list_markets()}")
    _store_summary(market, None, payload, n)
    log.info("요약 생성·저장(주문 축): %s (근거 %d건)", market, n)
    return payload


def stored_summaries(market: str, product: str) -> dict | None:
    """저장된 **제품 축** 요약 → `{payload, model, n_reviews, generated_at}`. 없으면 None.

    화면은 **이 함수만** 쓴다. 여기서 없다고 생성으로 넘어가면 결국 로드마다 과금된다.
    """
    return _fetch_summary("product IS NOT NULL AND product=%s", [market, product])


def stored_market_summaries(market: str) -> dict | None:
    """저장된 **주문 축**(고객 응대·배송) 요약. 없으면 None(=아직 미생성).

    없을 때 제품 축 행으로 폴백하는 건 **화면 쪽**(`api.main`) 일이다 — 구 payload 에 섞여
    있던 cs·shipping 을 읽을지 말지는 표시 정책이고, 저장소는 없는 걸 없다고 답한다.
    """
    return _fetch_summary("product IS NULL", [market])


def _fetch_summary(cond: str, params: list) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, model, n_reviews, generated_at FROM review_summaries "
            f"WHERE market=%s AND {cond}", params).fetchone()   # cond 는 상수, 사용자 입력 아님
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


def order_view_for(market: str, *, with_summary: bool = True) -> dict:
    """마켓 하나의 **주문 경험** 뷰 — 고객 응대·배송(ADR-0015).

    `consolidated_for_market` 과 같은 행을 읽지만 축이 다르다: 저건 제품 평가를 마켓 범위로
    모아 보는 것이고, 이건 애초에 마켓에만 귀속되는 사실을 마켓당 한 벌 만드는 것이다.
    팬아웃 복제분은 `build_order_view` 안에서 조각 단위로 접힌다 — 여기서 미리 거를 필요 없다.

    범위 주의(ADR-0007): 수집이 제품 앵커라 이 뷰의 모집단도 '이 마켓에서 추적 중인 제품들의
    후기'다. 마켓의 모든 주문이 아니다 — UI 라벨도 그 범위로 표기할 것.
    """
    with connect() as conn:
        records = _records_for(conn, market, None)
    sectionize = None
    if with_summary and records:
        sectionize = lambda prompt, schema: LLM().complete(
            prompt, model=settings.model_judge, schema=schema, label="consolidated.order")
    return cv.build_order_view(market, records, llm_sectionize=sectionize)


# ---------------------------------------------------------------- 변경분 요약 갱신
# ADR-0015(축 분리) 이전에 저장된 payload 는 여섯 기준이 전부 제품 행에 들어 있다 — 형태가
# 다르므로 근거가 안 늘었어도 재생성 대상이다. tz-aware 로 둔다: `generated_at` 이 aware 라
# naive 상수와 비교하면 TypeError 가 난다(라이브 확인 2026-08-07 — `Etc/UTC` aware 로 온다).
ADR_0015_CUTOFF = datetime(2026, 8, 7, tzinfo=timezone.utc)

# 요약 1건당 실측 호출 수(제품 축 3소스 + 서포터 버킷) × gpt-5.4 호출당 평균 비용.
# 어림값이라 `est_cost_usd` 는 **결정 재료**지 청구서가 아니다 — 사용자가 보고 dry_run 을 푼다.
_CALLS_PER_SUMMARY = 3.3
_USD_PER_CALL = 0.0072


def _generated_at(row: dict) -> datetime | None:
    """저장 요약의 생성 시각 → tz-aware datetime. 못 읽으면 None.

    `_fetch_summary` 가 ISO 문자열로 돌려주므로 여기서 파싱한다. naive 로 들어오면 UTC 로
    간주해 aware 로 올린다 — 커토프가 aware 라 섞으면 비교 자체가 TypeError 다.
    """
    raw = row.get("generated_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_stale(row: dict | None, current: int, min_delta: int) -> bool:
    """이 요약을 다시 만들어야 하는가. **무료 판정** — LLM 을 부르지 않는다.

    네 가지만 본다: 미생성 / 근거가 `min_delta` 이상 늘었다 / 다른 모델로 만들었다 /
    ADR-0015 이전 payload 형태다. 생성 시각을 못 읽으면 **오래된 것으로 본다** — 모르는 걸
    최신으로 치면 옛 형태 payload 가 영영 안 갱신된다.
    """
    if row is None:
        return True                                        # 미생성
    if current - (row.get("n_reviews") or 0) >= min_delta:
        return True                                        # 근거가 늘었다
    if row.get("model") != settings.model_judge:
        return True                                        # 모델 교체
    gen = _generated_at(row)
    return gen is None or gen < ADR_0015_CUTOFF            # 구 payload 형태


def refresh_stale_summaries(*, min_delta: int = 3, min_evidence: int = 3,
                            dry_run: bool = True) -> dict:
    """근거가 늘어난 요약만 재생성한다. **판정은 무료, 생성만 유료.**

    ⚠️ `dry_run=False` 는 유료다 — 대상 수 × 약 3.3 콜. 기본값이 `True` 인 이유이고,
      `est_cost_usd` 를 함께 돌려주는 이유다(계획 R7: 사용자가 보고 결정한다).

    **왜 개수가 유효한 워터마크인가:** 색인이 `ON CONFLICT DO NOTHING` 이라 행은 늘기만 한다.
    줄어들 길은 수동 삭제뿐이다.

    **왜 SQL 로 세지 않는가:** 두 축의 '개수' 정의가 다르다. `n_reviews` 는 행 수지만
    `n_orders` 는 팬아웃을 접은 **조각 수**다(`_fold_orders`). 주문 축을 `count(*)` 로 세면
    "배송 후기 27건"처럼 부풀려진다. `with_summary=False` 는 `llm_sectionize=None` 으로 떨어져
    LLM 을 한 번도 안 부르면서 화면과 **같은 계산**을 한다 — 그래서 재구현하지 않는다.

    **왜 `specs` 기준으로 열거하는가:** 후기 쪽 `(market, product)` 조합 중 83개가 스펙에 없다
    (실측). 그 안엔 유령(풀조합·향료), 별칭, 개인 태그가 섞여 있고 지금은 가릴 방법이 없다.
    스펙 없는 제품에 요약을 만들지 않는 것이 **유령에 유료 요약을 쓰지 않는 자동 필터**다.
    대가로 판매자가 실제로 파는데 1층이 아직 못 모은 제품도 요약이 없다 — 해결은 '판매자 제품
    목록 먼저' 과제와 같은 자리이고 여기서는 의도적으로 미해결이다.
    """
    stale: list[dict] = []
    for market in list_markets():
        for p in list_products(market):
            product = p["product"]
            now = consolidated_for(market, product, with_summary=False).get("n_reviews") or 0
            if now < min_evidence:
                continue                                   # 근거 빈약 → 요약을 만들지 않는다
            if _is_stale(stored_summaries(market, product), now, min_delta):
                stale.append({"market": market, "product": product, "n": now, "axis": "product"})
        now_o = order_view_for(market, with_summary=False).get("n_orders") or 0
        if now_o >= min_evidence and _is_stale(stored_market_summaries(market), now_o, min_delta):
            stale.append({"market": market, "product": None, "n": now_o, "axis": "order"})

    if dry_run:
        out = {"stale": stale, "count": len(stale), "dry_run": True,
               "est_cost_usd": round(len(stale) * _CALLS_PER_SUMMARY * _USD_PER_CALL, 4)}
        log.info("refresh_stale_summaries(dry): %d건 · 예상 $%.4f", out["count"], out["est_cost_usd"])
        return out

    for s in stale:
        if s["product"]:
            generate_summaries(s["market"], s["product"])
        else:
            generate_market_summaries(s["market"])
    log.info("refresh_stale_summaries: %d건 재생성", len(stale))
    return {"regenerated": len(stale), "stale": stale, "dry_run": False}


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
