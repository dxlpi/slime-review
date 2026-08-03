# -*- coding: utf-8 -*-
"""
관련성 골드셋 무결성 검사 — 계획 `.omc/plans/kind-axis-resolution.md` AC1·AC2. CI 게이트.

**이 검사기가 지키는 단 하나의 불변식:**
  `keep`/`collected_for` 는 **쿼리 조건부**(D2, `relevance.py`)라 같은 텍스트라도 타깃이 바뀌면
  달라지는 게 정상이다. 반면 `M/Q/E` 는 **텍스트의 절대 속성**이다 — "ㅂ슬라임 간단후기"는
  어떤 제품을 검색했든 후기다. 그러므로:

      동일 텍스트 → M/Q/E 는 반드시 일치.  keep/collected_for 불일치는 검사 대상이 아니다.

계획 §1-A 가 정정한 지점이 정확히 이것이다. 초안 검사에서 `collected_for` 를 안 보고
(text, kind, keep) 만 비교해 "모순 6그룹, 골드 무효" 라는 잘못된 결론이 나왔었다.
쿼리 조건부 하드 네거티브 6건은 `bootstrap_relevance_gold.py:111-124` 가 AC3 용으로 의도 생성한
**자산**이므로, 이 검사기는 그 6건이 사라지면 오히려 실패한다.

실행:  python evals/check_gold_integrity.py      (위반 시 exit 1)
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
RULINGS = Path(__file__).resolve().parent / "gold" / "boundary_rulings.json"

AXES = ("M", "Q", "E")
# 쿼리 조건부 하드 네거티브(AC3 자산) — 삭제되면 실패.
REQUIRED_NEGATIVES = ("dc-132", "dc-133", "dc-134", "in-135", "in-136", "in-137")


def check(gold: dict) -> tuple[list[str], list[str]]:
    """(errors, notes) — errors 가 비어야 통과."""
    errors: list[str] = []
    notes: list[str] = []
    items = gold["items"]
    by_id = {it["id"]: it for it in items}

    # --- AC2: 3축 스키마 ---
    for it in items:
        label = it.get("label") or {}
        for ax in AXES:
            if label.get(ax) not in (0, 1):
                errors.append(f"[AC2] {it['id']}: label.{ax} 가 0|1 이 아님 ({label.get(ax)!r})")
        if "kind" in label:
            errors.append(f"[AC2] {it['id']}: 폐기된 label.kind 가 남아 있음 ({label['kind']!r})")
        if label.get("kind_deprecated") == "resale":
            errors.append(f"[AC2] {it['id']}: resale 범주 잔재 (아모스 갤 = 비거래 도메인)")

    # --- AC1a: 쿼리 조건부 네거티브 보존 ---
    for nid in REQUIRED_NEGATIVES:
        if nid not in by_id:
            errors.append(f"[AC1] 쿼리 조건부 하드 네거티브 {nid} 가 삭제됨 — AC3 자산이라 보존해야 함")

    # --- AC1b: 절대축 일치 (동일 텍스트 그룹) ---
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[(it.get("text") or "").strip()].append(it)
    dup_groups = 0
    for text, members in groups.items():
        if len(members) < 2:
            continue
        dup_groups += 1
        axis_sets = {tuple((m["label"] or {}).get(ax) for ax in AXES) for m in members}
        if len(axis_sets) > 1:
            ids = ", ".join(m["id"] for m in members)
            errors.append(
                f"[AC1] 절대축 불일치: {ids} 가 같은 텍스트인데 M/Q/E 가 다름 {sorted(axis_sets)}"
                f"  «{text[:30]}…»")
        # keep 불일치는 정상 — 쿼리 조건부. 확인만 하고 통과시킨다.
        if len({m["label"].get("keep") for m in members}) > 1:
            notes.append(f"· 쿼리 조건부 네거티브 정상 동작: {', '.join(m['id'] for m in members)} "
                         f"(같은 텍스트, 다른 타깃 → 다른 keep)")
    notes.append(f"· 동일 텍스트 그룹 {dup_groups}개 검사")

    # --- AC3: 경계 판정 진행 상황(정보) ---
    prov = [it["id"] for it in items if (it.get("label") or {}).get("provisional")]
    if prov:
        notes.append(f"⚠ 경계 판정 잠정 {len(prov)}건 — 사용자 확정 대기: {', '.join(sorted(prov))}")
    if RULINGS.exists():
        rulings = json.loads(RULINGS.read_text(encoding="utf-8")).get("rulings", [])
        missing = [r["id"] for r in rulings if r["id"] not in by_id]
        if missing:
            errors.append(f"[AC3] boundary_rulings 가 없는 항목을 가리킴: {missing}")
        for r in rulings:
            it = by_id.get(r["id"])
            if not it:
                continue
            got = tuple(it["label"].get(ax) for ax in AXES)
            want = (r["M"], r["Q"], r["E"])
            if got != want:
                errors.append(f"[AC3] {r['id']}: 판정({want})이 골드({got})에 반영되지 않음 "
                              f"— migrate_gold_mqe.py --write 를 다시 돌릴 것")
        notes.append(f"· 경계 판정 {len(rulings)}건 대조 완료")

    counts = {ax: sum(it["label"][ax] for it in items if it["label"].get(ax) in (0, 1)) for ax in AXES}
    notes.append(f"· 항목 {len(items)}건 | M={counts['M']} Q={counts['Q']} E={counts['E']}")
    return errors, notes


def main() -> int:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    errors, notes = check(gold)
    print("=" * 64)
    print("관련성 골드셋 무결성 (AC1 절대축 일치 / AC2 3축 스키마)")
    print("=" * 64)
    for n in notes:
        print(n)
    if errors:
        print("-" * 64)
        for e in errors:
            print("✗ " + e)
        print(f"\n{len(errors)}건 위반 ❌")
        return 1
    print("\n무결성 통과 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
