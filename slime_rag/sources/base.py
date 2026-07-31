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
    def collect(self, keywords: list[str], limit: int = 100,
                target: Optional[dict] = None) -> Iterator[RawReview]:
        """키워드(마켓명/제품명/초성)별로 원시 후기를 yield.

        target = {"market": str|None, "slime": str} — 2층 관련성 게이트 앵커(선택). None 이면
        keywords[0]로 폴백하거나(2층) 무시한다(1층). 하위호환을 위해 기본 None.
        """
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


# ---------------------------------------------------------------- 관련성 게이트(2층)
def resolve_target(target: Optional[dict], keywords: list[str]) -> Optional[dict]:
    """관련성 앵커용 타깃 해석. 명시 target 우선; 없으면 keywords[0]을 slime 으로 폴백(계획 Step 6).
    앵커를 만들 근거가 전혀 없으면 None → 게이트는 하위호환 패스스루로 동작."""
    if target and (target.get("slime") or target.get("market")):
        return target
    for kw in (keywords or []):
        kw = (kw or "").strip()
        if kw:
            return {"market": None, "slime": kw}
    return None


class RelevanceGate:
    """
    관련성 게이트 — 수집 루프가 후보 배치(post+댓글 / 캡션들)를 넘기면 KEEP 만 흘려보낸다.

    - 비관련은 yield 안 하고 **카운트 안 함**(예산=관련 항목, 계획 §1·D8).
    - 상한(D9): 정지 = relevant==limit OR examined>=K*limit OR 소스 자체 페이지 상한.
      수집 루프는 매 배치 전 `should_stop()` 으로 네트워크 조기 종료.
    - 관측성(AC7): DROP 마다 axis/score/kind/url 로깅, 경계 KEEP 별도 마킹, 종료 시 요약 1줄.
    - 앵커 없음(target 미주입+keywords 없음) 또는 미설정 플랫폼 → 비활성(패스스루, 하위호환).
    - 임베딩은 `relevance.classify_batch` 가 배치로 처리(BGE-M3 encode 호출 amortize).
    """

    def __init__(self, platform: str, target: Optional[dict], keywords: list[str],
                 limit: int, logger=log):
        from ..relevance import RELEVANCE_CONF, RELEVANCE_K
        self.platform = platform
        self.limit = limit
        self.log = logger
        self.target = resolve_target(target, keywords)
        self.conf = RELEVANCE_CONF.get(platform)
        self.active = bool(self.conf) and self.target is not None
        self.cap = (self.conf.get("k", RELEVANCE_K) if self.conf else RELEVANCE_K) * limit
        self.relevant = 0
        self.examined = 0
        self.emitted = 0                              # 패스스루 카운터
        self.dropped = {"topic": 0, "kind": 0, "domain": 0}

    def should_stop(self) -> bool:
        if not self.active:
            return self.emitted >= self.limit
        return self.relevant >= self.limit or self.examined >= self.cap

    def filter(self, reviews: list[RawReview]) -> Iterator[RawReview]:
        """후보 배치 → KEEP 만 yield. 카운터 갱신·로그. 비활성이면 limit 까지 그대로 흘림."""
        if not reviews:
            return
        if not self.active:                           # 하위호환 패스스루
            for r in reviews:
                if self.emitted >= self.limit:
                    return
                yield r
                self.emitted += 1
            return
        from ..relevance import classify_batch
        verdicts = classify_batch(reviews, self.target, self.conf)
        for review, v in zip(reviews, verdicts):
            if self.relevant >= self.limit or self.examined >= self.cap:
                return
            self.examined += 1
            if not v.keep:
                self.dropped[v.axis] = self.dropped.get(v.axis, 0) + 1
                self.log.info("relevance drop axis=%s score=%.3f kind=%s url=%s",
                              v.axis, v.topic_score, v.kind, review.url)
                continue
            if v.near_boundary:
                self.log.info("relevance near-boundary keep score=%.3f url=%s",
                              v.topic_score, review.url)
            review.meta["relevance"] = {
                "axis": v.axis, "topic_score": round(v.topic_score, 4),
                "kind": v.kind, "near_boundary": v.near_boundary,
            }
            yield review
            self.relevant += 1

    def finish(self) -> None:
        """소스 종료 시 shortfall(AC5) + 요약 카운터(AC7) 로깅."""
        if not self.active:
            return
        # examined==0 은 관련성 미달이 아니라 '수집할 후보가 애초에 없음'(토큰/네트워크) →
        # 그건 소스가 이미 로깅하므로 여기서 shortfall 로 오탐하지 않는다.
        if self.relevant < self.limit and self.examined > 0:
            reason = "examined 상한(K*limit) 도달" if self.examined >= self.cap else "후보 소진"
            self.log.warning(
                "relevance shortfall [%s]: relevant=%d < limit=%d (%s, examined=%d, cap=%d)",
                self.platform, self.relevant, self.limit, reason, self.examined, self.cap)
        if self.examined:
            self.log.info("relevance summary [%s] examined=%d relevant=%d dropped=%s",
                          self.platform, self.examined, self.relevant, self.dropped)
