# -*- coding: utf-8 -*-
"""Apify 스크래퍼 소스 — 인스타 해시태그(2층 유저후기) + 프로필(1층 판매자 스펙).

공식 Graph API(business_discovery/ig_hashtag_search)가 App Review 벽으로 막혀, 공개 데이터만
긁는 서드파티 스크래퍼로 라이브 캡션을 얻는다. 공식 API 아님을 투명 라벨링(meta.source="apify",
scraped=True). `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑을 무비용 검증(eval/test_apify_source.py).
"""

from __future__ import annotations
import re
from typing import Iterator, Optional

from .base import RawReview, Source, RelevanceGate, is_low_quality, toxic_via_llm, log


# ---------------------------------------------------------------- 아이템 → RawReview (액터 공용)
def _item_to_review(item: dict, *, platform: str, classify_fn=None,
                    hashtag: str | None = None) -> Optional[RawReview]:
    """Apify 인스타 데이터셋 아이템 1건 → `RawReview`. 빈/저품질 캡션은 None.

    **해시태그 액터와 URL 액터가 같은 필드 이름을 쓴다**(2026-08-07 라이브 확인:
    `shortCode`/`ownerUsername`/`hashtags`/`likesCount`/`commentsCount`/`timestamp`/`url`/
    `caption`). 그래서 매핑은 한 벌이고 소스 클래스는 '무엇을 요청하는가'만 다르다.
    **Don't:** 액터별로 복제하지 말 것 — `meta` 키가 갈리면 하류(`build_source_ref`·
    `index_post`·편향 라벨)가 소스마다 다른 모양을 받는다.
    """
    caption = (item.get("caption") or "").strip()
    if not caption or is_low_quality(caption):
        return None
    tags = item.get("hashtags") or []
    tag = hashtag or (tags[0] if tags else None)
    url = item.get("url") or (
        f"https://www.instagram.com/p/{item['shortCode']}/" if item.get("shortCode") else "")
    # 홍보성(대가/무상 제공) 라벨 — 캡션 자족(KB-무관). 판매자 라우팅은 KB 가 필요해
    # bias.partition(수집 후 패스)에서 확정한다. 여기선 review_class 초안만.
    from ..bias import detect_promo
    is_promo, promo_marker = detect_promo(caption)
    return RawReview(
        text=caption,
        url=url,
        platform=platform,
        posted_at=item.get("timestamp"),
        meta={
            "kind": "hashtag_caption",
            "source": "apify",
            "scraped": True,
            "hashtag": tag,
            "hashtags": tags,
            "owner_username": item.get("ownerUsername"),
            "likes": item.get("likesCount"),
            "comments": item.get("commentsCount"),
            "shortcode": item.get("shortCode"),
            "toxic": toxic_via_llm(caption, classify_fn),
            "review_class": "promo" if is_promo else "genuine",
            "promo_marker": promo_marker,
        },
    )


def _owner_from_input_url(url: str | None) -> Optional[str]:
    """`https://www.instagram.com/<handle>/` → `<handle>`. 프로필 URL 이 아니면 None.

    `instagram-scraper` 로 피드를 훑을 때 아이템의 `ownerUsername` 이 비는 경우가 있어
    **우리가 무엇을 요청했는지**(`inputUrl`)를 폴백으로 쓴다. 소유자를 잃으면
    `bias.partition` 이 판매자로 라우팅하지 못해 1층 게시물이 2층 후기로 새어 들어간다.
    """
    m = re.match(r"^https?://(?:www\.)?instagram\.com/([^/?#]+)/?", (url or "").strip())
    if not m:
        return None
    handle = m.group(1)
    # 게시물/탐색 경로는 프로필이 아니다 — `/p/<code>/` 를 핸들 'p' 로 읽으면 안 된다.
    return None if handle in {"p", "reel", "reels", "tv", "explore", "stories"} else handle


def _post_to_seller_review(post: dict, owner: str | None, *, platform: str,
                           classify_fn=None) -> Optional[RawReview]:
    """판매자(1층) 게시물 1건 → `RawReview`. 빈/저품질 캡션은 None.

    **`review_class` 를 달지 않는다** — 판매자 글이라 홍보성 판정 대상이 아니고,
    `bias.partition` 이 `owner_username` → KB 핸들로 라우팅한다. 여기서 promo 라벨을
    붙이면 마켓 본인 게시물이 '홍보성 후기' 버킷으로 새고, 그 순간 1층 스펙 경로가 끊긴다.

    `instagram-profile-scraper`(latestPosts[]) 와 `instagram-scraper`(평탄 게시물) 두 액터가
    같은 필드 이름을 쓰므로 매핑은 한 벌이다. **Don't:** 액터별로 복제하지 말 것 —
    `meta` 키가 갈리면 하류(`bias.partition`·`_specs_from_seller_post`)가 소스마다 다른
    모양을 받는다. 같은 이유로 2층 매퍼(`_item_to_review`)와도 합치지 **않는다**:
    저쪽은 `review_class` 를 달아야 하고 이쪽은 달면 안 되는, 규칙이 반대인 두 경로다.
    """
    caption = (post.get("caption") or "").strip()
    if not caption or is_low_quality(caption):
        return None
    url = post.get("url") or (
        f"https://www.instagram.com/p/{post['shortCode']}/" if post.get("shortCode") else "")
    return RawReview(
        text=caption,
        url=url,
        platform=platform,
        posted_at=post.get("timestamp"),
        meta={
            "kind": "profile_post",
            "source": "apify",
            "scraped": True,
            "owner_username": owner or _owner_from_input_url(post.get("inputUrl")),
            "hashtags": post.get("hashtags") or [],
            "likes": post.get("likesCount"),
            "comments": post.get("commentsCount"),
            "shortcode": post.get("shortCode"),
            "toxic": toxic_via_llm(caption, classify_fn),
        },
    )


# ---------------------------------------------------------------- 인스타그램 해시태그(Apify 스크래퍼)
class ApifyHashtagSource(Source):
    """
    2층 긍정편향 유저후기 — 인스타 해시태그 캡션을 Apify 스크래퍼로 수집.

    [소싱 결정] Graph API `ig_hashtag_search` 는 advanced access(App Review·비즈 인증)를 요구해
    포트폴리오 데모 범위 밖(business_discovery 와 동일 벽). 대신 서드파티 스크래퍼
    `apify/instagram-hashtag-scraper`(공개 데이터만)로 라이브 캡션을 얻는다. 공식 API 아님을
    투명하게 라벨링: 모든 RawReview.meta 에 source="apify", scraped=True 를 실어 official_spec 과
    혼동을 차단한다. platform 은 "instagram"(하류 소스편향 집계에서 긍정편향 IG 소스로 취급).

    비용/한계(2026-07-14 확인): $1.90/1000건, 해시태그당 ~30건 상한, 무료 티어는 API 호출 불가
    (유료 엔트리 플랜 필요). 그래서 폭(큐레이션 태그 다수)으로 recall, 깊이는 포기. token 미설정 시
    조용히 스킵(collect_all 이 빈 결과 처리 — DCInside/NotImplementedError 스킵과 동일한 회복력).

    오프라인 테스트: `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑을 무비용 검증한다
    (data/apify_hashtag_sample.json, eval/test_apify_source.py).
    """
    platform = "instagram"
    COST_PER_1000 = 1.90   # USD, pay-per-result (2026-07-14 확인)

    def __init__(self, token: str | None = None, hashtags: list[str] | None = None,
                 actor: str = "apify/instagram-hashtag-scraper",
                 results_per_hashtag: int = 30, classify_fn=None,
                 hashtags_path: Optional[str] = None):
        self.token = token
        self.actor = actor
        self.results_per_hashtag = results_per_hashtag
        self.classify_fn = classify_fn
        # 큐레이션 태그: 명시 인자 > hashtags_path 파일 > config 기본 경로
        self.hashtags = hashtags if hashtags is not None else self._load_hashtags(hashtags_path)

    # -------- 큐레이션 해시태그 로드 --------
    @staticmethod
    def _load_hashtags(path: Optional[str] = None) -> list[str]:
        """data/ig_hashtags.json → 평탄화된 태그 리스트('#' 없이). '_' 접두 키는 무시."""
        from ..config import settings
        p = path or settings.ig_hashtags_path
        try:
            import json
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:                       # 파일 없거나 깨지면 빈 목록(collect 이 keywords 로 보강)
            log.warning("해시태그 목록 로드 실패(%s): %s", p, e)
            return []
        tags: list[str] = []                          # global 폐기 — by_market 만 광역 태그로 사용
        for mk, mtags in (raw.get("by_market", {}) or {}).items():
            if mk.startswith("_"):
                continue
            tags.extend(mtags or [])
        # 순서 유지 중복 제거
        seen, out = set(), []
        for t in tags:
            t = (t or "").lstrip("#").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    @staticmethod
    def _is_choseong_only(s: str) -> bool:
        """전부 한글 호환 자모(ㅂ, ㅁㅁ 등)인가 — 초성 단독 검색어 판별.
        '봄'(완성형 음절, U+AC00~)은 False, 'ㅂ'(U+3131~)은 True."""
        return bool(s) and all("㄰" <= c <= "㆏" for c in s)

    def _resolve_hashtags(self, keywords: list[str]) -> list[str]:
        """검색어가 있으면 '제품명 그대로'만 검색한다(마켓명·'슬라임' 등 광역어 미부착).
        검색어가 없을 때만 큐레이션 마켓 태그(by_market)로 광역 수집한다. 순서 유지 중복 제거.

        예) _resolve_hashtags(["레몬커드쉘도넛"]) → ["레몬커드쉘도넛"]
            머머슬라임·슬라임 같은 마켓/광역 태그는 붙이지 않는다(제품 단위 정밀검색).
        """
        seen, out = set(), []
        for kw in (keywords or []):
            kw = (kw or "").strip()
            if not kw or self._is_choseong_only(kw):   # 초성 단독('ㅂ')은 과광범위 → 제외
                continue
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
        if out:                                        # 특정 제품 검색: 제품명만
            return out
        return list(self.hashtags)                     # 검색어 없음: 큐레이션 마켓 태그로 광역 수집

    # -------- 네트워크 경계(테스트 주입점) --------
    def _run(self, hashtags: list[str]) -> list[dict]:
        """Apify actor 실행 → 데이터셋 아이템(raw dict) 리스트. 실패/토큰없음이면 []."""
        if not self.token:
            log.info("APIFY_TOKEN 미설정 → 해시태그 수집 스킵")
            return []
        if not hashtags:
            log.info("해시태그 목록 비어있음 → 스킵")
            return []
        try:
            from apify_client import ApifyClient
        except ImportError:
            log.warning("apify-client 미설치 → 해시태그 수집 스킵 (pip install apify-client)")
            return []
        try:
            client = ApifyClient(self.token)
            run_input = {"hashtags": hashtags, "resultsType": "posts",
                         "resultsLimit": self.results_per_hashtag}
            run = client.actor(self.actor).call(run_input=run_input)
            # v3: .call() 은 pydantic Run 모델(run.default_dataset_id).
            # v1: dict({"defaultDatasetId": ...}). 둘 다 지원.
            ds_id = getattr(run, "default_dataset_id", None)
            if ds_id is None and isinstance(run, dict):
                ds_id = run.get("defaultDatasetId")
            if not ds_id:
                log.warning("Apify run 에 dataset id 없음: %r", run)
                return []
            return list(client.dataset(ds_id).iterate_items())
        except Exception as e:                       # 네트워크/플랜/쿼터 실패 → 회복력 있게 스킵
            log.warning("Apify 수집 실패: %s", e)
            return []

    # -------- 아이템 → RawReview --------
    def _to_review(self, item: dict, hashtag: str | None = None) -> Optional[RawReview]:
        return _item_to_review(item, platform=self.platform,
                               classify_fn=self.classify_fn, hashtag=hashtag)

    def collect(self, keywords: list[str], limit: int = 100,
                target: dict | None = None) -> Iterator[RawReview]:
        hashtags = self._resolve_hashtags(keywords)
        items = self._run(hashtags)
        # 관측성: 요청 태그 수 / 반환 건수 / 예상비용(무음 상한 금지)
        cost = len(items) / 1000 * self.COST_PER_1000
        log.info("Apify 해시태그 %d개 요청 → %d건 반환 (예상비용 $%.4f)",
                 len(hashtags), len(items), cost)
        # 매핑(중복접기 + 저품질 드롭) — 관련성 게이트 이전 상태.
        seen, mapped = set(), []
        for item in items:
            key = item.get("shortCode") or item.get("url")
            if key and key in seen:                  # shortCode 중복 접기
                continue
            if key:
                seen.add(key)
            review = self._to_review(item)
            if review is None:                       # 빈/저품질 캡션 드롭
                continue
            mapped.append(review)
        # 관련성 게이트(2층). 캡션은 이미 배치로 와 있어 classify 배치가 자연스럽다.
        # 앵커가 없으면(target 미주입 + keywords 없음) 하위호환 패스스루(RelevanceGate 비활성).
        gate = RelevanceGate(self.platform, target, keywords, limit, log)
        yield from gate.filter(mapped)
        gate.finish()


class ApifyPostUrlSource(Source):
    """2층 유저후기 — **게시물 URL 을 직접 지정**해 캡션을 가져온다(Apify instagram-scraper).

    [소싱 결정 2026-08-07] 해시태그 경로가 **닿지 못하는 태그가 있다**. 실측: `#깡수박화채` 가
    `apify/instagram-hashtag-scraper` 에서 두 번 연속 0건(`NO RESULTS: ... is private or has no
    posts`)이었는데, 같은 런의 `#빈짱슬라임` 은 5건이 정상 반환됐고, 문제의 태그를 단 게시물 2건은
    URL 로 조회하니 그대로 나왔다. 한글 문제도 토큰 문제도 아니고 **그 태그의 explore 페이지가
    로그아웃 스크레이핑에 안 보이는 것**(신생·저용량 태그로 추정)이다. 즉 이 경로가 없으면
    '해시태그는 달려 있는데 영영 못 걷는 후기'가 남는다.

    ⚠️ **탐색 경로가 아니다.** URL 은 사람이 준다 — 이 소스는 표본을 만들지 않으므로
    '이 제품 후기 N건 중 M건' 같은 커버리지 주장의 근거로 쓰면 안 된다. 발견은 해시태그 경로와
    마켓 태그 광역 수집(`by_market`) 몫이고, 여기는 **이미 아는 URL 을 확실히 넣는** 자리다.

    ⚠️ 관련성 앵커는 **`target` 으로만** 온다. `keywords` 에 담기는 건 URL 문자열이라
    `resolve_target` 의 keywords[0] 폴백에 그대로 흘리면 `slime="https://..."` 라는 앵커가 생겨
    코사인 점수가 통째로 무의미해진다. 그래서 게이트에 keywords 를 넘기지 않는다 —
    target 이 없으면 문서화된 패스스루로 떨어진다(사람이 고른 URL 이라 그 편이 정직하다).

    비용: 액터가 결과당 과금이라 상수를 박지 않고 **run 의 실제 사용액**(`usageTotalUsd`)을
    로그로 남긴다 — 미검증 상수를 두는 것보다 관측성이 낫다.
    오프라인 테스트: `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑 검증(무비용).
    """
    platform = "instagram"

    def __init__(self, token: str | None = None,
                 actor: str = "apify/instagram-scraper", classify_fn=None):
        self.token = token
        self.actor = actor
        self.classify_fn = classify_fn

    # -------- 네트워크 경계(테스트 주입점) --------
    def _run(self, urls: list[str]) -> list[dict]:
        """Apify actor 실행 → 데이터셋 아이템(raw dict). 실패/토큰없음이면 [](회복력)."""
        if not self.token:
            log.info("APIFY_TOKEN 미설정 → URL 수집 스킵")
            return []
        if not urls:
            log.info("URL 목록 비어있음 → 스킵")
            return []
        try:
            from apify_client import ApifyClient
        except ImportError:
            log.warning("apify-client 미설치 → URL 수집 스킵 (pip install apify-client)")
            return []
        try:
            client = ApifyClient(self.token)
            run = client.actor(self.actor).call(run_input={
                "directUrls": urls, "resultsType": "posts",
                "resultsLimit": 1,          # URL 당 그 게시물 하나. 프로필로 번지지 않게.
                "addParentData": False,
            })
            ds_id = getattr(run, "default_dataset_id", None)
            usd = getattr(run, "usage_total_usd", None)
            if isinstance(run, dict):       # apify-client v1 은 dict
                ds_id = ds_id or run.get("defaultDatasetId")
                usd = usd if usd is not None else run.get("usageTotalUsd")
            if not ds_id:
                log.warning("Apify run 에 dataset id 없음: %r", run)
                return []
            items = list(client.dataset(ds_id).iterate_items())
            log.info("Apify URL %d개 요청 → %d건 반환%s", len(urls), len(items),
                     f" (실사용 ${usd:.4f})" if isinstance(usd, (int, float)) else "")
            return items
        except Exception as e:              # 네트워크/플랜/쿼터 실패 → 회복력 있게 스킵
            log.warning("Apify URL 수집 실패: %s", e)
            return []

    def collect(self, keywords: list[str], limit: int = 100,
                target: dict | None = None) -> Iterator[RawReview]:
        """`keywords` 는 **게시물 URL 목록**이다(Source 인터페이스 유지)."""
        urls = [u.strip() for u in (keywords or []) if (u or "").strip()]
        items = self._run(urls)
        seen, mapped = set(), []
        for item in items:
            key = item.get("shortCode") or item.get("url")
            if key and key in seen:         # 같은 URL 을 두 번 넘긴 경우 접기
                continue
            if key:
                seen.add(key)
            review = _item_to_review(item, platform=self.platform,
                                     classify_fn=self.classify_fn)
            if review is None:              # 빈/저품질 캡션 드롭
                continue
            mapped.append(review)
        # keywords(=URL)를 게이트에 넘기지 않는 이유는 클래스 독스트링 참조.
        gate = RelevanceGate(self.platform, target, [], limit, log)
        yield from gate.filter(mapped)
        gate.finish()


class InstagramProfileSource(Source):
    """1층 공식 스펙 수집 — 마켓 '본인' 계정 피드를 username 으로 스크랩(Apify profile scraper).

    [소싱 결정 2026-07-15] Graph API business_discovery(handle) 는 App Review 벽으로 막혀
    1층을 fixture 로 대체했지만(memo instagram-businessdiscovery-blocked), Apify
    instagram-profile-scraper 는 '핸들의 공개 게시물'을 App Review 없이 가져온다 —
    business_discovery 의 스크래핑 대체물. 수집물은 owner_username==핸들 이라 bias.partition 이
    판매자로 라우팅 → extract.extract_spec → specs(기존 ingest_hashtag 판매자 경로 재사용).
    공식 API 아님을 투명 라벨링: meta.source="apify", scraped=True.

    비용/한계: $1.60/1000 '프로필'(결과 아님), 프로필당 최신 ~12게시물, resultsLimit 파라미터
    없음(액터가 최신분만). token 미설정/실패 시 조용히 스킵(collect_all 회복력).
    오프라인 테스트: `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑 검증 가능.
    """
    platform = "instagram"
    COST_PER_1000 = 1.60   # USD, per profile (2026-07-15 확인, 무료 플랜)

    def __init__(self, token: str | None = None,
                 actor: str = "apify/instagram-profile-scraper", classify_fn=None):
        self.token = token
        self.actor = actor
        self.classify_fn = classify_fn

    # -------- 네트워크 경계(테스트 주입점) --------
    def _run(self, usernames: list[str]) -> list[dict]:
        """Apify profile actor 실행 → 데이터셋 아이템(raw dict). 실패/토큰없음이면 []."""
        if not self.token:
            log.info("APIFY_TOKEN 미설정 → 프로필 수집 스킵")
            return []
        if not usernames:
            log.info("username 목록 비어있음 → 스킵")
            return []
        try:
            from apify_client import ApifyClient
        except ImportError:
            log.warning("apify-client 미설치 → 프로필 수집 스킵 (pip install apify-client)")
            return []
        try:
            client = ApifyClient(self.token)
            run = client.actor(self.actor).call(run_input={"usernames": usernames})
            ds_id = getattr(run, "default_dataset_id", None)
            if ds_id is None and isinstance(run, dict):
                ds_id = run.get("defaultDatasetId")
            if not ds_id:
                log.warning("Apify run 에 dataset id 없음: %r", run)
                return []
            return list(client.dataset(ds_id).iterate_items())
        except Exception as e:                       # 네트워크/플랜/쿼터 실패 → 회복력 있게 스킵
            log.warning("Apify 프로필 수집 실패: %s", e)
            return []

    @staticmethod
    def _iter_posts(item: dict):
        """액터 출력 스키마 유연 대응: 프로필 객체(latestPosts[]) 또는 평탄 포스트 아이템 양쪽.
        (post_dict, owner_username) 을 yield. owner 는 post > 프로필 순으로 해석."""
        posts = item.get("latestPosts")
        if isinstance(posts, list) and posts:
            owner = item.get("username") or item.get("ownerUsername")
            for p in posts:
                yield p, (p.get("ownerUsername") or owner)
        elif item.get("caption") is not None or item.get("shortCode"):
            yield item, item.get("ownerUsername")

    # -------- 아이템 → RawReview --------
    def _to_review(self, post: dict, owner: str | None) -> Optional[RawReview]:
        return _post_to_seller_review(post, owner, platform=self.platform,
                                      classify_fn=self.classify_fn)

    # -------- 프로필 레벨 필드(로고) — 게시물 경로와 분리 --------
    def fetch_profiles(self, usernames: list[str]) -> list[dict]:
        """핸들 목록 → 프로필 메타 `{username, profile_pic_url, full_name}` 리스트.

        `collect()` 과 **같은 액터 응답의 다른 층**을 읽는다. `_to_review` 는 `latestPosts[]`
        만 훑어 프로필 레벨 필드를 통째로 버리는데, 로고는 거기 있다. 그래서 새 네트워크
        호출이 아니라 새 매핑이다 — 유일한 네트워크 경계 `_run()` 을 그대로 재사용하므로
        토큰 없음/실패 시 `[]` 회복력과 오프라인 테스트 주입점을 함께 상속한다.

        여기서는 저장하지 않는다(순수 매핑). 다운로드·KB 기록은 `slime_rag.logos` 담당 —
        정책 경계가 ADR-0010 이라 수집기가 재호스팅 결정을 갖고 있으면 안 된다.
        """
        usernames = [u.lstrip("@").strip() for u in (usernames or []) if u and u.strip()]
        items = self._run(usernames)
        cost = len(usernames) / 1000 * self.COST_PER_1000
        log.info("Apify 프로필 메타 %d개 요청 → %d 아이템 (예상비용 $%.4f)",
                 len(usernames), len(items), cost)
        out, missing = [], []
        for item in items:
            handle = item.get("username") or item.get("ownerUsername")
            if not handle:
                continue
            # HD(320px) 우선 — ADR-0010 이 정한 해상도 상한이자 하한이다. 없으면 150px 폴백.
            pic = item.get("profilePicUrlHD") or item.get("profilePicUrl")
            if not pic:
                missing.append(handle)          # 무음 갭 금지 — 세어서 로그로 드러낸다
                continue
            out.append({"username": handle, "profile_pic_url": pic,
                        "full_name": item.get("fullName") or None})
        if missing:
            log.warning("프로필 사진 URL 없음 %d건: %s", len(missing), ", ".join(missing))
        return out

    def collect(self, keywords: list[str], limit: int = 100,
                target: dict | None = None) -> Iterator[RawReview]:
        """keywords = 마켓 핸들(username). '@' 접두 허용. 프로필 최신 게시물을 RawReview 로 방출.
        1층(판매자 스펙) 경로라 관련성 게이트 미적용(D6 불변식) — target 은 인터페이스 통일용으로만 받음."""
        usernames = [k.lstrip("@").strip() for k in (keywords or []) if k and k.strip()]
        items = self._run(usernames)
        cost = len(usernames) / 1000 * self.COST_PER_1000
        log.info("Apify 프로필 %d개 요청 → %d 아이템 반환 (예상비용 $%.4f)",
                 len(usernames), len(items), cost)
        seen, emitted = set(), 0
        for item in items:
            for post, owner in self._iter_posts(item):
                if emitted >= limit:
                    return
                key = post.get("shortCode") or post.get("url")
                if key and key in seen:              # shortCode 중복 접기
                    continue
                if key:
                    seen.add(key)
                review = self._to_review(post, owner)
                if review is None:                   # 빈/저품질 캡션 드롭
                    continue
                yield review
                emitted += 1
        log.info("Apify 프로필: %d건 방출(중복/저품질 제외)", emitted)


class ApifyProfileFeedSource(Source):
    """1층 판매자 피드 **전량** 수집 — 마켓 프로필을 깊게 훑는다(Apify instagram-scraper).

    [소싱 결정 2026-08-07] `InstagramProfileSource`(profile-scraper)는 최신 **~12개**만 주고
    `resultsLimit` 파라미터가 아예 없다. 그 창으로는 마켓의 **제품 목록을 만들 수 없다** —
    실측으로 드러난 결과가 이거다: 두 번째 라이브 런이 1층 커버리지(10→12 마켓)는 올렸는데
    후기 쪽 어휘 갭은 **76 그대로**였다. 창이 최신순이라 들어오는 건 신제품인데 후기 코퍼스는
    옛 제품 얘기를 하기 때문이다. 그래서 `directUrls` 에 **프로필 URL** 을 넣고
    `resultsType="posts"` + `resultsLimit=N` 으로 같은 피드를 깊이 가져오는 액터로 바꾼다.

    핵심 성질 — 바꾸지 말 것:
    · **핸들 하나당 액터 호출 하나.** 한 번에 몰아 넣으면 9번째 마켓에서 실패했을 때
      1~8번 마켓의 유료 결과까지 통째로 날아간다. 마켓별 호출이라야 받는 즉시 디스크에
      떨어지고, 비용도 마켓별로 귀속된다.
    · **`_run` 안에서 곧바로 저장한다**(`rawstore.save_run`). 저장이 호출부에 있으면
      호출부가 예외로 죽는 순간 방금 산 응답이 사라진다. 이 클래스가 존재하는 이유의 절반이
      '가공 전에 원문을 남긴다'라서, 그 저장은 매핑보다 **앞**이어야 한다.
      (오프라인 테스트는 `_run` 을 통째로 주입하므로 디스크를 건드리지 않는다.)
    · **관련성 게이트 미적용** — 1층 경로(D6 불변식). `InstagramProfileSource` 와 같다.

    비용: 결과당 과금이라 상수를 박지 않고 run 의 실제 사용액(`usageTotalUsd`)을 남긴다
    (`ApifyPostUrlSource` 가 세운 선례). 무료 플랜 기준 $2.70/1000 결과.
    ⚠️ 반환량이 요청량과 같으면 **피드가 잘렸을 수 있다** — 호출부가 그걸 `hit_limit` 으로
      드러낸다. 조용한 절단을 '전부 걷었다'로 읽지 않기 위한 장치다.
    """
    platform = "instagram"

    def __init__(self, token: str | None = None, actor: str | None = None,
                 classify_fn=None, raw_kind: str = "ig_profile_feed"):
        from ..config import settings
        self.token = token
        self.actor = actor or settings.apify_profile_feed_actor
        self.classify_fn = classify_fn
        self.raw_kind = raw_kind
        self.last_usage_usd: float | None = None
        self.last_requested: int | None = None

    # -------- 네트워크 경계(테스트 주입점) --------
    def _run(self, username: str, *, results_limit: int,
             newer_than: str | None = None) -> list[dict]:
        """핸들 **하나**의 피드를 훑어 데이터셋 아이템(raw dict)을 돌려주고 원문을 저장한다.

        실패/토큰없음/패키지없음이면 `[]`(회복력) — 그 경우엔 저장할 응답도 없다.
        """
        self.last_usage_usd = None
        self.last_requested = results_limit
        if not self.token:
            log.info("APIFY_TOKEN 미설정 → 피드 수집 스킵 (%s)", username)
            return []
        if not username:
            return []
        try:
            from apify_client import ApifyClient
        except ImportError:
            log.warning("apify-client 미설치 → 피드 수집 스킵 (pip install apify-client)")
            return []
        run_input: dict = {
            "directUrls": [f"https://www.instagram.com/{username}/"],
            "resultsType": "posts",
            "resultsLimit": results_limit,
            "addParentData": False,
        }
        if newer_than:
            run_input["onlyPostsNewerThan"] = newer_than
        try:
            client = ApifyClient(self.token)
            run = client.actor(self.actor).call(run_input=run_input)
            ds_id = getattr(run, "default_dataset_id", None)
            usd = getattr(run, "usage_total_usd", None)
            if isinstance(run, dict):            # apify-client v1 은 dict
                ds_id = ds_id or run.get("defaultDatasetId")
                usd = usd if usd is not None else run.get("usageTotalUsd")
            if not ds_id:
                log.warning("Apify run 에 dataset id 없음: %r", run)
                return []
            items = list(client.dataset(ds_id).iterate_items())
            self.last_usage_usd = usd if isinstance(usd, (int, float)) else None
        except Exception as e:                   # 네트워크/플랜/쿼터 실패 → 회복력 있게 스킵
            log.warning("Apify 피드 수집 실패 (%s): %s", username, e)
            return []
        # ---- 가공 **전** 저장. 이 줄보다 앞에서 items 를 건드리지 않는다. ----
        from .. import rawstore
        try:
            rawstore.save_run(items, actor=self.actor, kind=self.raw_kind, key=username,
                              requested={"resultsLimit": results_limit,
                                         "onlyPostsNewerThan": newer_than},
                              usage_usd=self.last_usage_usd)
        except OSError as e:                     # 디스크 실패로 유료 결과를 버리진 않는다
            log.error("원문 저장 실패 (%s) — 메모리 결과는 계속 사용: %s", username, e)
        log.info("Apify 피드 %s: %d/%d건%s", username, len(items), results_limit,
                 f" (실사용 ${self.last_usage_usd:.4f})"
                 if isinstance(self.last_usage_usd, (int, float)) else "")
        return items

    def collect(self, keywords: list[str], limit: int = 200,
                target: dict | None = None) -> Iterator[RawReview]:
        """keywords = 마켓 핸들 목록('@' 접두 허용). 핸들마다 별도 액터 호출.

        `limit` 은 **핸들당** 요청 결과 수다(전체 합이 아니다) — 액터의 `resultsLimit` 이
        URL 당 적용되기 때문이고, 그래야 마켓 하나가 예산을 다 먹지 않는다.
        1층 경로라 관련성 게이트 미적용(D6) — `target` 은 인터페이스 통일용.
        """
        for raw_handle in (keywords or []):
            username = (raw_handle or "").lstrip("@").strip()
            if not username:
                continue
            for item in self._run(username, results_limit=limit):
                review = _post_to_seller_review(
                    item, item.get("ownerUsername") or username,
                    platform=self.platform, classify_fn=self.classify_fn)
                if review is not None:           # 빈/저품질 캡션 드롭
                    yield review
