# -*- coding: utf-8 -*-
"""
1층 공식 스펙 — fixture 로더 / KB 시드 (Phase 2 보강).

배경(2026-06-29 결정): Instagram Graph API `business_discovery` 는 앱이 개발 모드여도
advanced access(=App Review 통과)를 요구한다. 비즈 인증·스크린캐스트·수주 심사는 포트폴리오
데모 범위 밖이라, 1층 공식 스펙은 **큐레이션 fixture**(`data/layer1_fixture.json`)로 주입한다.
수집 *설계*(business_discovery 인터페이스)는 `sources.InstagramSource` 에 그대로 남겨, 라이브/픽스처가
동일 코드 경로를 타게 한다. 이 모듈은 그 fixture 를 읽어:
  1) 원시 게시물(posts)을 RawReview(=1층 추출 입력)로 흘려보내거나,
  2) 손으로 시드한 products[] 를 KB 마켓에 병합(KB `products[]` 미시드 → 개체연결 제품매칭 보류 해소).

fixture 구조: { "markets": { "<handle>": { "posts": [...], "products": [...] } } }
  - posts[]   : business_discovery media.data 모양(caption/permalink/timestamp/media_type/id)
  - products[]: prompts/slime_rag_extraction_prompts.md 1층 스키마와 동일
'_' 로 시작하는 최상위/마켓 키(_note,_schema,_status,_sample)는 메타라 무시한다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator, Optional

from .config import settings

log = logging.getLogger("layer1")


# ---------------------------------------------------------------- 로드
def load_fixture(path: Optional[Path] = None) -> dict[str, dict]:
    """fixture → {handle: {"posts":[...], "products":[...]}}. 메타(_*) 키는 제거."""
    p = Path(path) if path else settings.layer1_fixture_path
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    markets = raw.get("markets", {})
    return {
        handle: {"posts": entry.get("posts", []), "products": entry.get("products", [])}
        for handle, entry in markets.items()
        if not handle.startswith("_")
    }


# ---------------------------------------------------------------- 원시 게시물 → RawReview
def iter_official_posts(fixture: dict[str, dict], handles: Optional[list[str]] = None):
    """fixture posts 를 (handle, post-dict) 로 순회. handles 주면 그 핸들만."""
    for handle, entry in fixture.items():
        if handles and handle not in handles:
            continue
        for post in entry.get("posts", []):
            yield handle, post


# ---------------------------------------------------------------- KB 시드
def seed_kb_products(markets: list[dict], fixture: Optional[dict[str, dict]] = None) -> int:
    """KB 마켓 리스트의 빈 products[] 를 fixture 의 손시드 products 로 채운다(in-place).

    매칭: 마켓의 handle 또는 handles_alt 가 fixture 키와 일치하면 병합.
    반환: 시드된 제품 총개수. (개체연결 제품매칭의 그라운드 트루스 공급)
    """
    fx = fixture if fixture is not None else load_fixture()
    seeded = 0
    for m in markets:
        keys = [m.get("handle"), *m.get("handles_alt", [])]
        prods: list[dict] = []
        for k in keys:
            if k and k in fx:
                # 제품에 source_permalink 가 없으면 같은 마켓 게시물 permalink 로 폴백(사용자 결정:
                # 공식 스펙 URL = 게시물 permalink 재사용). fixture 원본은 건드리지 않게 복사본에 주입.
                posts = fx[k].get("posts") or []
                fallback = posts[0].get("permalink") if posts else None
                for p in fx[k].get("products", []):
                    if not p.get("source_permalink") and fallback:
                        p = {**p, "source_permalink": fallback}
                    prods.append(p)
        if prods:
            m["products"] = prods
            seeded += len(prods)
            log.info("KB 시드: %s ← %d개 제품", m.get("market"), len(prods))
    return seeded


# ---------------------------------------------------------------- 1층 → specs 행
def iter_specs(kb_markets: list[dict]):
    """시드된 KB 마켓 → specs 테이블 행 튜플을 순회.

    (market_word, product, scent, base_combo, slime_type, beads, source_permalink) 를 내준다.
    market 은 정규 market_word(빈짱/봄/머머) — reviews.market(linking 결과)과 조인 키 일치.
    slime_type 은 TYPE_ENUM 배열을 콤마결합, 비면 type_other(마켓 고유 베이스어)로 폴백.
    beads 는 비즈/토핑 구성요소 리스트(오픈 어휘), 없으면 [].
    source_permalink 은 공식 스펙 출처 게시물 URL(seed_kb_products 가 게시물 permalink 로 폴백), 없으면 None.
    """
    for m in kb_markets:
        mw = m.get("market_word") or m.get("market")
        for p in m.get("products", []):
            stype = ", ".join(p.get("type") or []) or p.get("type_other")
            yield (mw, p["product_name"], p.get("official_scent"),
                   p.get("glue_composition"), stype, p.get("beads") or [],
                   p.get("source_permalink"))


# ---------------------------------------------------------------- 셀프테스트
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fx = load_fixture()
    print(f"fixture 마켓 {len(fx)}개")
    for handle, post in iter_official_posts(fx):
        print(f"  post  {handle}: {post['caption'].splitlines()[0]}  ({post['permalink']})")

    # KB(demo) 에 시드해 products[] 가 실제로 채워지는지 확인
    kb = json.loads(settings.kb_demo_path.read_text(encoding="utf-8"))
    n = seed_kb_products(kb["markets"], fx)
    filled = [m["market"] for m in kb["markets"] if m["products"]]
    print(f"시드된 제품 {n}개, products[] 채워진 마켓: {filled}")
