# -*- coding: utf-8 -*-
"""
관측성 + LLM 호출 래퍼 (평가 하드 게이트: 로깅·비용·재시도).

설계 의도:
- 모든 LLM 호출은 이 한 곳을 통과한다 → 지연·토큰·비용·상태를 빠짐없이 기록.
- 벤더를 인터페이스 뒤에 숨긴다 → 교체 가능(이 파일은 OpenAI 구현이지만
  `LLM.complete` 시그니처는 벤더 무관. Anthropic→OpenAI 전환 시 파이프라인 무변경).
- 벤더 SDK(`openai`)는 **지연 임포트**한다 → LLM 을 호출하지 않는 경로(예: `extract.LAYER1_SCHEMA`
  같은 순수 스키마 상수)가 SDK 설치 없이 임포트된다. 오프라인 테스트·CI 가 벤더에 묶이지 않게
  하는 것이 목적이며, '벤더는 이 파일 뒤에만' 원칙의 연장이다.
- JSON 추출은 structured outputs(`response_format` json_schema, strict)로 강제.
  주의: GPT-5 계열은 추론 모델이라 temperature 가 무시/제한될 수 있다.
        결정성은 temperature 가 아니라 '스키마 강제'로 확보한다(그래서 temperature 미전송).
"""

from __future__ import annotations
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:                    # 타입 힌트 전용 — 런타임엔 로드되지 않는다.
    import openai

from .config import settings

log = logging.getLogger("llm_ops")

# 비용 집계 단가 (USD / 1M tokens). 출처: developers.openai.com/api/docs/pricing (2026-06).
# cached_input: 동일 출처 재확인(2026-08-04) — gpt-5.4/5.5 계열은 캐시 적중 시 입력가의
# 90% 할인(= input 의 1/10)이 공통 규칙이라 표로 그대로 확인됨. 모델에 cached_input 키가
# 없으면 _cost_usd 가 전량 input 단가로 청구한다(구모델 동작 불변).
PRICING = {
    "gpt-5.4":      {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-5.5":      {"input": 5.00, "cached_input": 0.50, "output": 30.00},
}


def _cost_usd(model: str, usage) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    cached = _cached_tokens(usage)
    cached_rate = p.get("cached_input", p["input"])   # 미지원 모델은 input 단가로 대체 청구
    uncached = usage.prompt_tokens - cached
    return (uncached * p["input"] + cached * cached_rate
            + usage.completion_tokens * p["output"]) / 1_000_000


def _cached_tokens(usage) -> int:
    """벤더가 보고한 프롬프트 캐시 적중 토큰. 필드가 없으면 0(미지원).

    고정 시스템 프롬프트가 2,000자대인데 본문은 수십 자라(계획 §1-E) 캐시 적중 여부가
    추출 비용을 좌우한다. 지원 여부 자체가 관측 대상이므로 조용히 삼키지 않고 0으로 기록한다.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    return int(getattr(details, "cached_tokens", 0) or 0) if details else 0


def _schema_name(label: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", label) or "structured_output"
    return name[:64]


@dataclass
class CallRecord:
    label: str
    model: str
    status: str                 # 'ok' | 'parse_error' | 'refusal' | 'api_error'
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0      # 프롬프트 캐시 적중분(AC14). 벤더 미지원이면 0.
    cost_usd: float = 0.0
    attempts: int = 1
    error: Optional[str] = None


# 세션 단위 원장(ledger). 대시보드/리포트는 이걸 집계한다.
LEDGER: list[CallRecord] = []


def summary() -> dict:
    """누적 호출·토큰·비용 집계."""
    return {
        "calls": len(LEDGER),
        "errors": sum(1 for r in LEDGER if r.status != "ok"),
        "input_tokens": sum(r.input_tokens for r in LEDGER),
        "cached_tokens": sum(r.cached_tokens for r in LEDGER),
        "output_tokens": sum(r.output_tokens for r in LEDGER),
        "cost_usd": round(sum(r.cost_usd for r in LEDGER), 4),
    }


class LLM:
    """모든 LLM 호출의 단일 통로."""

    def __init__(self, client: Optional[openai.OpenAI] = None):
        import openai                # 지연 로드(모듈 docstring). sys.modules 캐시라 반복 비용 없음.
        # OPENAI_API_KEY 를 환경에서 자동 해석.
        self.client = client or openai.OpenAI()

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        schema: Optional[dict] = None,
        max_tokens: int = 4096,
        effort: Optional[str] = None,
        label: str = "",
    ) -> Any:
        """
        schema 가 있으면 JSON(dict) 을, 없으면 텍스트(str) 를 반환.
        파싱 실패 시 1회 재시도(스펙). 모든 시도를 LEDGER 에 기록.
        """
        import openai                # 지연 로드 — 아래 `except openai.APIError` 에서 필요.

        model = model or settings.model_extract
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,   # 추론 모델은 max_tokens 대신 이걸 받음
        }
        if schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": _schema_name(label), "strict": True, "schema": schema},
            }
        if effort:
            kwargs["reasoning_effort"] = effort     # 'low'|'medium'|'high'

        last_err: Optional[str] = None
        for attempt in range(1, 3):                  # 최대 2회(최초 + 재시도 1)
            t0 = time.monotonic()
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except openai.APIError as e:
                last_err = f"{type(e).__name__}: {e}"
                LEDGER.append(CallRecord(label, model, "api_error",
                                         int((time.monotonic() - t0) * 1000),
                                         attempts=attempt, error=last_err))
                log.warning("LLM api_error [%s] %s", label, last_err)
                continue

            latency = int((time.monotonic() - t0) * 1000)
            usage = resp.usage
            msg = resp.choices[0].message

            if getattr(msg, "refusal", None):        # 안전상 거부 → 재시도해도 안 풀림
                last_err = f"refusal: {msg.refusal}"
                LEDGER.append(CallRecord(label, model, "refusal", latency,
                                         usage.prompt_tokens, usage.completion_tokens,
                                         _cached_tokens(usage), _cost_usd(model, usage),
                                         attempt, last_err))
                break

            text = msg.content or ""
            if not schema:
                LEDGER.append(CallRecord(label, model, "ok", latency,
                                         usage.prompt_tokens, usage.completion_tokens,
                                         _cached_tokens(usage), _cost_usd(model, usage), attempt))
                return text
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                last_err = f"JSONDecodeError: {e}"
                LEDGER.append(CallRecord(label, model, "parse_error", latency,
                                         usage.prompt_tokens, usage.completion_tokens,
                                         _cached_tokens(usage), _cost_usd(model, usage),
                                         attempt, last_err))
                log.warning("LLM parse_error [%s] 재시도", label)
                continue
            LEDGER.append(CallRecord(label, model, "ok", latency,
                                     usage.prompt_tokens, usage.completion_tokens,
                                     _cached_tokens(usage), _cost_usd(model, usage), attempt))
            return parsed

        raise RuntimeError(f"LLM 호출 실패 [{label}]: {last_err}")
