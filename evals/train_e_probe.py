# -*- coding: utf-8 -*-
"""
E 축 로지스틱 프로브 오프라인 학습 (계획 `.omc/plans/kind-axis-resolution.md` B-3, AC10).

**왜 프로브인가:** 규칙 캐스케이드와 임베딩 프로브는 **오류가 상보적**이다(§2, 일치율 83.7%).
그래서 둘을 이진 게이트 두 개로 직렬 연결하지 않고 **합집합 후보 + E 신뢰도 순위**로 쓴다.
프로브 단독 채택은 하지 않는다 — n=123 에 과적합할 수 있어서 순위 기여로만 제한한다(§5 위험표).

**런타임 학습 없음.** 여기서 학습한 (w, b) 를 `evals/gold/e_probe.npz` 로 커밋하고,
`relevance.load_e_probe()` 가 그걸 로드만 한다. BGE-M3 는 이미 로드돼 있으므로 추가 모델 의존성 0.

**sklearn 을 쓰지 않는다.** 학습은 오프라인 1회지만, 선언되지 않은 의존성을 CI 경로에 끌어들이지
않기 위해 numpy 만으로 L2 정규화 로지스틱 회귀를 돌린다(전배치 경사하강, 결정적).

실행:
    python evals/train_e_probe.py              # 5-fold CV 리포트만
    python evals/train_e_probe.py --write      # 전체 학습 후 e_probe.npz 기입
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import slime_rag.relevance as R
from slime_rag import relevance_rules as rules

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
OUT = Path(__file__).resolve().parent / "gold" / "e_probe.npz"

# d=1024 ≫ n=123 이라 정규화가 곧 성능이다. 실측 스윕(L2 ∈ {1e-4…1e-1}):
#   1e-4 → F1 0.812 / 1e-3 → 0.702 / 1e-2 → 0.108 / 1e-1 → 0.000(전부 음성으로 붕괴).
# 1e-4 가 계획 §2 의 프로브 수치(F1 0.821)를 재현하는 지점.
L2 = 1e-4
LR = 2.0
ITERS = 4000
FOLDS = 5


def _fit(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """L2 로지스틱 회귀, 전배치 경사하강(결정적 — 난수 초기화 없음)."""
    n, d = X.shape
    w, b = np.zeros(d), 0.0
    for _ in range(ITERS):
        p = 1.0 / (1.0 + np.exp(-(X @ w + b)))
        err = p - y
        w -= LR * ((X.T @ err) / n + L2 * w / n)
        b -= LR * err.mean()
    return w, b


def _prf(y: np.ndarray, pred: np.ndarray) -> tuple[float, float, float]:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def load_xy() -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """디시 항목만 — 3축은 디시 신호다(인스타는 mqe_axis=False)."""
    items = [x for x in json.loads(GOLD.read_text(encoding="utf-8"))["items"]
             if x["platform"] == "dcinside"]
    # 런타임과 동일한 표현: 청크 임베딩의 정규화 평균 벡터(relevance._verdict 와 같은 계산).
    vecs = []
    for it in items:
        chunks = R.chunk(it["text"]) or [it["text"]]
        vecs.append(R._normalize(np.asarray(R._embed(chunks), dtype=float)).mean(axis=0))
    X = np.asarray(vecs, dtype=float)
    y = np.asarray([it["label"]["E"] for it in items], dtype=float)
    return X, y, items


def cross_validate(X: np.ndarray, y: np.ndarray, items: list[dict]) -> dict:
    """결정적 5-fold(인덱스 % FOLDS — 난수 미사용). 규칙·합집합·교집합도 같은 폴드로 비교."""
    n = len(y)
    probe_pred = np.zeros(n)
    for f in range(FOLDS):
        te = np.array([i % FOLDS == f for i in range(n)])
        w, b = _fit(X[~te], y[~te])
        probe_pred[te] = (1.0 / (1.0 + np.exp(-(X[te] @ w + b))) >= 0.5).astype(float)
    rule_pred = np.asarray([rules.axes(it["text"])["E"] for it in items], dtype=float)
    union = np.maximum(rule_pred, probe_pred)
    inter = np.minimum(rule_pred, probe_pred)
    agree = float((rule_pred == probe_pred).mean())
    rows = {"규칙 캐스케이드": rule_pred, "프로브 (5-fold)": probe_pred,
            "합집합 (규칙 OR 프로브)": union, "교집합 (규칙 AND 프로브)": inter}
    return {"n": n, "agreement": agree,
            "metrics": {k: _prf(y, v) for k, v in rows.items()},
            "union_pred": union, "y": y}


def main() -> int:
    ap = argparse.ArgumentParser(description="E 프로브 오프라인 학습")
    ap.add_argument("--write", action="store_true", help="e_probe.npz 기입")
    args = ap.parse_args()

    X, y, items = load_xy()
    rep = cross_validate(X, y, items)
    print("=" * 66)
    print(f"E 프로브 — n={rep['n']} (dcinside), L2={L2}, 결정적 {FOLDS}-fold")
    print("=" * 66)
    print(f"{'방법':<26}{'P':>8}{'R':>8}{'F1':>8}")
    for name, (p, r, f1) in rep["metrics"].items():
        print(f"{name:<26}{p:>8.3f}{r:>8.3f}{f1:>8.3f}")
    print("-" * 66)
    print(f"규칙·프로브 일치율: {rep['agreement']:.3f}  (낮을수록 오류가 상보적 → 합집합 이득)")
    miss = int(((rep["union_pred"] == 0) & (rep["y"] == 1)).sum())
    print(f"합집합이 놓친 진짜 E: {miss} / {int(rep['y'].sum())}  ← D6 편향 보존의 핵심 수치")

    if args.write:
        w, b = _fit(X, y)                        # 커밋용은 전체 데이터 학습(성능 보고는 CV 값)
        np.savez(OUT, w=w, b=np.float64(b))
        print(f"\n가중치 기입: {OUT} (dim={len(w)})")
        print("※ 리포트 수치는 5-fold CV 값이다 — 이 전체학습 가중치의 in-sample 성능이 아니다.")
    else:
        print("\n(리포트만 — 가중치를 커밋하려면 --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
