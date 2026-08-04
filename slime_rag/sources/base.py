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


# ---------------------------------------------------------------- chrome strip (사이트 UI 잔재)
# 크롤러 단계에서 걷어내는 '본문이 아닌 것'. 분류기보다 앞이어야 한다 — 여기서 남으면
# 임베딩·LLM 이 뉴스 헤드라인이나 닉네임을 본문 신호로 착각한다(계획 B-0 / AC5).
#
# 1) 디시 실시간 뉴스 위젯 블리드: `<헤드라인> 1 / 20 이전 다음`.
#    페이저 토큰(`n / m 이전 다음`)이 위젯의 끝이고, 헤드라인은 같은 줄 앞부분이다.
#    → 줄 시작부터 페이저까지를 통째로 제거(줄 단위라 본문 다른 줄은 보존).
_NEWS_WIDGET_RE = re.compile(r"^.*?\d+\s*/\s*\d+\s*이전\s*다음[ \t]*", re.M)
# 2) 앱 푸터. 실제 표기는 `- dc official App`(중간 공백) — 공백 없는 변형도 함께 흡수.
_APP_FOOTER_RE = re.compile(r"-\s*dc\s*official\s*app.*$", re.I | re.M)
# 3) 멘션. `@글쓴 아갤러(58.78)` 은 중간 공백 때문에 단순 `@\S+` 로 안 지워져 '아갤러'가 남고,
#    그 잔재가 메타 오탐을 유발한다. '글쓴' 접두 형태를 먼저 소비한 뒤 일반 형태를 지운다.
_MENTION_RE = re.compile(r"@(?:글쓴\s+)?\S+")


def strip_chrome(text: str) -> str:
    """본문에서 사이트 UI 잔재(뉴스 위젯·앱 푸터·멘션)를 제거한다. 무손실 정제 — 후기 텍스트 불변."""
    t = text or ""
    t = _NEWS_WIDGET_RE.sub("", t)
    t = _APP_FOOTER_RE.sub("", t)
    t = _MENTION_RE.sub("", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return "\n".join(line.strip() for line in t.splitlines()).strip()


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
    - 상한(D9): 정지 = relevant==budget OR examined>=K*limit OR 소스 자체 페이지 상한.
      수집 루프는 매 배치 전 `should_stop()` 으로 네트워크 조기 종료.
    - **순위·예산**(kind-axis-resolution §C-3 / AC10): 배치 안의 KEEP 후보를 E 신뢰도로 정렬한 뒤
      예산만큼만 흘린다. 초과분은 **드롭이 아니라 '미처리'(unprocessed)** 로 따로 세고 로깅한다 —
      침묵 절단 금지. 질문글은 걸러지는 게 아니라 꼬리로 밀려 예산이 안 닿으면 자연히 안 나간다.
    - 관측성(AC7): DROP 마다 axis/score/axes/url 로깅, 경계 KEEP 별도 마킹, 종료 시 요약 1줄.
    - 앵커 없음(target 미주입+keywords 없음) 또는 미설정 플랫폼 → 비활성(패스스루, 하위호환).
    - 임베딩은 `relevance.classify_batch` 가 배치로 처리(BGE-M3 encode 호출 amortize).
    """

    def __init__(self, platform: str, target: Optional[dict], keywords: list[str],
                 limit: int, logger=log):
        from ..relevance import RELEVANCE_CONF, RELEVANCE_K, TAU_SCOPE_MISMATCH
        self.platform = platform
        self.limit = limit
        self.log = logger
        self.target = resolve_target(target, keywords)
        self.conf = RELEVANCE_CONF.get(platform)
        self.active = bool(self.conf) and self.target is not None
        # WS1 step 10 — τ 가 다른 target_scope 로 보정됐으면 fail-loud. 잠정 기본값으로
        # 조용히 내려가는 게 이 검사가 막으려는 정확한 실패 모드다(ADR-0007).
        if self.active and platform in TAU_SCOPE_MISMATCH:
            file_scope = TAU_SCOPE_MISMATCH[platform]
            active_scope = (self.conf or {}).get("target_scope", "product")
            raise RuntimeError(
                f"relevance τ 스코프 불일치[{platform}]: 보정 파일의 τ 는 target_scope="
                f"'{file_scope}' 로 산출됐으나 운용 설정(RELEVANCE_CONF)은 target_scope="
                f"'{active_scope}' 다. 이 상태로는 활성화할 수 없다(잠정 기본값 대체 금지) — "
                f"필요 조치: '{active_scope}' 스코프에서 keep 을 재판정하고 "
                f"evals/calibrate_relevance.py 로 재보정해 relevance_tau.json 의 "
                f"target_scope 를 '{active_scope}' 로 맞출 것."
            )
        self.cap = (self.conf.get("k", RELEVANCE_K) if self.conf else RELEVANCE_K) * limit
        # 예산 N — 기본은 limit(관련 항목 수). conf['budget'] 로 소스별 상한을 따로 줄 수 있다.
        self.budget = (self.conf or {}).get("budget") or limit
        self.relevant = 0
        self.examined = 0
        self.emitted = 0                              # 패스스루 카운터
        self.unprocessed = 0                          # 예산 초과(드롭 아님) — AC10
        self.dropped = {"topic": 0, "meta": 0, "domain": 0, "e_union": 0}

    def should_stop(self) -> bool:
        if not self.active:
            return self.emitted >= self.limit
        return self.relevant >= self.budget or self.examined >= self.cap

    @staticmethod
    def _rank_key(v) -> tuple:
        """정렬 키 — E 신뢰도 버킷(2:규칙·프로브 둘 다 / 1:하나만 / 0:둘 다 음성) 우선,
        동률이면 편향 보존 항목(AC8)을 앞으로, 그다음 topic 점수."""
        return (v.e_bucket, int(v.bias_hold), v.topic_score)

    def filter(self, reviews: list[RawReview]) -> Iterator[RawReview]:
        """후보 배치 → 순위대로 예산만큼 yield. 비활성이면 limit 까지 그대로 흘림."""
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

        keepers: list[tuple[RawReview, object]] = []
        for review, v in zip(reviews, verdicts):
            if self.examined >= self.cap:             # 책임 수집 상한(D9) — 더 안 본다
                break
            self.examined += 1
            if not v.keep:
                self.dropped[v.axis] = self.dropped.get(v.axis, 0) + 1
                self.log.info("relevance drop axis=%s score=%.3f axes=%s url=%s",
                              v.axis, v.topic_score, v.axes, review.url)
                continue
            keepers.append((review, v))

        keepers.sort(key=lambda pair: self._rank_key(pair[1]), reverse=True)
        for rank, (review, v) in enumerate(keepers):
            v.rank = rank
            if self.relevant >= self.budget:
                # 예산 초과 = **미처리**. DROP 카운터에 넣지 않는다 — 둘을 섞으면 리포트가
                # "걸러냈다"와 "예산이 모자랐다"를 구분 못 하고, 그게 곧 침묵 절단이다.
                v.unprocessed = True
                self.unprocessed += 1
                self.log.info("relevance unprocessed(예산 초과, 드롭 아님) rank=%d %s url=%s",
                              rank, v.axes, review.url)
                continue
            if v.near_boundary:
                self.log.info("relevance near-boundary keep score=%.3f url=%s",
                              v.topic_score, review.url)
            review.meta["relevance"] = {
                "axis": v.axis, "topic_score": round(v.topic_score, 4),
                "M": v.M, "Q": v.Q, "E": v.E,
                "e_rule": v.e_rule, "e_probe": v.e_probe, "e_bucket": v.e_bucket,
                "bias_hold": v.bias_hold, "rank": rank,
                "near_boundary": v.near_boundary,
                "target": self.target, "target_scope": self.conf.get("target_scope", "product"),
                "tau": self.conf.get("tau_topic", 0.45),
                "rank_score": None,   # C′(ADR-0007) 미구축 — 항상 null
            }
            yield review
            self.relevant += 1

    def finish(self) -> None:
        """소스 종료 시 shortfall(AC5) + 요약 카운터(AC7) + 미처리 노출(AC10) 로깅."""
        if not self.active:
            return
        # examined==0 은 관련성 미달이 아니라 '수집할 후보가 애초에 없음'(토큰/네트워크) →
        # 그건 소스가 이미 로깅하므로 여기서 shortfall 로 오탐하지 않는다.
        if self.relevant < self.budget and self.examined > 0:
            reason = "examined 상한(K*limit) 도달" if self.examined >= self.cap else "후보 소진"
            self.log.warning(
                "relevance shortfall [%s]: relevant=%d < budget=%d (%s, examined=%d, cap=%d)",
                self.platform, self.relevant, self.budget, reason, self.examined, self.cap)
        if self.examined:
            self.log.info("relevance summary [%s] examined=%d relevant=%d unprocessed=%d dropped=%s",
                          self.platform, self.examined, self.relevant, self.unprocessed, self.dropped)
