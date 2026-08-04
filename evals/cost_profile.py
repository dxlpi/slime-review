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

실행:  python evals/cost_profile.py            (OPENAI_API_KEY 필요 — 없으면 skip, 댓글 단건 경로)
       python evals/cost_profile.py --thread   (스레드 배치 경로, WS3 §1-2 — `extract_thread` 실측)

스레드 모드(`--thread`)는 계획 `three-remaining-items-plan.md` Workstream 3 step 1-2:
`LAYER2_THREAD_SYSTEM` 고정 토큰(n=1 최소 본문) + n∈{12,16,20,24} 배치의 `prompt_tokens`/
`cached_tokens`/`cost_usd`(캐시 인지 청구, `llm_ops._cost_usd`)를 LEDGER 에서 그대로 읽는다
(직접 토큰 추정 금지). 배치 원문은 `data/dcinside_sample_raw.json`(gitignore, 실 디시 원문)의
글 번호(`no=`)로 스레드를 묶어 큰 것부터 소진 — 한 스레드가 n 에 못 미치면 다음 스레드를
이어붙인다(합성 배치, 조합 내역을 리포트에 기록). n=12 는 콜드(루프의 첫 호출)와 웜(재호출)을
둘 다 재서 캐시 효과를 대조하고, 웜 호출의 `cost_usd/12` 를 "본문만 이론적 바닥"
((prompt−fixed) tokens × input 단가 + output 단가, 캐시 미적용)과 비교한 비율을 함께 낸다 —
계획 §3 의 캐시 하드스톱 판정(±10%)이 쓰는 수치.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slime_rag import llm_ops
from slime_rag.extract import LAYER2_SYSTEM, LAYER2_THREAD_SYSTEM, extract_review, extract_thread
from slime_rag.llm_ops import LLM

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
MIN_BODY = "."                     # 고정분만 남기기 위한 최소 본문
N_BODIES = 6                       # 실 댓글 표본 수(비용 억제)

DC_SAMPLE = Path(__file__).resolve().parent.parent / "data" / "dcinside_sample_raw.json"
THREAD_NS = [12, 16, 20, 24]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
THREAD_REPORT_PATH = RESULTS_DIR / "cost_profile_thread.json"
_NO_RE = re.compile(r"[?&]no=(\d+)")


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


def run_comment_mode() -> int:
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


# ================================================================== 스레드 모드 (WS3 §1-2)
def _load_dc_threads() -> list[dict]:
    """`data/dcinside_sample_raw.json`(실 디시 원문)을 글 번호(`no=`) 기준으로 스레드로 묶는다.

    반환은 조각 수 내림차순 — n 을 채울 때 소진할 스레드 개수(=합성 배치의 조합 수)를
    최소화하기 위함이다. 각 스레드: {"no", "title"(글 제목, 없으면 None), "texts"(글+댓글 순서)}.
    """
    data = json.loads(DC_SAMPLE.read_text(encoding="utf-8"))
    groups: dict[str, dict] = {}
    order: list[str] = []
    for it in data["items"]:
        m = _NO_RE.search(it.get("url") or "")
        no = m.group(1) if m else "NA"
        if no not in groups:
            groups[no] = {"no": no, "title": None, "texts": []}
            order.append(no)
        g = groups[no]
        if it.get("type") == "post":
            g["title"] = it.get("parent_title")
        g["texts"].append(it["text"])
    return sorted((groups[no] for no in order), key=lambda g: -len(g["texts"]))


def _build_thread_batch(n: int, threads: list[dict]) -> tuple[str | None, list[str], list[dict]]:
    """실 스레드들을 큰 것부터 소진해 길이 n 짜리 조각 배치를 만든다.

    한 스레드가 n 에 못 미치면 다음 스레드를 이어붙인다 — **합성 배치**(진짜 원문 조각들의
    조합이지 생성 텍스트가 아니다). `composition` 이 어느 스레드에서 몇 개를 가져왔는지 기록한다.
    """
    texts: list[str] = []
    composition: list[dict] = []
    title: str | None = None
    for g in threads:
        if len(texts) >= n:
            break
        take = g["texts"][: n - len(texts)]
        if not take:
            continue
        if title is None:
            title = g["title"]
        composition.append({"thread_no": g["no"], "used": len(take), "of": len(g["texts"])})
        texts.extend(take)
    if len(texts) < n:
        raise RuntimeError(
            f"data/dcinside_sample_raw.json 에 조각이 부족함(n={n} 요청, {len(texts)}개 확보)")
    return title, texts[:n], composition


def run_thread_mode() -> int:
    llm = LLM()
    threads = _load_dc_threads()

    # (a) LAYER2_THREAD_SYSTEM 고정 토큰 — n=1, 최소 본문
    extract_thread(None, [MIN_BODY], llm)
    fixed = _last_ok().input_tokens

    # (b)+(c) n ∈ {12,16,20,24} — extract_thread 는 MAX_THREAD_SOURCES 를 클램프하지 않는다
    # (그건 extract_collected 의 배치 루프에만 있다 — extract.py 를 고치지 않고 직접 호출로 확인).
    rows = []
    for n in THREAD_NS:
        title, texts, composition = _build_thread_batch(n, threads)
        extract_thread(title, texts, llm)
        rec = _last_ok()
        body_tok = max(rec.input_tokens - fixed, 0)
        rows.append({
            "n": n,
            "prompt_tokens": rec.input_tokens,
            "body_tokens": body_tok,
            "cached_tokens": rec.cached_tokens,
            "fixed_share": round(1 - body_tok / rec.input_tokens, 4) if rec.input_tokens else None,
            "cost_usd": round(rec.cost_usd, 6),
            "cost_usd_per_fragment": round(rec.cost_usd / n, 8),
            "composition": composition,
        })

    # n=12 웜 캐시 재호출 — 같은 조합을 한 번 더 보내 콜드(위 루프의 첫 행) vs 웜을 대조한다.
    title12, texts12, composition12 = _build_thread_batch(12, threads)
    extract_thread(title12, texts12, llm)
    warm = _last_ok()
    warm_row = {
        "n": 12,
        "prompt_tokens": warm.input_tokens,
        "cached_tokens": warm.cached_tokens,
        "cost_usd": round(warm.cost_usd, 6),
        "cost_usd_per_fragment": round(warm.cost_usd / 12, 8),
        "composition": composition12,
    }

    # 본문만 이론적 바닥 — 캐시 미적용, (prompt−fixed) 를 input 단가로 + output 을 output 단가로.
    # 웜 호출의 cost_usd/12 와 이 바닥을 비교한 비율이 계획 §3 캐시 하드스톱 판정(±10%) 수치다.
    price = llm_ops.PRICING.get(warm.model, {})
    warm_body_tok = max(warm.input_tokens - fixed, 0)
    floor_usd = (warm_body_tok * price.get("input", 0.0)
                 + warm.output_tokens * price.get("output", 0.0)) / 1_000_000
    floor_per_fragment = floor_usd / 12 if floor_usd else 0.0
    ratio = (warm.cost_usd / floor_usd) if floor_usd else None

    print("=" * 78)
    print("스레드 배치 비용 구조 (WS3 §1-2) — 벤더 보고 prompt_tokens/cost_usd 기반, 추정 아님")
    print("=" * 78)
    print(f"LAYER2_THREAD_SYSTEM 고정 지시문: {len(LAYER2_THREAD_SYSTEM):,}자")
    print(f"고정분 prompt_tokens(시스템 + n=1 최소 본문): {fixed:,}  (model={warm.model})")
    print(f"\n{'n':>4}{'prompt_tok':>12}{'본문_tok':>10}{'고정비중':>10}"
          f"{'cached':>9}{'cost_usd':>12}{'cost/fragment':>16}")
    for r in rows:
        print(f"{r['n']:>4}{r['prompt_tokens']:>12}{r['body_tokens']:>10}"
              f"{r['fixed_share']:>10.1%}{r['cached_tokens']:>9}"
              f"{r['cost_usd']:>12.6f}{r['cost_usd_per_fragment']:>16.8f}")
        for c in r["composition"]:
            print(f"       └ thread {c['thread_no']}: {c['used']}/{c['of']} 조각 사용")

    print(f"\n[n=12 콜드 vs 웜 캐시]")
    cold_row = rows[0]
    print(f"  콜드(루프 첫 호출): cached_tokens={cold_row['cached_tokens']:>6}  "
          f"cost_usd={cold_row['cost_usd']:.6f}  cost/fragment={cold_row['cost_usd_per_fragment']:.8f}")
    print(f"  웜(재호출)        : cached_tokens={warm_row['cached_tokens']:>6}  "
          f"cost_usd={warm_row['cost_usd']:.6f}  cost/fragment={warm_row['cost_usd_per_fragment']:.8f}")

    print(f"\n[본문만 이론적 바닥] (prompt−fixed)={warm_body_tok} tok × input단가 "
          f"+ output={warm.output_tokens} tok × output단가 (캐시 미적용)")
    print(f"  floor_cost_usd={floor_usd:.6f}  floor_cost/fragment={floor_per_fragment:.8f}")
    if ratio is not None:
        within_10pct = abs(ratio - 1.0) <= 0.10
        print(f"  웜 cost/fragment ÷ floor = {ratio:.3f}"
              f"  → {'±10% 이내 — 캐시 하드스톱 성립(12 유지)' if within_10pct else '±10% 초과 — 캐시만으론 하드스톱 불성립, attribution 게이트로'}")
    else:
        print("  floor_usd=0 (가격표에 이 모델 없음) — 비율 계산 불가")

    print(f"\n누적 LEDGER: {llm_ops.summary()}")

    report = {
        "system_fixed_prompt_tokens": fixed,
        "model": warm.model,
        "rows": rows,
        "warm_n12": warm_row,
        "floor": {
            "body_tokens": warm_body_tok,
            "output_tokens": warm.output_tokens,
            "floor_cost_usd": round(floor_usd, 6),
            "floor_cost_usd_per_fragment": round(floor_per_fragment, 8),
            "warm_over_floor_ratio": round(ratio, 4) if ratio is not None else None,
        },
        "ledger_summary": llm_ops.summary(),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    THREAD_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n리포트 저장: {THREAD_REPORT_PATH}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="추출 호출 비용 구조 실측")
    ap.add_argument("--thread", action="store_true",
                     help="스레드 배치(extract_thread) 경로 측정(WS3 §1-2). 기본은 기존 댓글 단건 경로.")
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("· cost_profile skip (OPENAI_API_KEY 없음) — AC11/WS3 은 실호출 LEDGER 기반이다")
        return 0

    return run_thread_mode() if args.thread else run_comment_mode()


if __name__ == "__main__":
    raise SystemExit(main())
