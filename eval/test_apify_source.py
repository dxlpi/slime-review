# -*- coding: utf-8 -*-
"""
ApifyHashtagSource 오프라인 매핑 테스트 — API 미호출/무비용.

`_run`(유일한 네트워크 경계)에 data/apify_hashtag_sample.json 을 주입해
캡션→RawReview 매핑·중복접기·저품질 드롭·limit·provenance 라벨을 검증한다.
샘플 파일이 실제 apify/instagram-hashtag-scraper 출력 필드 모양을 흉내내므로,
유료 플랜 없이도 매핑 회귀를 잡는다.

실행:  python -m eval.test_apify_source   (repo 루트에서)
"""
from __future__ import annotations
import json
from pathlib import Path

from slime_rag.sources import ApifyHashtagSource, collect_all
from slime_rag.config import settings

SAMPLE = Path(settings.kb_demo_path).parent / "apify_hashtag_sample.json"


def _sample_items() -> list[dict]:
    with open(SAMPLE, encoding="utf-8") as f:
        return json.load(f)["items"]


def _source_with_sample(**kw) -> ApifyHashtagSource:
    """_run 을 샘플 반환으로 대체한 소스(네트워크 경계 주입)."""
    src = ApifyHashtagSource(token="dummy", hashtags=["슬라임"], **kw)
    items = _sample_items()
    src._run = lambda hashtags: items          # seam: 실제 actor 호출 대체
    return src


def test_mapping_and_provenance():
    src = _source_with_sample()
    reviews = list(src.collect(keywords=[], limit=100))
    # 샘플 5건 중: 중복 1건 접힘 + 저품질('ㅋㅋ') + 빈캡션 드롭 → 2건 남아야
    assert len(reviews) == 2, f"기대 2건, 실제 {len(reviews)}"
    r = reviews[0]
    assert r.platform == "instagram"
    assert r.text.startswith("봄슬라임 허니푸냥이")
    assert r.url == "https://www.instagram.com/p/Cabc001/"
    assert r.meta["kind"] == "hashtag_caption"
    assert r.meta["source"] == "apify"
    assert r.meta["scraped"] is True
    assert r.meta["owner_username"] == "slime_lover_kr"
    assert r.meta["hashtag"] in ("봄슬라임", "슬라임후기")
    print("✓ 매핑·provenance OK (2건, source=apify/scraped=True)")


def test_dedup():
    src = _source_with_sample()
    reviews = list(src.collect(keywords=[], limit=100))
    shortcodes = [r.meta.get("shortcode") for r in reviews]
    assert len(shortcodes) == len(set(shortcodes)), "shortCode 중복이 접히지 않음"
    assert "Cabc002" in shortcodes and shortcodes.count("Cabc002") == 1
    print("✓ 중복 shortCode 접힘 OK")


def test_low_quality_and_empty_dropped():
    src = _source_with_sample()
    texts = [r.text for r in src.collect(keywords=[], limit=100)]
    assert "ㅋㅋ" not in texts, "저품질 캡션이 드롭되지 않음"
    assert "" not in texts, "빈 캡션이 드롭되지 않음"
    print("✓ 저품질/빈 캡션 드롭 OK")


def test_limit_respected():
    src = _source_with_sample()
    assert len(list(src.collect(keywords=[], limit=1))) == 1, "limit 미준수"
    print("✓ limit 준수 OK")


def test_resolve_hashtags_specific_product():
    """특정 제품 검색 → 제품명만. 마켓명/'슬라임' 광역어·초성단독은 미부착."""
    src = ApifyHashtagSource(token="dummy", hashtags=["봄슬라임"])
    tags = src._resolve_hashtags(["레몬커드쉘도넛", "ㅁㅁ"])   # 초성 단독은 제외
    assert tags == ["레몬커드쉘도넛"]                      # 제품명 그대로, 그것만
    assert "봄슬라임" not in tags                         # 큐레이션 마켓 태그 섞이지 않음
    assert "레몬커드쉘도넛슬라임" not in tags              # '슬라임' 접미어 안 붙음
    print("✓ 특정 제품 검색: 제품명만(마켓명·슬라임 접미어 없음, 초성 제외) OK")


def test_resolve_hashtags_no_keywords_broad():
    """검색어 없으면 큐레이션 마켓 태그로 광역 수집."""
    src = ApifyHashtagSource(token="dummy", hashtags=["봄슬라임", "머머슬라임"])
    assert src._resolve_hashtags([]) == ["봄슬라임", "머머슬라임"]
    print("✓ 검색어 없음: 큐레이션 마켓 태그 광역 수집 OK")


def test_token_unset_is_resilient():
    """토큰 없으면 예외 없이 빈 결과 — collect_all 회복력."""
    src = ApifyHashtagSource(token=None, hashtags=["슬라임"])
    out = collect_all([src], keywords=["봄"], per_source_limit=10)
    assert out == [], f"토큰 없을 때 빈 리스트 기대, 실제 {out}"
    print("✓ 토큰 미설정 시 예외 없이 [] (collect_all 회복력) OK")


def test_to_review_sets_review_class():
    """_to_review 가 캡션 자족으로 review_class(genuine/promo)를 라벨하는지."""
    src = ApifyHashtagSource(token="dummy", hashtags=["슬라임"])
    genuine = src._to_review({"caption": "봄슬라임 허니푸냥이 말랑 향 좋아요 재구매각",
                              "shortCode": "Cg1", "hashtags": ["봄슬라임"]})
    assert genuine.meta["review_class"] == "genuine"
    assert genuine.meta["promo_marker"] is None
    promo = src._to_review({"caption": "서포터즈로 무상 제공받은 슬라임 후기 말랑좋음",
                            "shortCode": "Cp1", "hashtags": ["슬라임"]})
    assert promo.meta["review_class"] == "promo"
    assert promo.meta["promo_marker"] == "서포터즈"
    print("✓ _to_review review_class(genuine/promo) 라벨 OK")


def test_curated_hashtags_load():
    """data/ig_hashtags.json 로드 → by_market 평탄화(global 폐기)."""
    tags = ApifyHashtagSource._load_hashtags()
    assert "봄슬라임" in tags, "by_market 마켓 태그가 로드되지 않음"
    assert "슬라임" not in tags, "global 광역어가 아직 로드됨(폐기됐어야)"
    assert all(not t.startswith("#") for t in tags), "'#' 접두가 남아있음"
    assert len(tags) == len(set(tags)), "중복 태그가 남아있음"
    print(f"✓ 큐레이션 해시태그 로드 OK (by_market {len(tags)}개, global 없음)")


if __name__ == "__main__":
    test_mapping_and_provenance()
    test_dedup()
    test_low_quality_and_empty_dropped()
    test_limit_respected()
    test_resolve_hashtags_specific_product()
    test_resolve_hashtags_no_keywords_broad()
    test_token_unset_is_resilient()
    test_to_review_sets_review_class()
    test_curated_hashtags_load()
    print("\n모든 오프라인 테스트 통과 ✅")
