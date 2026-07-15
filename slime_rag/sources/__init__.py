# -*- coding: utf-8 -*-
"""
슬라임 RAG — 수집 레이어 (플러그인 구조)

설계 의도:
- Source 인터페이스 하나로 디시/인스타/(나중에)네이버·유튜브를 동일하게 다룬다.
- 각 구현체는 RawReview(원시 후기)만 내보내고, 추출/개체연결/집계는 하류 단계가 담당.
- '책임 있는 수집': robots 확인 + 요청 간 딜레이 + 페이지 상한 + 재시도 + 원문 미재배포(필요 스니펫만 하류에서).

주의:
- 디시 갤러리 id와 DOM 셀렉터는 라이브 사이트 기준으로 반드시 검증/조정해야 함(각 파일 [ADJUST] 표시).
- 네트워크 차단 환경에선 실행 불가 — 로컬에서 키/셀렉터 넣고 돌릴 것.

파일 구성(과거 단일 sources.py 를 소스별로 분할, import 경로는 불변):
- `base`          — RawReview, Source, Throttle, robots_allowed, get, 노이즈/유해 필터
- `dcinside`      — DCInsideSource (2층 백본: 본문+댓글)
- `instagram`     — InstagramSource (1층 fixture / Graph API 스텁)
- `apify`         — ApifyHashtagSource(2층 해시태그) · InstagramProfileSource(1층 판매자)
- `orchestration` — expand_queries, collect_all
"""

from __future__ import annotations

from .base import (
    RawReview, Source, Throttle,
    robots_allowed, get,
    is_low_quality, has_toxic, toxic_via_llm,
)
from .dcinside import DCInsideSource
from .instagram import InstagramSource
from .apify import ApifyHashtagSource, InstagramProfileSource
from .orchestration import expand_queries, collect_all

__all__ = [
    "RawReview", "Source", "Throttle",
    "robots_allowed", "get",
    "is_low_quality", "has_toxic", "toxic_via_llm",
    "DCInsideSource", "InstagramSource",
    "ApifyHashtagSource", "InstagramProfileSource",
    "expand_queries", "collect_all",
]
