# -*- coding: utf-8 -*-
"""수집 레이어 공통 기반 — 모델·인터페이스·책임수집 유틸·노이즈/유해 필터.

여기 있는 것은 소스 구현체(dcinside/instagram/apify)가 공유하는 최소 공통부다.
새 소스는 `Source` 를 구현하고 이 모듈의 `RawReview`·`get`·필터를 재사용한다.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional
from urllib.parse import urlparse
import urllib.robotparser as robotparser
import time, re, logging

import requests

log = logging.getLogger("sources")


# ---------------------------------------------------------------- 공통 모델
@dataclass
class RawReview:
    text: str                      # 정제된 본문(+제목)
    url: str
    platform: str                  # 'dcinside' | 'instagram' | ...
    posted_at: Optional[str] = None
    raw_title: Optional[str] = None
    meta: dict = field(default_factory=dict)   # 키워드/갤러리 등 추적용


# ---------------------------------------------------------------- 인터페이스
class Source(ABC):
    """모든 수집기는 이 인터페이스를 구현. 나중에 네이버/유튜브도 여기에 추가만 하면 됨."""
    platform: str

    @abstractmethod
    def collect(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        """키워드(마켓명/제품명/초성)별로 원시 후기를 yield."""
        ...


# ---------------------------------------------------------------- 책임 수집 유틸
class Throttle:
    """요청 간 최소 간격 보장(서버 예의 + 차단 회피)."""
    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()


def robots_allowed(url: str, user_agent: str) -> bool:
    """robots.txt 준수 여부 확인. 강제력은 없지만 지키는 게 원칙."""
    try:
        parts = urlparse(url)
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception as e:           # robots를 못 읽으면 보수적으로 진행 보류
        log.warning("robots 확인 실패(%s): %s", url, e)
        return False


def get(session: requests.Session, url: str, throttle: Throttle,
        retries: int = 2, timeout: int = 10) -> Optional[str]:
    """딜레이 + 재시도 포함 GET. 실패 시 None."""
    for attempt in range(retries + 1):
        throttle.wait()
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.text
            log.warning("HTTP %s @ %s", r.status_code, url)
        except requests.RequestException as e:
            log.warning("요청 실패(%s) @ %s [%d/%d]", e, url, attempt + 1, retries + 1)
        time.sleep(1.5 * (attempt + 1))
    return None


# ---------------------------------------------------------------- 노이즈/유해 필터
# NOTE: 시드 수준. 운영에선 분류기/LLM로 대체 권장(아래 toxic_via_llm 훅 참고).
_TOXIC_SEED = [
    # 최소한의 시드. 실제 목록은 별도 파일로 관리하고 여기서 로드 권장.
    # (욕설/혐오 표현 패턴)
]
_NOISE_RE = re.compile(r"^(ㅋ+|ㅇㅇ|ㄴㄴ|\.+|\s*)$")

def is_low_quality(text: str, min_len: int = 8) -> bool:
    t = (text or "").strip()
    if len(t) < min_len:
        return True
    if _NOISE_RE.match(t):
        return True
    return False

def has_toxic(text: str) -> bool:
    t = text or ""
    return any(p and p in t for p in _TOXIC_SEED)

def toxic_via_llm(text: str, classify_fn=None) -> bool:
    """운영용: 외부 분류기/LLM 훅. classify_fn(text)->bool 주입."""
    return bool(classify_fn(text)) if classify_fn else has_toxic(text)
