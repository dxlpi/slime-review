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

from slime_rag.sources import ApifyHashtagSource, InstagramProfileSource, collect_all
from slime_rag.config import settings

SAMPLE = Path(settings.kb_demo_path).parent / "apify_hashtag_sample.json"
# 라이브 프로필 스크랩 1회분 스냅샷(@from.murmurslime, 2026-07-15) — 로고 매핑 검증용.
PROFILE_SAMPLE = Path(settings.kb_demo_path).parent / "apify_profile_murmur_raw.json"


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


def test_curated_hashtags_cover_every_kb_market():
    """`by_market` 키가 KB 마켓 전부를 덮어야 한다.

    회귀 근거(2026-08-07): `모모찌` 키가 빠져 있어 검색어 없는 광역 수집이 그 마켓 태그를
    **한 번도 요청하지 않았다**. 개수를 상수로 박아 두면(예전엔 13) 마켓이 늘 때 같은 구멍이
    다시 생기고, 요청을 안 했다는 사실은 0건과 구분되지 않아 조용하다.

    ⚠️ 대상은 **`handle` 이 있는 마켓**뿐이다(2026-08-11). KB 가 14→38마켓이 되면서
      **이름만 등록되고 `handle` 이 빈** 항목이 24개 생겼다 — 감사가 아모스갤에서 마켓 자리로
      쓰인다고 지목한 초성들이고, 실명은 알아냈지만 IG 계정은 아직 모른다.
      그 마켓들은 `pipeline._seller_targets` 가 경고와 함께 건너뛰므로 **수집 대상이 아니고**,
      해시태그를 지어내면 실재하지 않는 태그를 요청하게 된다(1급 규칙: 모르는 건 안 만든다).
      ⛔ 이 필터를 '전 마켓'으로 되돌리지 말 것 — 되돌리려면 핸들을 먼저 채워야 한다.
        핸들이 채워지는 순간 `want` 가 자동으로 늘어 이 게이트가 다시 구멍을 잡는다.
    """
    import json
    from slime_rag import linking

    doc = json.loads(settings.ig_hashtags_path.read_text(encoding="utf-8"))
    have = set(doc["by_market"])
    markets = linking.load_kb().markets
    want = {m["market_word"] for m in markets if m.get("market_word") and m.get("handle")}
    no_handle = {m["market_word"] for m in markets if not m.get("handle")}
    assert not (want - have), f"by_market 에 빠진 마켓: {sorted(want - have)}"
    assert not (have - want - no_handle), \
        f"KB 에 없는 by_market 키(오타 의심): {sorted(have - want - no_handle)}"
    print(f"✓ 큐레이션 해시태그가 수집 가능한 KB 마켓 {len(want)}개 전부 덮음 OK "
          f"(핸들 미상 {len(no_handle)}개는 수집 대상 밖)")


def test_fetch_profiles_mapping():
    """프로필 레벨 매핑(ADR-0010 로고) — 실제 액터 출력 스냅샷으로 무비용 검증.

    `data/apify_profile_murmur_raw.json` 이 존재하는 이유가 정확히 이것이다: 라이브
    프로필 스크랩 1회분을 떠 뒀기 때문에 유료 호출 없이 필드 모양 회귀를 잡는다.
    """
    raw = json.loads(PROFILE_SAMPLE.read_text(encoding="utf-8"))
    src = InstagramProfileSource(token="dummy")
    src._run = lambda usernames: raw["items"]        # seam: 실제 actor 호출 대체

    out = src.fetch_profiles(["@from.murmurslime"])  # '@' 접두 허용 확인
    assert len(out) == 1, f"기대 1건, 실제 {len(out)}"
    p = out[0]
    assert p["username"] == "from.murmurslime"
    assert p["full_name"], "fullName 이 비었다"
    # HD(320px)가 있으면 반드시 그쪽 — ADR-0010 이 정한 해상도다.
    assert p["profile_pic_url"] == raw["items"][0]["profilePicUrlHD"], "HD 우선이 깨졌다"
    assert p["profile_pic_url"].startswith("https://"), "URL 이 아니다"

    # HD 없으면 150px 폴백, 둘 다 없으면 무음 드롭이 아니라 결손으로 빠진다
    item = dict(raw["items"][0])
    item.pop("profilePicUrlHD")
    src._run = lambda usernames: [item]
    assert src.fetch_profiles(["x"])[0]["profile_pic_url"] == item["profilePicUrl"], "폴백 실패"
    item2 = {k: v for k, v in item.items() if k != "profilePicUrl"}
    src._run = lambda usernames: [item2]
    assert src.fetch_profiles(["x"]) == [], "사진 URL 없는 항목이 통과했다"

    # 토큰 없으면 조용히 [] (기존 회복력 계약 상속 — 새 네트워크 경계를 만들지 않았다는 증거)
    assert InstagramProfileSource(token=None).fetch_profiles(["x"]) == []
    print(f"✓ fetch_profiles: HD 우선·폴백·결손 드롭·토큰없음 회복력 OK ({p['username']})")


def test_fetch_profiles_ignores_post_media():
    """게시물 미디어는 로고 경로로 절대 새지 않는다 — ADR-0009 §4 는 무변경이다."""
    raw = json.loads(PROFILE_SAMPLE.read_text(encoding="utf-8"))
    src = InstagramProfileSource(token="dummy")
    src._run = lambda usernames: raw["items"]
    keys = set().union(*(set(p) for p in src.fetch_profiles(["from.murmurslime"])))
    assert keys == {"username", "profile_pic_url", "full_name"}, \
        f"프로필 매핑이 계약 밖 필드를 흘린다: {keys}"
    # 액터 응답엔 latestPosts[] 와 relatedProfiles[](타 계정 아바타)가 들어 있다 — 둘 다 미유출
    assert "latestPosts" in raw["items"][0] and "relatedProfiles" in raw["items"][0], \
        "스냅샷이 바뀌어 이 테스트의 전제가 사라졌다"
    print("✓ fetch_profiles: 게시물 미디어·타 계정 아바타 미유출 OK")


# ---------------------------------------------------------------- URL 직접 수집(ApifyPostUrlSource)
# 캡션은 **합성**이다(라이브 필드 모양만 흉내). 수집물 바이트는 git 에 넣지 않는다 —
# ADR-0013/ADR-0010 의 예외는 마켓 로고뿐이다.
POSTURL_ITEMS = [
    {"shortCode": "AAA111", "ownerUsername": "tester_a",
     "hashtags": ["깡수박화채", "빈짱슬라임", "slime"],
     "likesCount": 12, "commentsCount": 1,
     "timestamp": "2026-07-23T09:18:45.000Z",
     "url": "https://www.instagram.com/p/AAA111/",
     "caption": "#빈짱슬라임 #깡수박화채\n\n물젤리 촉감 진짜 좋아요 향도 은은하고 재구매각"},
    {"shortCode": "BBB222", "ownerUsername": "tester_b",
     "hashtags": ["깡수박화채", "slime"],
     "likesCount": 3, "commentsCount": 0,
     "timestamp": "2026-07-28T09:46:13.000Z",
     "url": "https://www.instagram.com/p/BBB222/",
     "caption": "#깡수박화채 #빈짱슬라임 #슬라임 #slime #slimevideos"},
]


def _posturl_source_with_sample():
    from slime_rag.sources import ApifyPostUrlSource
    src = ApifyPostUrlSource(token="dummy")
    src._run = lambda urls: list(POSTURL_ITEMS)    # seam: 실제 actor 호출 대체
    return src


def test_posturl_shares_hashtag_mapping():
    """URL 액터와 해시태그 액터는 **같은 매핑 한 벌**을 쓴다(필드명이 같다 — 라이브 확인).
    meta 모양이 갈리면 하류(build_source_ref·index_post·편향 라벨)가 소스마다 다른 걸 받는다."""
    src = _posturl_source_with_sample()
    reviews = list(src.collect(["https://www.instagram.com/p/AAA111/",
                                "https://www.instagram.com/p/BBB222/"], limit=100))
    assert len(reviews) == 2, f"2건이어야: {len(reviews)}"
    r = reviews[0]
    assert r.platform == "instagram"
    assert r.meta["source"] == "apify" and r.meta["scraped"] is True, "provenance 라벨 누락"
    assert r.meta["shortcode"] == "AAA111" and r.meta["owner_username"] == "tester_a"
    assert r.meta["hashtags"] == ["깡수박화채", "빈짱슬라임", "slime"]
    assert r.meta["review_class"] == "genuine"
    # 해시태그 소스가 만드는 meta 키 집합과 동일해야 한다
    hash_keys = set(_source_with_sample()._to_review(_sample_items()[0]).meta)
    assert set(r.meta) == hash_keys, f"meta 키가 소스마다 갈린다: {set(r.meta) ^ hash_keys}"
    print("✓ URL 소스: 해시태그 소스와 매핑·provenance 한 벌 OK")


def test_posturl_never_uses_url_as_relevance_anchor():
    """**URL 을 관련성 앵커로 흘리면 안 된다.** `resolve_target` 은 keywords[0] 을 slime 으로
    폴백하므로, URL 을 그대로 넘기면 `slime="https://..."` 앵커가 생겨 코사인 점수가 통째로
    무의미해진다. 앵커는 `target` 으로만 온다."""
    import slime_rag.sources.apify as apify_mod

    captured = {}

    class _RecordingGate:
        def __init__(self, platform, target, keywords, limit, log):
            captured.update(platform=platform, target=target,
                            keywords=keywords, limit=limit)

        def filter(self, items):
            return iter(items)

        def finish(self):
            pass

    original = apify_mod.RelevanceGate
    apify_mod.RelevanceGate = _RecordingGate
    try:
        src = _posturl_source_with_sample()
        list(src.collect(["https://www.instagram.com/p/AAA111/"], limit=7))
    finally:
        apify_mod.RelevanceGate = original

    assert captured["keywords"] == [], \
        f"URL 이 관련성 앵커로 샜다: {captured['keywords']}"
    assert captured["target"] is None, "target 미주입인데 앵커가 생겼다"
    assert captured["limit"] == 7, "limit 이 게이트에 전달되지 않는다"
    print("✓ URL 소스: URL 을 관련성 앵커로 쓰지 않음 OK")


def test_posturl_dedup_and_resilience():
    """같은 URL 중복 접기 + 토큰 미설정 회복력(다른 소스와 동일 계약)."""
    from slime_rag.sources import ApifyPostUrlSource
    src = ApifyPostUrlSource(token="dummy")
    src._run = lambda urls: [POSTURL_ITEMS[0], dict(POSTURL_ITEMS[0])]
    assert len(list(src.collect(["https://www.instagram.com/p/AAA111/"] * 2))) == 1, \
        "중복 shortCode 가 안 접힌다"
    bare = ApifyPostUrlSource(token=None)
    assert list(bare.collect(["https://www.instagram.com/p/AAA111/"])) == [], \
        "토큰 없을 때 예외 없이 [] 여야(collect_all 회복력)"
    print("✓ URL 소스: 중복 접힘·토큰없음 회복력 OK")


def test_posturl_keeps_hashtag_only_caption():
    """해시태그만 있는 캡션은 **수집 단계에서 살아남는다**. 드롭은 여기가 아니라 추출이
    `reviews: []` 를 내는 자리에서 일어난다 — 두 자리를 헷갈리면 '수집 실패'로 오진한다."""
    src = _posturl_source_with_sample()
    reviews = list(src.collect(["https://www.instagram.com/p/BBB222/"], limit=100))
    assert any(r.meta["shortcode"] == "BBB222" for r in reviews), \
        "해시태그만 있는 캡션이 수집 단계에서 잘못 드롭됐다"
    print("✓ URL 소스: 해시태그뿐인 캡션은 수집 단계 통과(드롭은 추출 몫) OK")


# ---------------------------------------------------------------- 1층 피드 전량(instagram-scraper)
# 실제 apify/instagram-scraper 출력 32건(2026-08-06 스냅샷). 손작성 픽스처가 아니라
# **액터가 실제로 준 필드 모양**이라, 매핑 회귀를 유료 런 없이 진짜 payload 로 잡는다.
FEED_SAMPLE = Path(settings.kb_demo_path).parent / "apify_posts_backfill_raw.json"


def _feed_items() -> list[dict] | None:
    """실 payload 32건. **없으면 None** — 호출부가 눈에 보이게 스킵한다.

    ⚠️ 이 파일은 `.gitignore` 의 `data/apify_posts_*_raw.json` 에 걸려 **클론엔 없다**
      (수집 바이트 미커밋 — ADR-0013). 작성자 머신에선 열리고 CI 에선 `FileNotFoundError` 로
      죽는 구조라, 로컬 통과가 CI 통과를 뜻하지 않았다(실제로 그렇게 깨졌다).
      **Don't:** 통과시키려고 샘플을 커밋하지 말 것 — 캡션 본문이 들어 있다.
    """
    if not FEED_SAMPLE.exists():
        return None
    with open(FEED_SAMPLE, encoding="utf-8") as f:
        return json.load(f)["items"]


def _feed_source_with_sample(items=None):
    """`_run` 주입 — 네트워크도 디스크(rawstore)도 건드리지 않는다."""
    from slime_rag.sources import ApifyProfileFeedSource
    src = ApifyProfileFeedSource(token="dummy")
    payload = _feed_items() if items is None else items
    src._run = lambda username, **kw: payload
    return src


def test_feed_maps_real_actor_payload():
    """실제 instagram-scraper 아이템 → 판매자 RawReview 매핑."""
    items = _feed_items()
    if items is None:
        print(f"· 피드 실 payload 매핑 skip ({FEED_SAMPLE.name} 없음 — gitignore)")
        return
    src = _feed_source_with_sample(items)
    reviews = list(src.collect(["slime_gina_"], limit=200))
    assert reviews, "실 payload 에서 한 건도 매핑되지 않았다"
    r = reviews[0]
    assert r.meta["kind"] == "profile_post", f"1층 라벨이 아님: {r.meta['kind']}"
    assert r.meta["source"] == "apify" and r.meta["scraped"] is True
    assert r.meta["shortcode"] and r.url.startswith("https://www.instagram.com/p/")
    assert r.posted_at and isinstance(r.meta["hashtags"], list)
    assert all(x.meta["owner_username"] for x in reviews), "소유자 결손 — 판매자 라우팅이 끊긴다"
    print(f"✓ 피드 소스: 실 액터 payload {len(reviews)}건 매핑 OK")


def test_feed_never_labels_review_class():
    """판매자 글에 `review_class` 를 달면 마켓 본인 글이 '홍보성 후기'로 새고 1층이 끊긴다."""
    items = _feed_items()
    if items is None:
        print(f"· 피드 review_class 미부착 skip ({FEED_SAMPLE.name} 없음 — gitignore)")
        return
    src = _feed_source_with_sample(items)
    for r in src.collect(["slime_gina_"], limit=200):
        assert "review_class" not in r.meta, "1층 게시물에 홍보성 라벨이 붙었다"
        assert "promo_marker" not in r.meta
    print("✓ 피드 소스: review_class 미부착(1층) OK")


def test_feed_owner_falls_back_to_requested_handle():
    """`ownerUsername` 이 비어도 소유자를 잃지 않는다 — 잃으면 1층이 2층으로 샌다."""
    src = _feed_source_with_sample([
        {"shortCode": "Z1", "caption": "오늘 신상 올라왔어요 잘 부탁드려요 #빠코볼",
         "inputUrl": "https://www.instagram.com/slime_gina_/"}])
    r = list(src.collect(["slime_gina_"]))[0]
    assert r.meta["owner_username"] == "slime_gina_", r.meta["owner_username"]
    print("✓ 피드 소스: 소유자 폴백(요청 핸들/inputUrl) OK")


def test_feed_owner_fallback_ignores_post_urls():
    """`/p/<code>/` 를 핸들 'p' 로 읽으면 안 된다."""
    from slime_rag.sources.apify import _owner_from_input_url as f
    assert f("https://www.instagram.com/slime_gina_/") == "slime_gina_"
    assert f("https://instagram.com/from.murmurslime") == "from.murmurslime"
    for bad in ("https://www.instagram.com/p/ABC/", "https://www.instagram.com/reel/ABC/",
                None, ""):
        assert f(bad) is None, f"프로필이 아닌 URL 을 핸들로 읽었다: {bad}"
    print("✓ 피드 소스: 게시물 URL 을 핸들로 오독하지 않음 OK")


def test_feed_requests_one_actor_call_per_handle():
    """핸들당 호출 하나 — 몰아 넣으면 중간 실패에 앞선 마켓의 유료 결과까지 날아간다."""
    from slime_rag.sources import ApifyProfileFeedSource
    src = ApifyProfileFeedSource(token="dummy")
    seen: list[tuple] = []

    def fake(username, *, results_limit, newer_than=None):
        seen.append((username, results_limit, newer_than))
        return []
    src._run = fake
    list(src.collect(["slime_gina_", "@catchslime", "  "], limit=200))
    assert [s[0] for s in seen] == ["slime_gina_", "catchslime"], seen
    assert all(s[1] == 200 for s in seen), "limit 은 핸들당 요청량이어야 한다"
    print("✓ 피드 소스: 핸들당 액터 호출 1회('@' 정규화·빈값 스킵) OK")


def test_feed_token_unset_is_resilient():
    """토큰 없으면 예외 없이 0건 — 디스크에도 아무것도 안 쓴다."""
    from slime_rag.sources import ApifyProfileFeedSource
    bare = ApifyProfileFeedSource(token=None)
    assert list(bare.collect(["slime_gina_"])) == []
    print("✓ 피드 소스: 토큰없음 회복력 OK")


def test_run_usage_waits_for_settlement():
    """`.call()` 반환값의 사용액은 **정산 전**일 수 있다 — 재조회해서 확정값을 쓴다.

    실측 회귀(2026-08-09 해시태그 스윕): 첫 배치가 53건인데 $0.00 으로 기록됐고 마지막
    배치 값은 앞 배치들의 합계였다. 봉투 합계 $1.62 대 계정 실제 $1.95 — **17% 과소**.
    비용 기록이 낮으면 다음 런 예산을 그 숫자로 잡는다.
    """
    from slime_rag.sources.apify import finalized_usage_usd

    calls = {"n": 0}

    class _Run:
        def get(self):
            calls["n"] += 1
            return {"id": "r1", "usageTotalUsd": 0 if calls["n"] < 2 else 0.42}

    class _Client:
        def run(self, rid):
            assert rid == "r1"
            return _Run()

    stale = {"id": "r1", "usageTotalUsd": 0}          # 정산 전
    got = finalized_usage_usd(_Client(), stale, tries=3, delay=0)
    assert got == 0.42, f"정산된 값을 못 집었다: {got}"
    assert calls["n"] == 2, f"불필요한 재조회: {calls['n']}"

    # 이미 확정된 값이면 재조회하지 않는다(런마다 초 단위 지연이 붙으면 안 된다).
    calls["n"] = 0
    assert finalized_usage_usd(_Client(), {"id": "r1", "usageTotalUsd": 1.5}, delay=0) == 1.5
    assert calls["n"] == 0, "확정값인데 다시 조회했다"

    # 끝내 정산이 안 되면 None — 상수 추정치로 떨어지되 **실측인 척하지 않는다**.
    class _NeverClient:
        def run(self, rid):
            class _R:
                def get(self_inner):
                    return {"id": rid, "usageTotalUsd": 0}
            return _R()
    assert not finalized_usage_usd(_NeverClient(), {"id": "r1"}, tries=2, delay=0)
    print("✓ 런 사용액: 정산 대기 후 확정값 · 확정이면 무재조회 · 실패는 None OK")


def test_hashtag_saves_raw_split_by_tag():
    """해시태그 런도 **가공 전에** 원문을 남긴다 — 태그별 키로.

    회귀 대상은 실측된 유실이다: 이 경로만 저장이 없어서, 매핑 직후 첫 LLM(홍보성 캐스케이드)이
    죽으면(OpenAI 크레딧 소진 429) 방금 산 결과가 통째로 사라졌다.
    태그별로 가르는 이유는 **0건이 태그 단위로 보여야** 하기 때문이다(`#깡수박화채` 실측).
    """
    import tempfile
    from slime_rag import rawstore
    from slime_rag.sources import ApifyHashtagSource

    items = [
        {"shortCode": "A1", "caption": "빠코볼 최고예요 진짜 말랑", "hashtags": ["빠코볼"],
         "timestamp": "2026-08-01T00:00:00.000Z"},
        {"shortCode": "A2", "caption": "빠코볼이랑 키위스쿱 비교해봤어요 둘 다 좋음",
         "hashtags": ["빠코볼", "키위스쿱"], "timestamp": "2026-08-02T00:00:00.000Z"},
        {"shortCode": "A3", "caption": "이건 요청 안 한 태그만 달린 글이라 관련글로 끼워온 것",
         "hashtags": ["전혀다른태그"], "timestamp": "2026-08-03T00:00:00.000Z"},
    ]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        src = ApifyHashtagSource(token="dummy", results_per_hashtag=10)
        src.last_usage_usd = 0.03            # 런 실사용액 — 건수 비례로 갈려야 한다
        _real_dir = rawstore.run_dir
        rawstore.run_dir = lambda kind, key, root=None: _real_dir(kind, key, root=root or Path(d))
        try:
            src._save_raw(items, ["빠코볼", "키위스쿱", "깡수박화채"])
        finally:
            rawstore.run_dir = _real_dir

        got = {k: len(rawstore.latest_items("ig_hashtag", k, root=root))
               for k in rawstore.iter_keys("ig_hashtag", root=root)}

    assert got.get("빠코볼") == 2, f"태그에 걸린 글이 다 안 담겼다: {got}"
    assert got.get("키위스쿱") == 1, f"비교글이 양쪽 태그에 안 남았다: {got}"
    # 0건 태그도 **파일이 남는다** — '안 돌렸다'와 '돌렸는데 없다'는 다음 판단이 정반대다.
    assert "깡수박화채" in got and got["깡수박화채"] == 0, f"0건 태그 기록이 없다: {got}"
    # 요청 태그에 안 걸린 글도 버리지 않는다(무음 드롭 금지).
    assert got.get("_unmatched") == 1, f"미매칭 글이 사라졌다: {got}"
    print("✓ 해시태그 소스: 가공 전 원문 저장 · 태그별 분할 · 0건/미매칭 보존 OK")


def test_hashtag_raw_usage_is_split_not_duplicated():
    """비용은 **건수 비례로 나눈다.** 런 총액을 봉투마다 적으면 합계가 태그 수만큼 부푼다.

    나눗셈이 정확한 근거: 액터가 PAY_PER_EVENT 단일 이벤트(`result`)라 런 안에서 결과 1건의
    값이 같다(2026-08-09 액터 API 확인).
    """
    import tempfile
    from slime_rag import rawstore
    from slime_rag.sources import ApifyHashtagSource

    items = [{"shortCode": f"B{i}", "caption": f"후기 본문 {i} 말랑하고 좋아요",
              "hashtags": ["빠코볼"], "timestamp": "2026-08-01T00:00:00.000Z"} for i in range(4)]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        src = ApifyHashtagSource(token="dummy", results_per_hashtag=10)
        src.last_usage_usd = 0.01
        _real_dir = rawstore.run_dir
        rawstore.run_dir = lambda kind, key, root=None: _real_dir(kind, key, root=root or Path(d))
        try:
            src._save_raw(items, ["빠코볼", "키위스쿱"])
        finally:
            rawstore.run_dir = _real_dir
        total = sum(doc.get("usage_total_usd") or 0
                    for k in rawstore.iter_keys("ig_hashtag", root=root)
                    for doc in rawstore.load_runs("ig_hashtag", k, root=root))
    assert abs(total - 0.01) < 1e-9, f"봉투 합계가 런 실사용액과 다르다: {total}"
    print("✓ 해시태그 소스: 실사용액이 봉투에 건수 비례로 갈린다(합계 보존) OK")


def test_feed_shares_mapping_with_profile_source():
    """두 액터가 **같은 매퍼**를 쓴다 — 갈리면 하류가 소스마다 다른 meta 를 받는다."""
    from slime_rag.sources.apify import _post_to_seller_review
    post = {"shortCode": "Z9", "caption": "신상 폼볼이에요 오늘 올라왔어요 #빠코볼",
            "timestamp": "2026-01-01T00:00:00Z", "hashtags": ["빠코볼"],
            "likesCount": 3, "commentsCount": 1}
    direct = _post_to_seller_review(post, "slime_gina_", platform="instagram")
    via_profile = InstagramProfileSource(token="dummy")._to_review(post, "slime_gina_")
    assert direct.meta == via_profile.meta and direct.url == via_profile.url
    print("✓ 피드 소스: profile-scraper 와 매퍼 한 벌 OK")


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
    test_fetch_profiles_mapping()
    test_fetch_profiles_ignores_post_media()
    test_posturl_shares_hashtag_mapping()
    test_posturl_never_uses_url_as_relevance_anchor()
    test_posturl_dedup_and_resilience()
    test_posturl_keeps_hashtag_only_caption()
    test_curated_hashtags_cover_every_kb_market()
    test_feed_maps_real_actor_payload()
    test_feed_never_labels_review_class()
    test_feed_owner_falls_back_to_requested_handle()
    test_feed_owner_fallback_ignores_post_urls()
    test_feed_requests_one_actor_call_per_handle()
    test_feed_token_unset_is_resilient()
    test_feed_shares_mapping_with_profile_source()
    test_run_usage_waits_for_settlement()
    test_hashtag_saves_raw_split_by_tag()
    test_hashtag_raw_usage_is_split_not_duplicated()
    print("\n모든 오프라인 테스트 통과 ✅")
