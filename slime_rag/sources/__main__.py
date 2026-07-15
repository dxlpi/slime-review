# -*- coding: utf-8 -*-
"""수집 레이어 CLI 데모 — `python -m slime_rag.sources [해시태그...]`.

인자로 해시태그를 주면 그것들로 애드혹 인스타 검색(디시 데모는 스킵).
  예) python -m slime_rag.sources 머머슬라임 레몬커드쉘도넛
인자가 없으면 기본 데모(디시 수집 + #슬라임후기 스모크).
"""

import sys
import logging

from ..config import settings
from . import ApifyHashtagSource, DCInsideSource, collect_all


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    cli_tags = [t.lstrip("#") for t in sys.argv[1:] if t.strip()]

    if cli_tags:
        if not settings.apify_token:
            print("[apify] APIFY_TOKEN 미설정 → 해시태그 검색 불가. "
                  "APIFY_TOKEN=... 를 앞에 붙이거나 .env 에 설정하라.")
            return 1
        print(f"[apify] 해시태그 검색: {', '.join('#'+t for t in cli_tags)}")
        ig = ApifyHashtagSource(token=settings.apify_token, hashtags=cli_tags,
                                results_per_hashtag=settings.apify_results_per_hashtag)
        ig_reviews = collect_all([ig], keywords=[], per_source_limit=30)
        print(f"[apify] 수집 {len(ig_reviews)}건\n")
        # 편향 태깅: 판매자(KB 핸들 일치) / 홍보성(대가·무상 제공) / 실사용 을 즉시 표시.
        # 홍보성은 LLM 의미 판정(키가 있으면) — '서포터' 인용문 오탐/표현 미탐을 피한다.
        import os
        from ..bias import classify, seller_index, make_gated_llm_promo_detector
        from ..linking import load_kb
        sellers = seller_index(load_kb())
        promo_detector = None
        if os.getenv("OPENAI_API_KEY"):
            from ..llm_ops import LLM
            promo_detector = make_gated_llm_promo_detector(LLM(), settings.model_extract)
            print("[bias] 홍보성 판정: 게이트(recall)→LLM(verdict) 캐스케이드 "
                  "— 순수 실사용은 LLM 없이 단락\n")
        else:
            print("[bias] 홍보성 판정: 키워드 seed(폴백) — OPENAI_API_KEY 없음\n")
        for r in ig_reviews:
            cap = " ".join(r.text.split())        # 개행 접어 한 줄로
            v = classify(r, sellers, promo_detector=promo_detector)
            if v.category == "seller":
                tag = f"[판매자·{v.market}→1층]"
            elif v.category == "promo":
                tag = f"[홍보성·{v.marker}]"
            else:
                tag = "[실사용]"
            print(f"{tag} @{r.meta.get('owner_username')}  #{r.meta.get('hashtag')}"
                  f"  ♥{r.meta.get('likes')}  {r.url}\n  {cap[:120]}")
        if not ig_reviews:                        # 반환은 됐는데 0건이면 필드명/커버리지 진단
            raw = ig._run(cli_tags)
            if raw:
                print("[apify] ⚠️ 반환은 됐으나 0건 매핑 — 첫 아이템 키:", sorted(raw[0].keys()))
            else:
                print("[apify] 해당 해시태그에 공개 게시물이 없거나 스크랩 0건.")
        return 0

    # --- 기본 데모 ---
    # 디시: 라이브 검증(2026-06-17) 본문+댓글 수집·댓글 AJAX 정상.
    # 주의: 너무 구체적인 제품명("빈짱 연유스무디")은 그 갤에 글이 없어 0건이 정상.
    dc = DCInsideSource(gallery_id="amos")
    reviews = collect_all([dc], keywords=["슬라임"], per_source_limit=20)
    print(f"[dcinside] 수집 {len(reviews)}건")
    for r in reviews[:3]:
        print(r.platform, r.url, r.text[:60])

    # 라이브 스모크(게이트): APIFY_TOKEN 있을 때만 #슬라임후기 1회 수집.
    if settings.apify_token:
        print("\n[apify] 라이브 해시태그 스모크 테스트")
        ig = ApifyHashtagSource(token=settings.apify_token,
                                hashtags=["슬라임후기"],   # 단일 태그로 저비용 확인
                                results_per_hashtag=settings.apify_results_per_hashtag)
        ig_reviews = collect_all([ig], keywords=[], per_source_limit=10)
        print(f"[apify] 수집 {len(ig_reviews)}건")
        for r in ig_reviews[:3]:
            print(r.meta.get("owner_username"), r.meta.get("hashtag"), "|", r.text[:60])
    else:
        print("\n[apify] APIFY_TOKEN 미설정 → 라이브 스모크 스킵 "
              "(사용법: python -m slime_rag.sources 머머슬라임 레몬커드쉘도넛)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
