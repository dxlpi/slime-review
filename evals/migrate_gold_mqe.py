# -*- coding: utf-8 -*-
"""
골드셋 절대축 마이그레이션 — `label.kind`(배타 4분류) → `label.M/Q/E`(독립 이진 3축).
계획 `.omc/plans/kind-axis-resolution.md` 트랙 A (A-0/A-1/A-2, AC1·AC2·AC3).

왜 바꾸나(§1-B): 디시 글은 한 편에 여러 화행이 섞인다. `review/question/chitchat` 을 배타로
강제하면 centroid 가 서로를 향해 붕괴한다 — **taxonomy 가 버그**였다. 3축은 배타가 아니라서
`dc-015`("살만한가?" + "실망함")가 `Q=1, E=1` 로 동시에 참일 수 있다.

축 정의(절대 축 — `collected_for` 가 바뀌어도 흔들리면 안 된다):
  M : 갤 메타·드라마·구조적 노이즈(뉴스 위젯 블리드 포함). 하드 네거티브.
  Q : 질문·조언요청 화행. **관측용이며 드롭 사유가 아니다.**
  E : 작성자 **본인의 실사용 평가** 서술 존재. 추출 순위의 주 신호.

`keep` / `collected_for` 는 **건드리지 않는다** — 그건 쿼리 조건부 축(topic)이고 여기 관할이 아니다.
쿼리 조건부 하드 네거티브 6건(dc-132·133·134 / in-135·136·137)은 AC3 자산이라 보존한다(§1-A).

경계 15건(§7 블로커 #1)은 이 파일이 아니라 `gold/boundary_rulings.json` 이 정한다 —
사용자 판정이 오면 그 파일만 고치고 이 스크립트를 다시 돌리면 반영된다(멱등).

실행:
    python evals/migrate_gold_mqe.py            # 미리보기(diff 만)
    python evals/migrate_gold_mqe.py --write    # relevance_gold.json 갱신
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GOLD = Path(__file__).resolve().parent / "gold" / "relevance_gold.json"
RULINGS = Path(__file__).resolve().parent / "gold" / "boundary_rulings.json"

# ---------------------------------------------------------------- 절대축 라벨 (사람 판정)
# 여기 없는 id 는 해당 축 0. 초안 `kind` 에서 기계 변환하지 않는다 — 초안이 노이즈였다는 게
# §1-A 의 결론이고(명백한 후기가 chitchat 으로 초안됨), 그걸 그대로 옮기면 결함이 승계된다.

# M — 갤 메타/드라마 + 디시 실시간 뉴스 위젯 블리드.
M_IDS = {
    # 뉴스 위젯 블리드 11건(B-0 chrome-strip 대상이지만 라벨은 남긴다: 크롤러가 놓쳐도 분류기가 받음)
    "dc-006", "dc-011", "dc-021", "dc-038", "dc-043",
    "dc-061", "dc-067", "dc-083", "dc-089", "dc-096", "dc-108",
    # 갤 메타/드라마 — 제품·마켓 정보 0
    "dc-001", "dc-010",
}

# Q — 질문·조언요청 화행이 존재(수사의문·자문 포함). 드롭 사유 아님.
Q_IDS = {
    "dc-013", "dc-015", "dc-023", "dc-027", "dc-030", "dc-033", "dc-034", "dc-040",
    "dc-041", "dc-046", "dc-052", "dc-053", "dc-058", "dc-060", "dc-065", "dc-068",
    "dc-069", "dc-073", "dc-076", "dc-085", "dc-086", "dc-087", "dc-100", "dc-102",
    "dc-110", "dc-115", "dc-116", "dc-117", "dc-119",
}

# E — 작성자 본인의 실사용 평가 서술이 존재.
E_IDS = {
    # 디시 — 1인칭 실사용 평가
    "dc-000", "dc-004", "dc-005", "dc-012", "dc-013", "dc-014", "dc-015", "dc-016",
    "dc-017", "dc-018", "dc-019", "dc-028", "dc-040", "dc-042", "dc-047", "dc-048",
    "dc-049", "dc-050", "dc-051", "dc-054", "dc-055", "dc-059", "dc-062", "dc-064",
    "dc-066", "dc-071", "dc-072", "dc-102", "dc-103", "dc-105", "dc-109", "dc-114",
    # 쿼리 조건부 네거티브 — 텍스트가 같으므로 절대축도 같아야 한다(AC1)
    "dc-132", "dc-134",
    # 인스타 — 판매자 캡션 중 '직접 플레잉' 서술이 있는 것(공지·스펙 나열은 제외)
    "in-123", "in-124", "in-126", "in-127", "in-128", "in-129", "in-130", "in-131",
}

# 판정 근거 — 초안에서 뒤집힌 항목의 이유를 골드 `label.why` 에 실어 보낸다.
# (여기 없는 항목은 초안 why 를 그대로 둔다.)
AXIS_NOTES = {
    "dc-000": "명시적 '간단후기' + 제품별 5/5 평가 → E",
    "dc-004": "'조음' 평가 + 'ㅊㅊ' 추천 — 초안 question 은 오류(§1-A) → E",
    "dc-013": "'존나좋다/재밌었음' 실사용 + '슬켓이려나' 자문 → Q·E 동시 참",
    "dc-015": "'살만한가?'(Q) + 'ㅇㅉ거 … 좀 많이 실망함'(E) — §1-B 배타성 반증 사례",
    "dc-018": "'그래놀라진짜개좋음' — 초안 chitchat/DROP 은 오류(AC9)",
    "dc-024": "'잘맞을것같대서' = 전언 표지 → E=0 (AC7)",
    "dc-028": "'층분리랑 수분감 개심해' 부정 평가 — AC8 하드게이트 대상",
    "dc-062": "'카피바라는 만져봤는데 진짜 극락임' — 초안 chitchat 은 오류(AC9)",
    "dc-072": "'좋더라' = 직접지각 표지 → E=1 (AC7)",
    "in-135": "in-120 과 동일 텍스트 — 절대축 일치(공지라 E=0)",
    "in-136": "in-121 과 동일 텍스트 — 절대축 일치",
    "in-137": "in-122 과 동일 텍스트 — 절대축 일치",
}


def load_rulings() -> dict[str, dict]:
    """경계 판정 원장 → id별 축값. also_applies 로 동류 항목에 규칙 전파."""
    if not RULINGS.exists():
        return {}
    data = json.loads(RULINGS.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for r in data.get("rulings", []):
        axes = {"M": r["M"], "Q": r["Q"], "E": r["E"],
                "provisional": bool(r.get("provisional")), "theme": r["theme"]}
        out[r["id"]] = axes
        for other in r.get("also_applies", []):
            # 전파는 M/E 만 — Q(질문 화행)는 문장별로 달라서 규칙 전파 대상이 아니다.
            out.setdefault(other, {})
            out[other].update({"M": axes["M"], "E": axes["E"],
                               "provisional": axes["provisional"], "theme": r["theme"]})
    return out


def base_axes(item_id: str) -> dict:
    return {"M": int(item_id in M_IDS), "Q": int(item_id in Q_IDS), "E": int(item_id in E_IDS)}


def migrate(items: list[dict], rulings: dict[str, dict]) -> list[str]:
    """items 를 제자리 갱신하고, 변경 요약 줄들을 돌려준다."""
    changes: list[str] = []
    for it in items:
        iid = it["id"]
        label = it["label"]
        axes = base_axes(iid)
        ruling = rulings.get(iid)
        if ruling:
            for ax in ("M", "Q", "E"):
                if ax in ruling:
                    axes[ax] = int(ruling[ax])
        old_kind = label.pop("kind", None)
        if old_kind is not None:
            label["kind_deprecated"] = old_kind      # 되돌아볼 수 있게 보존(계획 AC2 허용안)
        before = {ax: label.get(ax) for ax in ("M", "Q", "E")}
        label.update(axes)
        if ruling:
            label["boundary"] = ruling["theme"]
            if ruling.get("provisional"):
                label["provisional"] = True
            else:
                label.pop("provisional", None)
        if iid in AXIS_NOTES:                        # 뒤집힌 라벨은 이유를 골드에 남긴다
            label["why"] = AXIS_NOTES[iid]
        label.pop("draft", None)                     # 더 이상 '자동 초안'이 아니다
        if before != axes:
            changes.append(f"{iid}: {before} → {axes}"
                           + (f"  [{ruling['theme']}{'·잠정' if ruling.get('provisional') else ''}]"
                              if ruling else ""))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description="골드셋 kind → M/Q/E 마이그레이션")
    ap.add_argument("--write", action="store_true", help="relevance_gold.json 갱신")
    args = ap.parse_args()

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    items = gold["items"]
    rulings = load_rulings()
    changes = migrate(items, rulings)

    n = {ax: sum(it["label"][ax] for it in items) for ax in ("M", "Q", "E")}
    n_prov = sum(1 for it in items if it["label"].get("provisional"))
    gold["_doc"] = (
        "관련성 골드셋. 절대 축 = label.M/Q/E(이진, 텍스트 속성 — collected_for 와 무관). "
        "쿼리 조건부 축 = label.keep(+collected_for). 경계 판정 근거는 gold/boundary_rulings.json. "
        "label.kind_deprecated 는 폐기된 4분류 초안(resale 범주 폐기). "
        "동일 텍스트를 공유하는 항목의 M/Q/E 는 반드시 일치해야 한다(check_gold_integrity.py)."
    )
    gold["counts"] = {
        "total": len(items),
        "keep": sum(1 for x in items if x["label"]["keep"]),
        "drop": sum(1 for x in items if not x["label"]["keep"]),
        "dcinside": sum(1 for x in items if x["platform"] == "dcinside"),
        "instagram": sum(1 for x in items if x["platform"] == "instagram"),
        "M": n["M"], "Q": n["Q"], "E": n["E"],
        "provisional_boundary": n_prov,
    }

    print(f"항목 {len(items)}건 | M={n['M']} Q={n['Q']} E={n['E']} | 경계 잠정 {n_prov}건")
    for line in changes:
        print("  " + line)
    if args.write:
        GOLD.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기입 완료 → {GOLD}")
    else:
        print("\n(미리보기 — 반영하려면 --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
