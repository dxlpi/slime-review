# -*- coding: utf-8 -*-
"""Apify 스크래퍼 소스 — 인스타 해시태그(2층 유저후기) + 프로필(1층 판매자 스펙).

공식 Graph API(business_discovery/ig_hashtag_search)가 App Review 벽으로 막혀, 공개 데이터만
긁는 서드파티 스크래퍼로 라이브 캡션을 얻는다. 공식 API 아님을 투명 라벨링(meta.source="apify",
scraped=True). `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑을 무비용 검증(eval/test_apify_source.py).
"""

from __future__ import annotations
from typing import Iterator, Optional

from .base import RawReview, Source, RelevanceGate, is_low_quality, toxic_via_llm, log


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
            platform=self.platform,
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
                "toxic": toxic_via_llm(caption, self.classify_fn),
                "review_class": "promo" if is_promo else "genuine",
                "promo_marker": promo_marker,
            },
        )

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
        caption = (post.get("caption") or "").strip()
        if not caption or is_low_quality(caption):
            return None
        url = post.get("url") or (
            f"https://www.instagram.com/p/{post['shortCode']}/" if post.get("shortCode") else "")
        # review_class 는 달지 않는다 — 판매자 글(1층). bias.partition 이 owner→seller 로 라우팅.
        return RawReview(
            text=caption,
            url=url,
            platform=self.platform,
            posted_at=post.get("timestamp"),
            meta={
                "kind": "profile_post",
                "source": "apify",
                "scraped": True,
                "owner_username": owner,
                "hashtags": post.get("hashtags") or [],
                "likes": post.get("likesCount"),
                "comments": post.get("commentsCount"),
                "shortcode": post.get("shortCode"),
                "toxic": toxic_via_llm(caption, self.classify_fn),
            },
        )

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
