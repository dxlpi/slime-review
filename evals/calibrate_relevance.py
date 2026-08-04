# -*- coding: utf-8 -*-
"""
τ_topic 보정 (US-006 Step 5) — 골드셋으로 소스별 관련성 임계값을 근거 기반으로 산출.

추측 금지(D5): τ 는 실텍스트 골드셋의 KEEP/DROP 라벨에 대한 topic_score 분포에서 결정한다.
방법: 소스별로 train/holdout 분할 → τ grid 스캔 → **precision ≥ 0.90 제약 하 recall 최대점** 선택
→ holdout 에서 재측정(AC4). 캡션 vs 포럼 분포가 가까우면 단일 τ 로 축소(리포트에 명시).

산출: --write 시 evals/gold/relevance_tau.json 에 소스별 τ 기입(relevance.py 가 import 때 반영).
리포트: --report 시 소스별 τ·precision·recall·분포 요약 출력.

실행:
    python evals/calibrate_relevance.py --report
    python evals/calibrate_relevance.py --write        # τ 를 relevance_tau.json 에 반영

추가(kind-axis-resolution AC8/AC9): 편향 보존 하드게이트 · 거짓 DROP 회귀 · 버킷별 순도 리포트.

주의: 보정 대상은 topic 축(코사인 임계) 하나다 — M/Q/E 축은 `relevance_rules` + E 프로브가 독립
      작동한다. 다만 **평가 모델은 런타임 전체 게이트와 동형**이어야 한다(D2, 평가측 수정):
      런타임은 M 이면 DROP, 아니면 `candidate`(=E 합집합 ∪ bias_hold) 가 keep 을 정한다
      (relevance.py:301-317). E 합집합 음성은 '관련성 DROP' 이 아니라 후보 탈락이지만, 결과적으로
      추출되지 않으므로 τ 를 그 사실 위에서 고르지 않으면 τ 가 허구가 된다. 따라서 주 수치는
      full gate 이고, 이전 리포트와의 연속성을 위해 M-only 수치도 함께 낸다.
      앵커 역시 런타임과 같은 `build_anchor(..., scope=conf["target_scope"])` 한 경로로 만든다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# 직접 실행(python evals/calibrate_relevance.py) 시 repo 루트를 경로에 추가(-m 없이도 동작).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import slime_rag.relevance as R
from slime_rag import relevance_rules as rules

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
TAU_OUT = Path(__file__).resolve().parent / "gold" / "relevance_tau.json"
# 그리드 하한(WS1): 마켓 스코프 앵커("<마켓> 슬라임")는 제품 스코프보다 코사인이 낮게 깔려서
# 0.30 하한이 물릴 수 있다. **실측(2026-08-04): 물리지 않는다** — 0.20 까지 내려 스캔해도 디시
# full-gate 곡선은 τ≤0.30 에서 평평(holdout P=0.733 R=0.500)이고 선택 τ 는 0.375 로 동일했다.
# recall 이 모자란 원인은 하한이 아니라 아래 recall_ceiling(후보 집합)이다 → 하한 0.30 유지.
GRID_MIN, GRID_MAX, GRID_STEP = 0.30, 0.65, 0.025
GRID = [round(float(x), 3) for x in np.arange(GRID_MIN, GRID_MAX + 1e-9, GRID_STEP)]
PRECISION_FLOOR = 0.90
RECALL_FLOOR = 0.80
# 판별력 가드(§7): chosen-τ 가 "전부 KEEP" 베이스라인보다 실제로 나은지. τ=grid min 은 M 축이
# 여전히 드롭하므로 베이스라인이 아니다 — 베이스라인은 **문자 그대로 전 항목 pred True**.
GUARD_F1_MARGIN = 0.05
GUARD_MIN_NEGATIVES = 10


def _split(items: list[dict]) -> tuple[list, list]:
    """결정적 train/holdout 분할 — id 정렬 후 3번째마다 holdout(랜덤 미사용)."""
    ordered = sorted(items, key=lambda x: x.get("id", ""))
    holdout = [x for i, x in enumerate(ordered) if i % 3 == 0]
    train = [x for i, x in enumerate(ordered) if i % 3 != 0]
    return train, holdout


def _item_signals(item: dict, conf: dict) -> tuple[float, bool, bool]:
    """항목별 (topic_score, m_drop, candidate) 1회 계산 — τ 그리드 전체에 재사용(재임베딩 없음).

    앵커는 **런타임과 같은 한 경로**로 만든다: `build_anchor(..., domain=, scope=)` — scope 는
    `RELEVANCE_CONF["target_scope"]`(디시=market / 인스타=product). 그래야 평가가 '출하된
    플랫폼별 방침'을 채점한다.

    게이트 모델(D2 평가측 수정): mqe_axis 소스는 런타임 `relevance.mqe_signals` 를 그대로 쓴다.
    런타임 `_verdict` 는 M 이면 DROP, 아니면 `candidate`(=E 합집합 ∪ bias_hold)가 keep 을
    결정한다(relevance.py:301-317) — 평가도 그 e_union 드롭을 **모델링**한다(런타임 무수정).
    mqe_axis 가 아닌 소스(인스타)는 candidate=True 로 두어 기존 모델(topic 단독)을 보존한다.
    """
    domain = bool(conf.get("domain_gate", False))
    anchor = R.build_anchor(item["collected_for"], domain=domain,
                            scope=conf.get("target_scope", "product")) or " "
    chunks = R.chunk(item["text"]) or [item["text"]]
    vecs = R._embed([anchor] + chunks)
    cvecs = np.asarray(vecs[1:], dtype=float)
    topic = R._max_cosine(cvecs, np.asarray(vecs[0], dtype=float))
    if not conf.get("mqe_axis"):
        return topic, False, True
    sig = R.mqe_signals(item["text"], R._normalize(cvecs).mean(axis=0), conf)
    return topic, bool(sig["M"]), bool(sig["candidate"])


def _pr_from_signals(sig: list[tuple[float, bool, bool, bool]], tau: float,
                     *, full_gate: bool = True) -> tuple[float, float, int, int, int]:
    """sig=(topic, m_drop, candidate, label_keep).

    full_gate=True (기본, AC4 판정 대상): 예측 KEEP = topic≥τ ∧ not M ∧ candidate — 런타임 전체 게이트.
    full_gate=False (연속성 지표): 예측 KEEP = topic≥τ ∧ not M — 이전 리포트와 같은 M-only 모델.
    """
    tp = fp = fn = 0
    for topic, m_drop, cand, keep in sig:
        pred = (topic >= tau) and not m_drop and (cand or not full_gate)
        tp += pred and keep
        fp += pred and not keep
        fn += (not pred) and keep
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall, tp, fp, fn


def _f1(p: float, r: float) -> float:
    return (2 * p * r / (p + r)) if (p + r) else 0.0


def _keep_all_baseline(sig: list[tuple[float, bool, bool, bool]]) -> tuple[float, float, float, int, int, int]:
    """판별력 가드의 베이스라인 — **문자 그대로 전 항목 pred True**(M 드롭도 없음)."""
    tp = sum(1 for *_x, keep in sig if keep)
    fp = sum(1 for *_x, keep in sig if not keep)
    p = tp / (tp + fp) if (tp + fp) else 1.0
    return p, 1.0, _f1(p, 1.0), tp, fp, 0


def _choose_tau(sig: list[tuple[float, bool, bool, bool]]) -> tuple[float, str]:
    """precision≥0.90 제약 하 recall 최대. 없으면 precision 최대(→recall) 폴백. (full-gate 모델)"""
    feasible = []
    for tau in GRID:
        p, r, *_ = _pr_from_signals(sig, tau)
        if p >= PRECISION_FLOOR:
            feasible.append((r, -tau, tau))         # recall 최대, 동률이면 낮은 τ(recall 우선)
    if feasible:
        feasible.sort(reverse=True)
        return feasible[0][2], "precision≥0.90 제약 하 recall 최대"
    best = max(GRID, key=lambda t: (_pr_from_signals(sig, t)[0], _pr_from_signals(sig, t)[1]))
    return best, "제약 불충족 → precision 최대 폴백"


def calibrate() -> dict:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    items = gold["items"]
    platforms = sorted({x["platform"] for x in items})
    report = {"grid": [GRID[0], GRID[-1]], "precision_floor": PRECISION_FLOOR,
              "recall_floor": RECALL_FLOOR, "by_platform": {}, "tau_topic": {},
              "target_scope": {p: R.RELEVANCE_CONF.get(p, {}).get("target_scope", "product")
                               for p in platforms},
              "eval": "primary=full gate(topic τ ∧ not M ∧ candidate, 런타임 동형) / m_only=연속성 지표"}
    for plat in platforms:
        subset = [x for x in items if x["platform"] == plat]
        conf = R.RELEVANCE_CONF.get(plat, {})
        train, holdout = _split(subset)
        tr_sig = [(*_item_signals(x, conf), x["label"]["keep"]) for x in train]
        ho_sig = [(*_item_signals(x, conf), x["label"]["keep"]) for x in holdout]
        tau, rule = _choose_tau(tr_sig if tr_sig else ho_sig)
        p_tr, r_tr, tp_tr, fp_tr, fn_tr = _pr_from_signals(tr_sig, tau) if tr_sig else (0, 0, 0, 0, 0)
        p_ho, r_ho, tp, fp, fn = _pr_from_signals(ho_sig, tau) if ho_sig else (0, 0, 0, 0, 0)
        # M-only(이전 리포트 연속성) — 같은 τ 에서 candidate 조건만 뺀 수치
        m_tr = _pr_from_signals(tr_sig, tau, full_gate=False) if tr_sig else (0, 0, 0, 0, 0)
        m_ho = _pr_from_signals(ho_sig, tau, full_gate=False) if ho_sig else (0, 0, 0, 0, 0)
        keep_scores = [s[0] for s in tr_sig + ho_sig if s[3]]
        drop_scores = [s[0] for s in tr_sig + ho_sig if not s[3]]
        meets_ac4 = (p_ho >= PRECISION_FLOOR and r_ho >= RECALL_FLOOR) if ho_sig else None
        report["tau_topic"][plat] = tau
        report["by_platform"][plat] = {
            "n": len(subset), "n_keep": len(keep_scores), "n_drop": len(drop_scores),
            "tau": tau, "rule": rule,
            "target_scope": conf.get("target_scope", "product"),
            "train": {"precision": round(p_tr, 3), "recall": round(r_tr, 3), "n": len(train),
                      "tp": tp_tr, "fp": fp_tr, "fn": fn_tr},
            "holdout": {"precision": round(p_ho, 3), "recall": round(r_ho, 3),
                        "n": len(holdout), "tp": tp, "fp": fp, "fn": fn},
            "m_only": {
                "train": {"precision": round(m_tr[0], 3), "recall": round(m_tr[1], 3),
                          "tp": m_tr[2], "fp": m_tr[3], "fn": m_tr[4]},
                "holdout": {"precision": round(m_ho[0], 3), "recall": round(m_ho[1], 3),
                            "tp": m_ho[2], "fp": m_ho[3], "fn": m_ho[4]},
            },
            "keep_score_mean": round(float(np.mean(keep_scores)), 3) if keep_scores else None,
            "drop_score_mean": round(float(np.mean(drop_scores)), 3) if drop_scores else None,
            "meets_AC4": meets_ac4,
        }
        # --- 판별력 가드(§7): mqe_axis 소스(디시)의 홀드아웃에서만 판정 ---
        if conf.get("mqe_axis") and ho_sig:
            b_p, b_r, b_f1, b_tp, b_fp, b_fn = _keep_all_baseline(ho_sig)
            f1 = _f1(p_ho, r_ho)
            negatives = sum(1 for *_x, keep in ho_sig if not keep)
            # τ 와 무관한 recall 상한 — τ=0 이어도 M/candidate 축이 이미 버린 KEEP 은 못 되찾는다.
            # 이 값이 RECALL_FLOOR 미만이면 **어떤 τ 로도 AC4 는 불가능**하다(τ 문제가 아님).
            n_keep_ho = sum(1 for *_x, keep in ho_sig if keep)
            reachable = sum(1 for t, m, c, keep in ho_sig if keep and not m and c)
            ceiling = (reachable / n_keep_ho) if n_keep_ho else None
            checks = {
                "meets_AC4": bool(meets_ac4),
                "f1_margin_ok": bool(f1 >= b_f1 + GUARD_F1_MARGIN),
                "fp_below_baseline": bool(fp < b_fp),
                "negatives_ok": bool(negatives >= GUARD_MIN_NEGATIVES),
            }
            report["by_platform"][plat]["guard"] = {
                **checks, "passed": all(checks.values()),
                "f1": round(f1, 3), "baseline_f1": round(b_f1, 3),
                "f1_margin": round(f1 - b_f1, 3), "f1_margin_required": GUARD_F1_MARGIN,
                "fp": fp, "baseline_fp": b_fp,
                "baseline": {"precision": round(b_p, 3), "recall": round(b_r, 3),
                             "tp": b_tp, "fp": b_fp, "fn": b_fn},
                "holdout_negatives": negatives, "negatives_required": GUARD_MIN_NEGATIVES,
                "recall_ceiling": round(ceiling, 3) if ceiling is not None else None,
                "recall_ceiling_reachable": reachable, "recall_ceiling_n_keep": n_keep_ho,
                "ac4_feasible_at_any_tau": bool(ceiling is not None and ceiling >= RECALL_FLOOR),
            }
    # 캡션 vs 포럼 τ 근접 시 단일 τ 축소 여부 안내
    taus = list(report["tau_topic"].values())
    report["single_tau_ok"] = (max(taus) - min(taus) <= 0.05) if len(taus) > 1 else True
    return report


def print_report(rep: dict) -> None:
    print("=" * 64)
    print("τ_topic 보정 리포트 (US-006 Step 5)")
    print("=" * 64)
    print("주 모델 = full gate (topic≥τ ∧ not M ∧ candidate) — AC4 는 이 수치로 판정.")
    print("보조 = M-only (topic≥τ ∧ not M) — 이전 리포트와의 연속성 지표일 뿐.")
    for plat, r in rep["by_platform"].items():
        ac4 = {True: "✅", False: "❌", None: "—"}[r["meets_AC4"]]
        m = r["m_only"]
        print(f"\n[{plat}] n={r['n']} (keep {r['n_keep']}/drop {r['n_drop']})  τ={r['tau']}  "
              f"scope={r['target_scope']}  ({r['rule']})")
        print(f"    [full] train   P={r['train']['precision']} R={r['train']['recall']} (n={r['train']['n']}, "
              f"tp={r['train']['tp']} fp={r['train']['fp']} fn={r['train']['fn']})")
        print(f"    [full] holdout P={r['holdout']['precision']} R={r['holdout']['recall']} "
              f"(n={r['holdout']['n']}, tp={r['holdout']['tp']} fp={r['holdout']['fp']} fn={r['holdout']['fn']})  AC4 {ac4}")
        print(f"    [M-only] train   P={m['train']['precision']} R={m['train']['recall']} "
              f"(tp={m['train']['tp']} fp={m['train']['fp']} fn={m['train']['fn']})")
        print(f"    [M-only] holdout P={m['holdout']['precision']} R={m['holdout']['recall']} "
              f"(tp={m['holdout']['tp']} fp={m['holdout']['fp']} fn={m['holdout']['fn']})")
        print(f"    score 평균: keep={r['keep_score_mean']} drop={r['drop_score_mean']}")
        g = r.get("guard")
        if g:
            mark = "✅ PASS" if g["passed"] else "❌ FAIL"
            print(f"    [판별력 가드] {mark}  (holdout, full-gate 기준)")
            print(f"        (i)   meets_AC4                = {g['meets_AC4']}")
            print(f"        (ii)a F1={g['f1']} vs 전부KEEP 베이스라인 F1={g['baseline_f1']} "
                  f"(margin {g['f1_margin']:+.3f} ≥ {g['f1_margin_required']}) = {g['f1_margin_ok']}")
            print(f"        (ii)b fp={g['fp']} < 베이스라인 fp={g['baseline_fp']} = {g['fp_below_baseline']}")
            print(f"        (iii) tp={r['holdout']['tp']} fp={r['holdout']['fp']} fn={r['holdout']['fn']} "
                  f"| 베이스라인 tp={g['baseline']['tp']} fp={g['baseline']['fp']} fn={g['baseline']['fn']}")
            print(f"        (iv)  홀드아웃 음성 {g['holdout_negatives']} ≥ {g['negatives_required']} "
                  f"= {g['negatives_ok']}")
            print(f"        (v)   recall 상한(τ 무관) = {g['recall_ceiling']} "
                  f"({g['recall_ceiling_reachable']}/{g['recall_ceiling_n_keep']} KEEP 이 not M ∧ candidate) "
                  f"| 어떤 τ 로도 AC4 가능 = {g['ac4_feasible_at_any_tau']}")
            if not g["negatives_ok"]:
                print("        ⛔ STOP — 홀드아웃 음성 부족: 이 τ 로 판별력을 주장할 수 없다(디시 음성 보강 필요).")
            if not g["ac4_feasible_at_any_tau"]:
                print(f"        ⛔ STOP — recall 상한 {g['recall_ceiling']} < 하한 {RECALL_FLOOR}: "
                      f"τ 를 어떻게 잡아도 AC4 미달이다. 원인은 임계가 아니라 후보 집합(E 합집합)과 "
                      f"골드 keep 정의의 불일치 — τ 를 쓰기 전에 방침 결정이 필요하다.")
    print("-" * 64)
    print(f"소스별 τ: {rep['tau_topic']}  | 단일 τ 축소 가능: {rep['single_tau_ok']}")
    print(f"타깃 스코프: {rep['target_scope']}")
    print(f"임계: precision≥{rep['precision_floor']}, recall≥{rep['recall_floor']} | "
          f"grid {rep['grid'][0]}~{rep['grid'][1]}")


# ---------------------------------------------------------------- AC8/AC9/버킷 순도 (3축)
# AC9 거짓 DROP 회귀 — v1 이 잘못 버렸던 항목들. 전부 후보로 남아야 한다.
AC9_IDS = {
    "dc-000": "5제품 후기(초코비크런키…) — 명시적 '간단후기'",
    "dc-018": "'그래놀라진짜개좋음' — 초안이 chitchat/DROP 으로 오라벨",
    "dc-062": "'카피바라는 만져봤는데 진짜 극락임'",
    "dc-105": "'ㄴㅈ 퀄 진짜 좋아'",
}


def _mqe_for(item: dict) -> dict:
    """항목 하나의 3축 신호 — 런타임과 같은 `relevance.mqe_signals` 를 쓴다(규칙 중복 금지)."""
    conf = R.RELEVANCE_CONF.get(item["platform"], {})
    chunks = R.chunk(item["text"]) or [item["text"]]
    mean_vec = R._normalize(np.asarray(R._embed(chunks), dtype=float)).mean(axis=0)
    return R.mqe_signals(item["text"], mean_vec, conf)


def axis_report(items: list[dict]) -> dict:
    """
    3축 축별 리포트. **topic 축은 여기서 보지 않는다** — AC8/AC9 는 'M/Q/E 축이 부당하게 버리지
    않는가'를 묻는 것이고, topic 컷은 별도 축(τ 보정)이라 섞으면 원인을 못 가린다.
    """
    dc = [x for x in items if x["platform"] == "dcinside"]
    sigs = {x["id"]: _mqe_for(x) for x in dc}

    # --- AC8 편향 보존 하드게이트 ---
    # M(갤 메타/뉴스 위젯)은 '제품 부정'이 아니라 구조적 노이즈다 — 경계 판정 T7 이 dc-001 을
    # 명시적으로 M=1 로 정했다. 그래서 M 항목은 부정 모집단에서 뺀다. 다만 **M 이 진짜 후기를
    # 죽이는 것**은 다른 문제이므로(그건 실제 편향 왜곡) 따로 세서 하드 실패로 잡는다.
    negatives = [x for x in dc if rules.is_negative(x["text"]) and not sigs[x["id"]]["M"]]
    bias_drops = [x["id"] for x in negatives if not sigs[x["id"]]["candidate"]]
    meta_ate_review = [x["id"] for x in dc if sigs[x["id"]]["M"] and x["label"]["E"]]
    meta_ate_negative = [x["id"] for x in dc
                         if sigs[x["id"]]["M"] and rules.is_negative(x["text"])]

    # --- AC9 거짓 DROP 회귀 ---
    ac9_fail = [i for i in AC9_IDS
                if i in sigs and (sigs[i]["M"] or not sigs[i]["candidate"])]

    # --- 버킷별 순도 + 헛호출률 ---
    buckets: dict[int, dict] = {b: {"n": 0, "true_E": 0} for b in (2, 1, 0)}
    for x in dc:
        s = sigs[x["id"]]
        if s["M"]:
            continue
        b = s["e_bucket"]
        buckets[b]["n"] += 1
        buckets[b]["true_E"] += int(x["label"]["E"])
    cand = [x for x in dc if not sigs[x["id"]]["M"] and sigs[x["id"]]["candidate"]]
    wasted = [x["id"] for x in cand if not x["label"]["E"]]

    return {
        "n_dcinside": len(dc),
        "n_meta_drop": sum(1 for s in sigs.values() if s["M"]),
        "n_candidate": len(cand),
        "n_true_E": sum(x["label"]["E"] for x in dc),
        "bias": {"n_negative": len(negatives), "dropped": bias_drops,
                 "retention": 1.0 if not negatives else 1 - len(bias_drops) / len(negatives),
                 "meta_ate_review": meta_ate_review, "meta_ate_negative": meta_ate_negative},
        "ac9": {"checked": list(AC9_IDS), "failed": ac9_fail},
        "buckets": {b: {**v, "purity": (v["true_E"] / v["n"]) if v["n"] else None}
                    for b, v in buckets.items()},
        "wasted_calls": {"n": len(wasted), "rate": len(wasted) / len(cand) if cand else 0.0,
                         "ids": wasted},
        "bias_hold_only": [x["id"] for x in cand
                           if sigs[x["id"]]["bias_hold"] and not sigs[x["id"]]["e_bucket"]],
    }


def print_axis_report(ar: dict) -> None:
    print("=" * 64)
    print("M/Q/E 축 리포트 (AC8 편향 보존 · AC9 거짓 DROP · 버킷 순도)")
    print("=" * 64)
    print(f"디시 {ar['n_dcinside']}건 | M 드롭 {ar['n_meta_drop']} | 후보 {ar['n_candidate']} "
          f"| 실제 E {ar['n_true_E']}")
    b = ar["bias"]
    ok = "✅" if not b["dropped"] else "❌"
    print(f"\n[AC8] 부정 감성 {b['n_negative']}건 후보 유지율 {b['retention']:.3f} {ok}")
    if b["dropped"]:
        print(f"      드롭됨(위반): {b['dropped']}")
    if ar["bias_hold_only"]:
        print(f"      E=0 이지만 편향 하드게이트로 유지: {ar['bias_hold_only']}")
    if b["meta_ate_negative"]:
        print(f"      (참고) M 축이 먼저 드롭한 부정 표현 항목: {b['meta_ate_negative']} "
              f"— 갤 메타라 제품 부정이 아님")
    ok_m = "✅" if not b["meta_ate_review"] else "❌"
    print(f"      M 축이 진짜 후기를 삼켰는가: {b['meta_ate_review'] or '없음'} {ok_m}")
    ok9 = "✅" if not ar["ac9"]["failed"] else "❌"
    print(f"\n[AC9] 거짓 DROP 회귀 {len(ar['ac9']['checked'])}건 {ok9}")
    for i, why in AC9_IDS.items():
        mark = "✗" if i in ar["ac9"]["failed"] else "✓"
        print(f"      {mark} {i}  {why}")
    print("\n[버킷 순도] 2=규칙·프로브 둘 다 / 1=하나만 / 0=둘 다 음성")
    print("      ⚠ 커밋된 프로브는 이 골드 전체로 학습됐다 → 아래 순도는 in-sample(낙관적).")
    print("        정직한 수치는 `python evals/train_e_probe.py` 의 5-fold CV 값을 볼 것.")
    for bk in (2, 1, 0):
        v = ar["buckets"][bk]
        pur = "—" if v["purity"] is None else f"{v['purity']:.3f}"
        print(f"      bucket {bk}: n={v['n']:<4} 실제E={v['true_E']:<4} 순도={pur}")
    w = ar["wasted_calls"]
    print(f"\n[헛호출률] 후보 중 실제 E 아님: {w['n']}/{ar['n_candidate']} = {w['rate']:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="관련성 τ_topic 보정 + M/Q/E 축 리포트")
    ap.add_argument("--report", action="store_true", help="리포트 출력")
    ap.add_argument("--write", action="store_true", help="τ 를 relevance_tau.json 에 기입")
    args = ap.parse_args()
    if args.write:
        # ADR-0007 잠복 위험 가드: 골드 keep 이 판정된 scope 와 ACTIVE scope 가 다르면, 여기서
        # 산출한 τ 는 "product 앵커 vs market 기준 keep" 같은 어긋난 짝으로 보정된 값이라
        # 파일에 기록하는 순간 fail-loud 검사를 깨끗이 통과하는 오염 τ 가 된다 → 기록 전 거부.
        ruled = json.loads(GOLD.read_text(encoding="utf-8")).get("keep_scope_ruling", {})
        if not ruled:                       # 침묵 통과와 승인 통과를 구분(τ 로더의 레거시 경고와 대칭)
            print("⚠ 골드에 keep_scope_ruling 없음 — 레거시 골드로 간주, scope 일치 검사 생략")
        mismatched = {p: (s, R.RELEVANCE_CONF[p].get("target_scope", "product"))
                      for p, s in ruled.items()
                      if p in R.RELEVANCE_CONF and s != R.RELEVANCE_CONF[p].get("target_scope", "product")}
        if mismatched:
            for p, (ruled_s, active_s) in mismatched.items():
                print(f"✗ --write 거부[{p}]: 골드 keep 판정 scope={ruled_s} ≠ ACTIVE scope={active_s} "
                      f"— keep 재판정 없이 기록하면 오염 τ 가 된다(ADR-0007 완화 절).")
            return 1
    rep = calibrate()
    items = json.loads(GOLD.read_text(encoding="utf-8"))["items"]
    ar = axis_report(items)
    if args.report or not args.write:
        print_report(rep)
        print()
        print_axis_report(ar)
    if args.write:
        TAU_OUT.write_text(json.dumps(
            {"_doc": "calibrate_relevance.py 산출 소스별 τ_topic. relevance.py 가 import 때 RELEVANCE_CONF 에 반영. "
                     "target_scope 는 이 τ 를 만든 앵커 방침 — 방침이 바뀌면 τ 는 무효다(재보정 필요).",
             "tau_topic": rep["tau_topic"], "target_scope": rep["target_scope"],
             "report": rep["by_platform"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nτ 기입: {TAU_OUT}  → {rep['tau_topic']}")
    # 하드 게이트: AC8 편향 보존 위반 또는 AC9 거짓 DROP 은 즉시 실패(계획 §3 "위반 시 종료코드 1").
    if ar["bias"]["dropped"] or ar["ac9"]["failed"] or ar["bias"]["meta_ate_review"]:
        print("\n❌ 하드게이트 위반 — 부정 후기 드롭 또는 거짓 DROP 회귀")
        return 1
    # AC4(topic τ 홀드아웃)는 정보용 — 골드 교정을 유도할 뿐 여기서 실패로 세지 않는다.
    unmet = [p for p, r in rep["by_platform"].items() if r["meets_AC4"] is False]
    if unmet:
        print(f"\n⚠ AC4(topic τ) 미충족 소스: {unmet} — 골드 교정/타깃 방침(§7 블로커 #2) 대기")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
