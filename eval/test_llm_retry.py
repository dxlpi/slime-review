# -*- coding: utf-8 -*-
"""LLM 재시도 정책 게이트 — 429 **두 종류**를 가르는 계약.

이 파일이 지키는 건 한 문장이다: **기다리면 풀리는 실패와 안 풀리는 실패는 처방이 다르다.**

  · `rate_limit_exceeded`(TPM/RPM) — 일시적. 서버가 '424ms 뒤에 다시'라고까지 알려 준다.
  · `insufficient_quota` — 잔액 0. 몇 번을 기다려도 안 풀린다.

실측(2026-08-09 원문 재처리 런): 둘을 같이 다뤄서, TPM 에 걸린 런이 **잠 없이** 두 번
재시도하고 죽었다(두 시도가 밀리초 안에 소진). 잔액은 $3.81 남아 있었는데 실패 모양이
크레딧 소진과 같아 처음엔 돈 문제로 읽혔다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_llm_retry`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag import llm_ops                      # noqa: E402


import openai                                       # noqa: E402


class _RateLimitError(openai.APIError):
    """`openai.RateLimitError` 대역.

    ⚠️ **`openai.APIError` 를 상속해야 한다** — `complete()` 이 잡는 게 그 타입이라,
      평범한 Exception 으로 두면 재시도 로직을 한 줄도 안 타고 그대로 밖으로 튄다
      (이 파일을 처음 쓸 때 실제로 그렇게 통과 못 했다). 생성자는 request 객체를 요구하므로
      `Exception.__init__` 으로 우회한다.
    """
    def __init__(self, msg):
        Exception.__init__(self, msg)
        self.message = msg

    def __str__(self):
        return self.args[0]


class _ServerError(Exception):
    pass


_TPM = ("Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-5.4-mini in "
        "organization org-x on tokens per min (TPM): Limit 200000, Used 199008, Requested "
        "2606. Please try again in 424ms.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}")
_QUOTA = ("Error code: 429 - {'error': {'message': 'You have no credits remaining.', "
          "'type': 'insufficient_quota', 'code': 'credit_balance_exhausted'}}")


def test_transient_rate_limit_uses_the_servers_own_delay():
    """서버가 알려 준 값을 쓴다 — 우리가 고른 상수보다 서버가 아는 값이 정확하다."""
    assert llm_ops._retry_after_seconds(_RateLimitError(_TPM)) == 0.424
    assert llm_ops._retry_after_seconds(_RateLimitError("429 rate_limit_exceeded try "
                                                        "again in 1.5s.")) == 1.5
    # 힌트가 없으면 기본값에서 시작해 호출부가 지수 백오프로 늘린다.
    assert llm_ops._retry_after_seconds(_RateLimitError("429 rate_limit_exceeded")) == \
        llm_ops.RATE_LIMIT_BASE_DELAY
    print("✓ 일시적 429: 서버 제안 지연(ms/s) 파싱 · 없으면 기본값 OK")


def test_quota_exhaustion_is_not_waited_on():
    """잔액 소진은 **기다림이 처방이 아니다** — None 을 돌려 즉시 포기하게 한다.

    ⛔ 여기서 지연을 돌려주면 잔액 0인 계정이 매 호출마다 대기 6회를 태운다.
      1,000건짜리 런이면 몇 시간을 아무것도 못 하면서 도는 것이다.
    """
    err = _RateLimitError(_QUOTA)
    assert llm_ops._is_quota_exhausted(err) is True
    assert llm_ops._retry_after_seconds(err) is None
    assert llm_ops._is_quota_exhausted(_RateLimitError(_TPM)) is False, \
        "TPM 을 잔액 소진으로 읽으면 기다리면 풀릴 실패를 포기한다"
    print("✓ 잔액 소진: 대기 없음 · TPM 과 구분 OK")


def test_other_failures_are_not_treated_as_rate_limits():
    """5xx 등은 속도 제한이 아니다 — 대기 루프가 아니라 기존 재시도 예산으로 간다."""
    assert llm_ops._retry_after_seconds(_ServerError("Error code: 500 - server error")) is None
    print("✓ 비-429 실패는 대기 대상 아님 OK")


def test_waiting_does_not_consume_the_parse_retry_budget():
    """속도 제한 대기는 **파싱 재시도 1회**(결정성 스펙)를 갉아먹지 않는다.

    한 통에 담으면, 잠깐 TPM 에 밀렸다는 이유로 정작 파싱 실패에 쓸 재시도가 사라진다.
    여기선 첫 호출이 TPM 으로 두 번 튕긴 뒤 성공하고, 그 성공 응답이 **깨진 JSON** 이라
    파싱 재시도가 살아 있어야 최종 성공한다.
    """
    calls = {"n": 0}

    class _Msg:
        def __init__(self, content): self.content, self.refusal = content, None

    class _Usage:
        prompt_tokens = completion_tokens = 10
        prompt_tokens_details = None

    class _Resp:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": _Msg(content)})()]
            self.usage = _Usage()

    class _Completions:
        def create(self, **_kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _RateLimitError(_TPM)          # 대기 2회
            if calls["n"] == 3:
                return _Resp("{not json")            # 파싱 실패 → 예산 1회 사용
            return _Resp('{"ok": true}')

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    slept: list[float] = []
    real_sleep = llm_ops.time.sleep
    llm_ops.time.sleep = slept.append
    try:
        out = llm_ops.LLM(client=_Client()).complete(
            "x", schema={"type": "object"}, label="test.retry")
    finally:
        llm_ops.time.sleep = real_sleep

    assert out == {"ok": True}, f"대기가 파싱 재시도 예산을 먹었다: {out}"
    assert calls["n"] == 4, f"호출 횟수가 예상과 다르다: {calls['n']}"
    assert len(slept) == 2 and slept[0] == 0.424, f"서버 제안 지연으로 안 쉬었다: {slept}"
    assert slept[1] > slept[0], f"지수 백오프가 아니다: {slept}"
    print(f"✓ 대기 {len(slept)}회 후 파싱 재시도 1회가 살아 있음 (sleep={slept}) OK")


def test_wait_count_is_capped():
    """무한 대기 금지 — 상한을 넘으면 포기하고 예외로 올린다."""
    class _Completions:
        def create(self, **_kw):
            raise _RateLimitError(_TPM)

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    slept: list[float] = []
    real_sleep = llm_ops.time.sleep
    llm_ops.time.sleep = slept.append
    try:
        llm_ops.LLM(client=_Client()).complete("x", label="test.cap")
    except RuntimeError:
        pass
    else:
        raise AssertionError("상한을 넘겼는데 예외가 안 났다")
    finally:
        llm_ops.time.sleep = real_sleep

    # 파싱 예산 2회 × 대기 상한 — 각 시도가 상한만큼 기다린 뒤 포기한다.
    assert len(slept) == 2 * llm_ops.RATE_LIMIT_MAX_WAITS, f"대기 횟수 {len(slept)}"
    assert max(slept) <= llm_ops.RATE_LIMIT_MAX_DELAY, f"한 번에 너무 오래 쉰다: {max(slept)}"
    print(f"✓ 대기 상한 {llm_ops.RATE_LIMIT_MAX_WAITS}회 · 1회 최대 "
          f"{llm_ops.RATE_LIMIT_MAX_DELAY}s OK")


def test_quota_exhaustion_fails_fast():
    """잔액 0이면 남은 재시도 예산까지 태우지 않는다 — 호출 1회로 끝난다."""
    calls = {"n": 0}

    class _Completions:
        def create(self, **_kw):
            calls["n"] += 1
            raise _RateLimitError(_QUOTA)

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    slept: list[float] = []
    real_sleep = llm_ops.time.sleep
    llm_ops.time.sleep = slept.append
    try:
        llm_ops.LLM(client=_Client()).complete("x", label="test.quota")
    except RuntimeError:
        pass
    finally:
        llm_ops.time.sleep = real_sleep

    assert calls["n"] == 1, f"잔액 소진인데 {calls['n']}회 호출했다"
    assert slept == [], f"잔액 소진인데 기다렸다: {slept}"
    print("✓ 잔액 소진: 1회 호출 후 즉시 실패(대기·재시도 없음) OK")


if __name__ == "__main__":
    test_transient_rate_limit_uses_the_servers_own_delay()
    test_quota_exhaustion_is_not_waited_on()
    test_other_failures_are_not_treated_as_rate_limits()
    test_waiting_does_not_consume_the_parse_retry_budget()
    test_wait_count_is_capped()
    test_quota_exhaustion_fails_fast()
    print("\nLLM 재시도 정책 게이트 통과 ✅")
