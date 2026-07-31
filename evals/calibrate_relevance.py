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

주의: topic 축(코사인 임계)만 보정한다. kind 축(디시)은 프로토타입(=골드셋) 기반 추가 DROP 필터로
      classify 에서 독립 작동 — τ 는 온토픽 컷만 결정한다.
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

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
TAU_OUT = Path(__file__).resolve().parent / "gold" / "relevance_tau.json"
GRID = [round(x, 3) for x in np.arange(0.30, 0.651, 0.025)]
PRECISION_FLOOR = 0.90
RECALL_FLOOR = 0.80


def _build_train_prototypes(train: list[dict]) -> dict[str, np.ndarray]:
    """kind 축 centroid 를 **train 라벨만으로** 구성(홀드아웃 누수 방지). relevance 캐시에 주입."""
    by_kind: dict[str, list[str]] = {}
    for x in train:
        kind = (x["label"] or {}).get("kind")
        if kind and x.get("text"):
            by_kind.setdefault(kind, []).append(x["text"])
    proto = {}
    for kind, texts in by_kind.items():
        proto[kind] = R._normalize(np.asarray(R._embed(texts), dtype=float)).mean(axis=0)
    return proto


def _split(items: list[dict]) -> tuple[list, list]:
    """결정적 train/holdout 분할 — id 정렬 후 3번째마다 holdout(랜덤 미사용)."""
    ordered = sorted(items, key=lambda x: x.get("id", ""))
    holdout = [x for i, x in enumerate(ordered) if i % 3 == 0]
    train = [x for i, x in enumerate(ordered) if i % 3 != 0]
    return train, holdout


def _item_signals(item: dict, conf: dict, proto: dict[str, np.ndarray]) -> tuple[float, bool]:
    """항목별 (topic_score, nontopic_drop) 1회 계산 — τ 그리드 전체에 재사용(재임베딩 없음).
    nontopic_drop = kind(resale/chitchat) 또는 domain(centroid, not_slime) 축의 DROP 여부."""
    plat = item["platform"]
    domain = bool(conf.get("domain_gate", False))
    anchor = R.build_anchor(item["collected_for"], domain=domain) or " "
    chunks = R.chunk(item["text"]) or [item["text"]]
    vecs = R._embed([anchor] + chunks)
    cvecs = np.asarray(vecs[1:], dtype=float)
    topic = R._max_cosine(cvecs, np.asarray(vecs[0], dtype=float))
    nontopic_drop = False
    if conf.get("kind_axis") and proto:
        mean_vec = R._normalize(cvecs).mean(axis=0)
        if R._nearest_kind(mean_vec, proto) in R._DROP_KINDS:
            nontopic_drop = True
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
              "eval": "full classify() (topic+kind), train-only prototypes(no leakage)"}
    for plat in platforms:
        subset = [x for x in items if x["platform"] == plat]
        conf = R.RELEVANCE_CONF.get(plat, {})
        train, holdout = _split(subset)
        # kind 프로토타입은 train 라벨만으로(홀드아웃 누수 방지).
        proto = _build_train_prototypes(train) if conf.get("kind_axis") else {}
        tr_sig = [(*_item_signals(x, conf, proto), x["label"]["keep"]) for x in train]
        ho_sig = [(*_item_signals(x, conf, proto), x["label"]["keep"]) for x in holdout]
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


def main() -> int:
    ap = argparse.ArgumentParser(description="관련성 τ_topic 보정")
    ap.add_argument("--report", action="store_true", help="리포트 출력")
    ap.add_argument("--write", action="store_true", help="τ 를 relevance_tau.json 에 기입")
    args = ap.parse_args()
    rep = calibrate()
    if args.report or not args.write:
        print_report(rep)
    if args.write:
        TAU_OUT.write_text(json.dumps(
            {"_doc": "calibrate_relevance.py 산출 소스별 τ_topic. relevance.py 가 import 때 RELEVANCE_CONF 에 반영.",
             "tau_topic": rep["tau_topic"], "report": rep["by_platform"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nτ 기입: {TAU_OUT}  → {rep['tau_topic']}")
    # AC4 게이트(홀드아웃 충족 여부) — 미달 소스가 있으면 비영점 종료(정보용, 골드 교정 유도)
    unmet = [p for p, r in rep["by_platform"].items() if r["meets_AC4"] is False]
    return 1 if unmet else 0


if __name__ == "__main__":
    raise SystemExit(main())
