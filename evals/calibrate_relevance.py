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

주의: topic 축(코사인 임계)만 보정한다. M/Q/E 축(디시)은 `relevance_rules` + E 프로브가 독립
      작동하며 τ 는 온토픽 컷만 결정한다. 3축에서 **DROP 사유는 M 뿐**이고, E 합집합 음성은
      후보 탈락(추출 안 함)이지 관련성 DROP 이 아니다.
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
GRID = [round(x, 3) for x in np.arange(0.30, 0.651, 0.025)]
PRECISION_FLOOR = 0.90
RECALL_FLOOR = 0.80


def _split(items: list[dict]) -> tuple[list, list]:
    """결정적 train/holdout 분할 — id 정렬 후 3번째마다 holdout(랜덤 미사용)."""
    ordered = sorted(items, key=lambda x: x.get("id", ""))
    holdout = [x for i, x in enumerate(ordered) if i % 3 == 0]
    train = [x for i, x in enumerate(ordered) if i % 3 != 0]
    return train, holdout


def _item_signals(item: dict, conf: dict) -> tuple[float, bool]:
    """항목별 (topic_score, nontopic_drop) 1회 계산 — τ 그리드 전체에 재사용(재임베딩 없음).
    nontopic_drop = M(메타) 축 또는 domain(centroid, not_slime) 축의 DROP 여부.
    ⚠️ E 합집합 음성은 여기에 넣지 않는다 — 그건 '관련 없음'이 아니라 '추출 순위 꼬리'다."""
    domain = bool(conf.get("domain_gate", False))
    anchor = R.build_anchor(item["collected_for"], domain=domain) or " "
    chunks = R.chunk(item["text"]) or [item["text"]]
    vecs = R._embed([anchor] + chunks)
    cvecs = np.asarray(vecs[1:], dtype=float)
    topic = R._max_cosine(cvecs, np.asarray(vecs[0], dtype=float))
    nontopic_drop = bool(conf.get("mqe_axis")) and rules.is_meta(item["text"])
    return topic, nontopic_drop


def _pr_from_signals(sig: list[tuple[float, bool, bool]], tau: float) -> tuple[float, float, int, int, int]:
    """sig=(topic, nontopic_drop, label_keep). 예측 KEEP = topic>=τ AND not nontopic_drop."""
    tp = fp = fn = 0
    for topic, ntd, keep in sig:
        pred = (topic >= tau) and not ntd
        tp += pred and keep
        fp += pred and not keep
        fn += (not pred) and keep
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return precision, recall, tp, fp, fn


def _choose_tau(sig: list[tuple[float, bool, bool]]) -> tuple[float, str]:
    """precision≥0.90 제약 하 recall 최대. 없으면 precision 최대(→recall) 폴백."""
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
              "eval": "topic(τ) + M(규칙, 학습 없음) — E 합집합은 DROP 이 아니라 순위라 제외"}
    for plat in platforms:
        subset = [x for x in items if x["platform"] == plat]
        conf = R.RELEVANCE_CONF.get(plat, {})
        train, holdout = _split(subset)
        tr_sig = [(*_item_signals(x, conf), x["label"]["keep"]) for x in train]
        ho_sig = [(*_item_signals(x, conf), x["label"]["keep"]) for x in holdout]
        tau, rule = _choose_tau(tr_sig if tr_sig else ho_sig)
        p_tr, r_tr, *_ = _pr_from_signals(tr_sig, tau) if tr_sig else (0, 0, 0, 0, 0)
        p_ho, r_ho, tp, fp, fn = _pr_from_signals(ho_sig, tau) if ho_sig else (0, 0, 0, 0, 0)
        keep_scores = [s[0] for s in tr_sig + ho_sig if s[2]]
        drop_scores = [s[0] for s in tr_sig + ho_sig if not s[2]]
        report["tau_topic"][plat] = tau
        report["by_platform"][plat] = {
            "n": len(subset), "n_keep": len(keep_scores), "n_drop": len(drop_scores),
            "tau": tau, "rule": rule,
            "train": {"precision": round(p_tr, 3), "recall": round(r_tr, 3), "n": len(train)},
            "holdout": {"precision": round(p_ho, 3), "recall": round(r_ho, 3),
                        "n": len(holdout), "tp": tp, "fp": fp, "fn": fn},
            "keep_score_mean": round(float(np.mean(keep_scores)), 3) if keep_scores else None,
            "drop_score_mean": round(float(np.mean(drop_scores)), 3) if drop_scores else None,
            "meets_AC4": (p_ho >= PRECISION_FLOOR and r_ho >= RECALL_FLOOR) if ho_sig else None,
        }
    # 캡션 vs 포럼 τ 근접 시 단일 τ 축소 여부 안내
    taus = list(report["tau_topic"].values())
    report["single_tau_ok"] = (max(taus) - min(taus) <= 0.05) if len(taus) > 1 else True
    return report


def print_report(rep: dict) -> None:
    print("=" * 64)
    print("τ_topic 보정 리포트 (US-006 Step 5)")
    print("=" * 64)
    for plat, r in rep["by_platform"].items():
        ac4 = {True: "✅", False: "❌", None: "—"}[r["meets_AC4"]]
        print(f"[{plat}] n={r['n']} (keep {r['n_keep']}/drop {r['n_drop']})  τ={r['tau']}  ({r['rule']})")
        print(f"    train   P={r['train']['precision']} R={r['train']['recall']} (n={r['train']['n']})")
        print(f"    holdout P={r['holdout']['precision']} R={r['holdout']['recall']} "
              f"(n={r['holdout']['n']}, tp={r['holdout']['tp']} fp={r['holdout']['fp']} fn={r['holdout']['fn']})  AC4 {ac4}")
        print(f"    score 평균: keep={r['keep_score_mean']} drop={r['drop_score_mean']}")
    print("-" * 64)
    print(f"소스별 τ: {rep['tau_topic']}  | 단일 τ 축소 가능: {rep['single_tau_ok']}")
    print(f"임계: precision≥{rep['precision_floor']}, recall≥{rep['recall_floor']}")


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
    rep = calibrate()
    items = json.loads(GOLD.read_text(encoding="utf-8"))["items"]
    ar = axis_report(items)
    if args.report or not args.write:
        print_report(rep)
        print()
        print_axis_report(ar)
    if args.write:
        TAU_OUT.write_text(json.dumps(
            {"_doc": "calibrate_relevance.py 산출 소스별 τ_topic. relevance.py 가 import 때 RELEVANCE_CONF 에 반영.",
             "tau_topic": rep["tau_topic"], "report": rep["by_platform"]},
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
