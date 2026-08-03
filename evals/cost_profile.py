# -*- coding: utf-8 -*-
"""
추출 호출 비용 구조 실측 (계획 `.omc/plans/kind-axis-resolution.md` C-0, AC11·AC14).

**왜 선행 게이트인가:** §1-E 는 "호출당 입력 토큰의 ~96%가 고정 프롬프트"라고 **추정**했다.
추정치를 구현 근거로 쓰지 않기로 했으므로(§5 위험표), 스레드 배치(C-1)에 들어가기 전에
숫자를 확정한다. 96% 가설이 깨지면 C-1 을 재검토해야 한다.

**측정 방법(추정 아님):** 벤더가 보고한 `usage.prompt_tokens` 를 LEDGER 에서 읽어 차분한다.
  고정분 = prompt_tokens(시스템 + 최소 본문)
  본문분 = prompt_tokens(시스템 + 실제 본문) − 고정분
토크나이저를 따로 들이지 않는다 — 벤더가 실제로 과금한 숫자가 유일한 근거다.

AC14(프롬프트 캐싱)는 "확인 후 리포트"까지만 요구한다. 같은 시스템 프롬프트로 연속 호출한 뒤
`usage.prompt_tokens_details.cached_tokens` 를 읽어 지원 여부를 그대로 적는다 — 코드 변경 없음.

실행:  python evals/cost_profile.py            (OPENAI_API_KEY 필요 — 없으면 skip)
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slime_rag import llm_ops
from slime_rag.extract import LAYER2_SYSTEM, extract_review
from slime_rag.llm_ops import LLM

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
MIN_BODY = "."                     # 고정분만 남기기 위한 최소 본문
N_BODIES = 6                       # 실 댓글 표본 수(비용 억제)


def sample_bodies() -> list[str]:
    """실제 디시 댓글 본문 — 합성 텍스트로 재면 길이 분포가 거짓말을 한다."""
    items = json.loads(GOLD.read_text(encoding="utf-8"))["items"]
    comments = [x["text"] for x in items
                if x["platform"] == "dcinside" and x.get("type") == "comment"]
    return comments[:N_BODIES]


def _last_ok() -> llm_ops.CallRecord:
    for rec in reversed(llm_ops.LEDGER):
        if rec.status == "ok":
            return rec
    raise RuntimeError("LEDGER 에 성공 호출이 없음")


def main() -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("· cost_profile skip (OPENAI_API_KEY 없음) — AC11 은 실호출 LEDGER 기반이다")
        return 0

    llm = LLM()
    bodies = sample_bodies()

    extract_review(MIN_BODY, llm)
    fixed = _last_ok().input_tokens

    rows = []
    for body in bodies:
        extract_review(body, llm)
        rec = _last_ok()
        rows.append({"chars": len(body), "prompt_tokens": rec.input_tokens,
                     "body_tokens": max(rec.input_tokens - fixed, 0),
                     "cached_tokens": rec.cached_tokens})

    body_tok = [r["body_tokens"] for r in rows]
    total_tok = [r["prompt_tokens"] for r in rows]
    fixed_share = [1 - b / t for b, t in zip(body_tok, total_tok)]
    cached_any = any(r["cached_tokens"] > 0 for r in rows)

    print("=" * 70)
    print("추출 호출 비용 구조 (AC11) — 벤더 보고 prompt_tokens 기반, 추정 아님")
    print("=" * 70)
    print(f"LAYER2_SYSTEM 고정 지시문: {len(LAYER2_SYSTEM):,}자")
    print(f"고정분 prompt_tokens(시스템 + 최소 본문): {fixed:,}")
    print(f"\n{'본문자수':>8}{'prompt_tok':>12}{'본문_tok':>10}{'고정비중':>10}{'cached':>9}")
    for r, share in zip(rows, fixed_share):
        print(f"{r['chars']:>8}{r['prompt_tokens']:>12}{r['body_tokens']:>10}"
              f"{share:>10.1%}{r['cached_tokens']:>9}")
    mean_share = statistics.mean(fixed_share)
    print("-" * 70)
    print(f"고정 프롬프트 비중 평균: {mean_share:.1%}  (본문 평균 {statistics.mean(body_tok):.1f} tok)")
    verdict = ("§1-E 의 '~96%' 가설 성립 — 분류 정확도는 비용 레버가 아니다. C-1(스레드 배치) 진행."
               if mean_share >= 0.90 else
               "§1-E 의 '~96%' 가설 **미성립** — C-1 착수 전 비용 모델을 다시 세울 것(§5 위험표).")
    print(f"판정: {verdict}")

    print("\n[AC14] 프롬프트 캐싱")
    if cached_any:
        print(f"      지원됨 — cached_tokens > 0 관측 (최대 {max(r['cached_tokens'] for r in rows):,})")
    else:
        print("      **미관측** — 동일 시스템 프롬프트 연속 호출에도 cached_tokens=0.")
        print("      벤더 정책상 캐시 최소 길이 미달이거나 미지원. C-1(배치)이 주 절감 수단이라")
        print("      계획은 무효화되지 않는다(§5 위험표 마지막 줄).")

    print(f"\n누적 LEDGER: {llm_ops.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
