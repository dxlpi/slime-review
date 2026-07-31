# -*- coding: utf-8 -*-
"""인스타그램 Graph API 소스(스텁) — 1층 공식 스펙 + 2층 유저후기 인터페이스.

[데이터 소싱 결정 2026-06-29] business_discovery 는 App Review(advanced access)를 요구해
데모 범위 밖 → 1층은 fixture 주입(`data/layer1_fixture.json`). 인터페이스는 보존해 토큰 확보 시
`_live_business_discovery` 로 전환 가능. 라이브 해시태그 수집은 `apify.ApifyHashtagSource` 사용.
"""

from __future__ import annotations
from typing import Iterator, Optional

import requests

from .base import RawReview, Source, Throttle


class InstagramSource(Source):
    """
    1층 공식 스펙 + 2층 유저 후기 캡션(긍정편향).
    Graph API 설계:
      - business_discovery(아는 마켓 핸들) → 마켓 공식 게시물 캡션/이미지 (1층)
      - ig_hashtag_search → recent_media/top_media 캡션 (유저 후기, username 없음)

    [데이터 소싱 결정 2026-06-29] business_discovery 는 앱 개발 모드에서도 advanced access
    (=App Review·비즈 인증·수주 심사)를 요구한다 → 포트폴리오 데모 범위 밖. 그래서 라이브 호출 대신
    큐레이션 fixture(`data/layer1_fixture.json`)를 같은 인터페이스로 주입한다. 인터페이스는 그대로 두어
    토큰만 확보되면 `_live_business_discovery` 로 전환 가능(설계는 코드에 남김).

    fixture_path 를 주면 fixture 모드, ig_user_id+access_token 만 주면 라이브 모드.
    """
    platform = "instagram"

    def __init__(self, access_token: str | None = None, ig_user_id: str | None = None,
                 min_interval: float = 1.0, fixture_path: Optional[str] = None):
        self.token = access_token
        self.ig_user_id = ig_user_id
        self.throttle = Throttle(min_interval)
        self.s = requests.Session()
        self.fixture_path = fixture_path   # set → fixture 모드(라이브 API 미호출)

    # -------- 1층: 마켓 공식 게시물 (라이브/픽스처 동일 반환형) --------
    def business_discovery(self, handle: str) -> list[dict]:
        """핸들의 공식 게시물 목록을 business_discovery 응답(media.data) 모양으로 반환.
        fixture_path 가 있으면 큐레이션 스냅샷에서, 없으면 Graph API 라이브."""
        if self.fixture_path:
            from ..layer1 import load_fixture
            fx = load_fixture(self.fixture_path)
            return fx.get(handle, {}).get("posts", [])
        return self._live_business_discovery(handle)

    def _live_business_discovery(self, handle: str) -> list[dict]:
        # GET /{ig_user_id}?fields=business_discovery.username({handle}){media{caption,permalink,timestamp,media_type}}
        # ⚠️ 대상 핸들이 공개 비즈/크리에이터 계정이어야 하고, 앱이 advanced access(App Review)여야 200.
        raise NotImplementedError(
            "business_discovery 라이브는 App Review 통과 후 가능 — 데모는 fixture_path 사용")

    def collect(self, keywords: list[str], limit: int = 100,
                target: Optional[dict] = None) -> Iterator[RawReview]:
        """fixture 모드: 마켓 공식 게시물 캡션을 1층 RawReview 로 yield(공식 스펙=official_spec).
        keywords 를 주면 그 핸들만, 없으면 fixture 전체. (2층 hashtag 경로는 _collect_hashtag 참고)
        1층(공식 스펙) 경로라 관련성 게이트 미적용 — target 은 인터페이스 통일용으로만 받음(D6)."""
        if not self.fixture_path:
            raise NotImplementedError("Graph API 토큰/App Review 연결 후 구현 — 데모는 fixture_path 사용")
        from ..layer1 import load_fixture, iter_official_posts
        fx = load_fixture(self.fixture_path)
        handles = [k for k in keywords if k in fx] or None
        n = 0
        for handle, post in iter_official_posts(fx, handles):
            if n >= limit:
                break
            yield RawReview(
                text=post["caption"],
                url=post.get("permalink", ""),
                platform=self.platform,
                posted_at=post.get("timestamp"),
                meta={"kind": "official_spec", "handle": handle,
                      "media_type": post.get("media_type"), "post_id": post.get("id")},
            )
            n += 1

    def _collect_hashtag(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        # [TODO·선택] GET /ig_hashtag_search → hashtag_id → /{hid}/recent_media?fields=caption,permalink,timestamp
        #             2층 '긍정편향 소스' 데모용. username 없음, 7일/30태그 한계. 현재 미구현.
        # 라이브 스크래퍼 티어 구현은 apify.ApifyHashtagSource 참고(App Review 없이 동작).
        raise NotImplementedError("ig_hashtag_search(2층 유저후기)는 선택 — 미구현. 라이브는 ApifyHashtagSource 사용")
