# -*- coding: utf-8 -*-
"""재감사 diff — **안정 키 위의 집합 차이**로 성공/회귀를 판정한다. 무과금·읽기전용.

⛔ **행 수 비교를 성공 기준으로 쓰지 말 것.** 접기(fold)가 행을 지우므로 분모(801)가 줄고,
  그러면 결함 수가 줄어도 **고쳐서인지 사라져서인지 구분되지 않는다.** 그래서 기준은
  `(post_id, attributes->>'mentioned_product')` 키 위의 집합 연산이다:

    resolved   = baseline − after − folded     # 실제로 고쳐진 것
    persisting = baseline ∩ after              # 남은 것
    regressed  = after − baseline              # ⚠️ 비어 있지 않으면 실패

⚠️ **매칭률을 먼저 단언한다.** 키가 안 맞으면 `after` 가 통째로 `regressed` 로 보이거나
  반대로 '결함 0'처럼 보이는데, 후자는 성공과 구분되지 않는다 — `INVERSION_ROLLBACK_WHERE`
  의 `::real` 사고와 정확히 같은 모양(조용히 빈 결과)이다.

`--folds` 는 `data/repair_ledgers/` 글롭을 받는다. ⛔ `.omc/` 아래를 가리키지 말 것 —
거긴 gitignore 라 원장이 세션 수명이고, 원장이 사라지면 `resolved` 가 **조용히 부풀려진다**
(지워진 행이 '고쳐진 행'으로 계산된다).

실행:
    OPENAI_API_KEY="" python evals/diff_audit.py \
        evals/results/dc_attribution_baseline.json \
        evals/results/dc_attribution_after.json \
        --folds 'data/repair_ledgers/*fold*.json'
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODES = ("D1", "D2", "D2c", "D3", "D4", "D5a", "D5b", "D5c", "D6", "D7", "D8", "D9", "D10", "F1")
MIN_MATCH_RATE = 0.95


def _keys(report: dict) -> dict:
    return {c: set(v) for c, v in (report.get("keys_by_code") or {}).items()}


def _fold_keys(patterns) -> set:
    """접기 원장에서 **삭제된 키**를 모은다.

    원장 항목은 `id`·`post_id`·`was` 를 갖는다. 감사 키는 `attributes->>'mentioned_product'`
    인데 원장의 `was` 는 **컬럼 값**(복구가 이미 비웠을 수 있다)이라 정확히 같지 않다 —
    그래서 `post_id` 만 맞는 키까지 넓게 잡고, 넓게 잡은 만큼을 `folded_loose` 로 **드러낸다**.
    (좁게 잡으면 `resolved` 가 부풀고, 넓게 잡으면 `resolved` 가 줄어든다. 줄어드는 쪽이
     안전한 방향이라 그쪽을 고르되 침묵하지 않는다.)
    """
    out, posts = set(), set()
    for pat in patterns or ():
        for p in sorted(glob.glob(pat)):
            if "/.omc/" in p:
                raise SystemExit(f"⛔ 원장이 .omc 아래다(세션 수명 — 커밋되지 않는다): {p}")
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            for e in data.get("entries") or []:
                if e.get("post_id") is None:
                    continue
                posts.add(e["post_id"])
                out.add(f"{e['post_id']}␟{e.get('was') or ''}")
    return out, posts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("baseline", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--folds", action="append", default=[],
                    help="접기 원장 글롭(data/repair_ledgers/*.json). 반복 가능")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    base = json.loads(args.baseline.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    bk, ak = _keys(base), _keys(after)
    folded, folded_posts = _fold_keys(args.folds)

    # ① 매칭률 — 비교 자체가 성립하는가. 이게 먼저다.
    base_all = set().union(*bk.values()) if bk else set()
    kept = set(base.get("keys_kept") or [])
    after_all = set().union(*ak.values()) if ak else set()
    after_kept = set(after.get("keys_kept") or [])
    universe_b, universe_a = base_all | kept, after_all | after_kept
    matched = len(universe_b & universe_a)
    rate = matched / len(universe_b) if universe_b else 0.0

    report = {"baseline": str(args.baseline), "after": str(args.after),
              "key_match_rate": round(rate, 4), "matched_keys": matched,
              "baseline_keys": len(universe_b), "after_keys": len(universe_a),
              "folded_keys": len(folded), "folded_posts": len(folded_posts),
              "by_code": {}, "regressed_total": 0}

    failures: list[str] = []
    if rate < MIN_MATCH_RATE:
        failures.append(
            f"키 매칭률 {rate:.1%} < {MIN_MATCH_RATE:.0%} — 비교가 무의미하다"
            " (골드 키가 바뀌었거나 재감사 입력이 다른 코퍼스다)")

    for code in CODES:
        b, a = bk.get(code, set()), ak.get(code, set())
        # 접기로 사라진 키는 '고쳐진 것'이 아니다 — 넓게 잡은 post 단위까지 뺀다.
        gone_by_fold = {k for k in (b - a)
                        if k in folded or k.split("␟")[0] in folded_posts}
        resolved = (b - a) - gone_by_fold
        report["by_code"][code] = {
            "baseline": len(b), "after": len(a),
            "resolved": len(resolved), "persisting": len(b & a),
            "folded": len(gone_by_fold), "regressed": len(a - b),
            "regressed_keys": sorted(a - b)[:20],
        }
        report["regressed_total"] += len(a - b)

    # ② 회귀 금지 — ✅ 유지였던 키가 결함으로 나타나면 즉시 실패.
    regressed_from_kept = (after_all & kept)
    report["regressed_from_kept"] = len(regressed_from_kept)
    report["regressed_from_kept_keys"] = sorted(regressed_from_kept)[:20]
    if regressed_from_kept:
        failures.append(f"✅ 유지였던 {len(regressed_from_kept)}건이 결함으로 나타났다")
    if report["regressed_total"]:
        failures.append(f"새 결함 유입 {report['regressed_total']}건(regressed ≠ ∅)")

    report["ok"] = not failures
    report["failures"] = failures
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    for f in failures:
        print(f"⛔ {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
