# -*- coding: utf-8 -*-
"""
1층 스펙 사람 검수 오버레이 게이트 — `slime_rag.spec_overrides` 순수 함수 전 분기.

DB·네트워크·LLM 미사용(무비용) → CI 게이트 대상. 파일 IO 는 tmp 디렉터리에서만 돈다
(정본 `data/spec_overrides.json` 은 읽지도 쓰지도 않는다).

검증(계획 §S6 의 7 케이스):
  1. `apply`: 오버레이 값이 **non-null LLM 값을 이긴다**(D3 회귀 가드)
  2. `apply`: 입력 dict 를 변형하지 않는다(순수성)
  3. `needs_review`: `beads` 만 빈 행은 큐에 **안 뜬다**(사용자 결정)
  4. `needs_review`: `unknown` 칸은 큐에서 빠지고 **값은 여전히 None**(1급 규칙)
  5. `record`→`save`→`load` 왕복에서 `was`(덮기 전 값)가 보존된다
  6. `save` 는 원자적 — 쓰다 실패해도 기존 파일이 남는다
  7. `orphans`: `specs` 에 없는 (마켓, 제품) 이 조용히 사라지지 않고 목록으로 나온다

실행:  python -m eval.test_spec_overrides   (repo 루트에서)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from slime_rag import spec_overrides as so

_AT = "2026-08-09"        # `at` 은 호출부 주입 — 테스트 결정성(모듈이 now() 를 안 부른다)


def _row(**kw) -> dict:
    """`specs` 한 행 모양(DB 컬럼 이름). 기본은 전 칸 미언급."""
    row = {"market": "베이퍼", "product": "와일드베리소프트콘",
           "base_combo": None, "scent": None, "slime_type": None,
           "official_texture": None, "beads": [], "source_permalink": None}
    row.update(kw)
    return row


# ---------------------------------------------------------------- 1. 오버레이가 이긴다
def test_overlay_beats_nonnull_llm_value():
    """D3 회귀 가드 — 이 하나가 깨지면 재수집이 사람 검수를 **조용히** 되돌린다.

    `_upsert_spec` 은 전 칸이 COALESCE(EXCLUDED, specs) 라 들어오는 non-null 이 이긴다.
    프로필 액터가 최신 ~12글만 주므로 같은 제품이 여러 글에 걸쳐 다시 잡히는 건 정상이고,
    그때마다 LLM 값이 사람 값을 덮으면 검수 노동이 흔적 없이 사라진다.
    """
    data = so.record({}, "베이퍼", "와일드베리소프트콘", "slime_type", "소프트콘",
                     was=None, at=_AT)
    incoming = _row(slime_type="폼볼", scent="딸기향")     # LLM 이 다음 런에 내놓은 값
    out = so.apply(incoming, data)
    assert out["slime_type"] == "소프트콘", f"LLM 값이 사람 값을 덮었다: {out['slime_type']!r}"
    assert out["scent"] == "딸기향", "오버레이가 없는 칸까지 건드렸다"

    # 배열 칸도 같은 규칙 — 빈 배열이 미언급이라 값 있는 오버레이가 이겨야 한다.
    data2 = so.record(data, "베이퍼", "와일드베리소프트콘", "beads", ["폼볼"], was=[], at=_AT)
    assert so.apply(_row(beads=["디폼"]), data2)["beads"] == ["폼볼"]
    print("✓ apply: 오버레이가 non-null LLM 값을 이긴다(배열 칸 포함) OK")


# ---------------------------------------------------------------- 2. 순수성
def test_apply_does_not_mutate_input():
    """`apply`·`record` 는 순수다. 인플레이스로 바꾸면 호출부가 같은 dict 를 재사용할 때
    (배치 루프) 앞 행의 오버레이가 뒤 행에 새는 형태로 조용히 틀린다."""
    data = so.record({}, "베이퍼", "와일드베리소프트콘", "scent", "코코넛과자향",
                     was=None, at=_AT)
    incoming = _row(scent="딸기향")
    snapshot = json.dumps(incoming, ensure_ascii=False, sort_keys=True)
    data_snapshot = json.dumps(data, ensure_ascii=False, sort_keys=True)

    out = so.apply(incoming, data)
    assert out is not incoming, "같은 객체를 돌려줬다"
    assert json.dumps(incoming, ensure_ascii=False, sort_keys=True) == snapshot, \
        "입력 spec_row 가 변형됐다"

    # record 도 마찬가지 — 새 dict 를 돌려주고 원본은 그대로.
    so.record(data, "머머", "위즈캔디샵", "scent", "복숭아향", was=None, at=_AT)
    assert json.dumps(data, ensure_ascii=False, sort_keys=True) == data_snapshot, \
        "record 가 원본 오버레이를 변형했다"

    # 배열 칸은 얕은 복사로도 새므로 별도 확인.
    data3 = so.record({}, "베이퍼", "와일드베리소프트콘", "beads", ["폼볼"], was=[], at=_AT)
    out3 = so.apply(_row(), data3)
    out3["beads"].append("디폼")
    assert data3["베이퍼"]["와일드베리소프트콘"]["beads"]["value"] == ["폼볼"], \
        "반환된 배열이 오버레이 내부 리스트와 같은 객체다"
    print("✓ apply/record: 입력 비변경(배열 별칭 포함) OK")


# ---------------------------------------------------------------- 3. beads 는 큐를 안 띄운다
def test_beads_alone_does_not_enter_queue():
    """사용자 결정 2026-08-09 — `beads='{}'` 는 108행인데 대부분 '없음'이 정답이라,
    큐에 넣으면 대상이 3.5배가 되고 대부분 '없음 확인' 노동이 된다. 편집은 되되 큐는 안 뜬다.
    `source_permalink` 도 같다 — 없으면 임베드만 못 뜨지 스펙이 빈 건 아니다."""
    full = _row(base_combo="아마존 우드 점토", scent="코코넛과자향",
                slime_type="소프트콘", official_texture="쫀득해요")
    assert so.needs_review(full, {}) == [], "네 칸이 다 찼는데 큐에 떴다"
    assert so.needs_review(dict(full, beads=[]), {}) == [], "beads 빈 배열이 큐를 띄웠다"
    assert so.needs_review(dict(full, source_permalink=None), {}) == [], \
        "source_permalink 결손이 큐를 띄웠다"

    assert so.needs_review(dict(full, slime_type=None), {}) == ["slime_type"]
    # 빈 문자열도 미언급으로 본다 — 사람이 입력칸을 비워 저장한 경우.
    assert so.needs_review(dict(full, scent=""), {}) == ["scent"]
    print("✓ needs_review: beads·permalink 는 큐 밖, 네 칸만 큐를 띄운다 OK")


# ---------------------------------------------------------------- 4. unknown 은 값을 만들지 않는다
def test_unknown_leaves_queue_without_creating_a_value():
    """`unknown` 이 없으면 그 행은 매번 큐에 다시 떠서 도구가 영원히 'N건 남음'을 띄운다.
    ⚠️ 그러면서도 **값은 만들지 않는다** — DB 는 계속 NULL 이다(1급 규칙: 지어내기 금지).
    """
    row = _row(base_combo="아마존 우드 점토", scent="코코넛과자향", slime_type="소프트콘")
    assert so.needs_review(row, {}) == ["official_texture"]

    data = so.record({}, "베이퍼", "와일드베리소프트콘", "official_texture", None,
                     was=None, at=_AT, unknown=True)
    assert so.needs_review(row, data) == [], "unknown 인데 큐에 남았다"
    assert so.is_unknown(data, "베이퍼", "와일드베리소프트콘", "official_texture")
    assert so.apply(row, data)["official_texture"] is None, "unknown 이 값을 만들어냈다"

    # ⚠️ unknown 은 **마스킹하지 않는다**: 나중에 판매자가 캡션에 적어 LLM 이 채우면 들어와야 한다.
    later = so.apply(dict(row, official_texture="말랑하고 쫀득해요"), data)
    assert later["official_texture"] == "말랑하고 쫀득해요", \
        "unknown 이 나중에 실제로 수집된 값을 지웠다"
    print("✓ needs_review/apply: unknown 은 큐에서만 빠지고 값은 안 만든다 OK")


# ---------------------------------------------------------------- 5. 왕복에서 was 보존
def test_roundtrip_preserves_was():
    """`was`(덮기 전 값)는 사람이 **틀린 값을 고친** 경우의 유일한 되짚기 단서다.
    추출기가 향료·재료어를 제품으로 잡은 사례가 이미 실측돼 있다(유령 제품 복구)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "spec_overrides.json"
        data = so.record({}, "베이퍼", "와일드베리소프트콘", "slime_type", "소프트콘",
                         was="폼볼", at=_AT, note="영상에서 콘 모양 확인")
        so.save(data, path)
        back = so.load(path)
        on_disk = json.loads(path.read_text(encoding="utf-8"))

    cell = back["베이퍼"]["와일드베리소프트콘"]["slime_type"]
    assert cell["value"] == "소프트콘"
    assert cell["was"] == "폼볼", f"덮기 전 값이 사라졌다: {cell}"
    assert cell["at"] == _AT and cell["note"] == "영상에서 콘 모양 확인"

    # `_` 메타 키는 로드가 버리고, 저장이 다시 써 넣는다(사람이 직접 여는 파일이라).
    assert "_comment" not in back, "메타 키가 데이터로 섞였다"
    assert on_disk["_comment"].startswith("1층 스펙"), "저장이 자기설명을 잃었다"
    print("✓ record→save→load: was·note·at 보존 + `_` 메타 왕복 OK")


# ---------------------------------------------------------------- 6. 원자적 저장
def test_save_is_atomic():
    """사람이 39건을 한 건씩 채우는 파일이라, 쓰다 죽어서 반쪽 JSON 이 남으면 그때까지의
    검수가 통째로 날아간다(다음 `load` 가 예외로 죽는다)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "spec_overrides.json"
        good = so.record({}, "베이퍼", "와일드베리소프트콘", "scent", "코코넛과자향",
                         was=None, at=_AT)
        so.save(good, path)
        before = path.read_text(encoding="utf-8")

        # 직렬화 불가 값 → 디스크를 건드리기 **전에** 터져야 한다.
        broken = so.record(good, "머머", "위즈캔디샵", "scent", {1, 2}, was=None, at=_AT)
        try:
            so.save(broken, path)
            raise AssertionError("직렬화 불가 값인데 저장이 성공했다")
        except TypeError:
            pass

        assert path.read_text(encoding="utf-8") == before, "실패한 저장이 기존 파일을 깨뜨렸다"
        assert so.load(path) == good, "실패 후 파일이 다시 안 읽힌다"
        assert not list(Path(d).glob("*.tmp")), "tmp 찌꺼기가 남았다"
    print("✓ save: 직렬화 먼저 → tmp → replace, 실패해도 기존 파일 보존 OK")


# ---------------------------------------------------------------- 7. 고아 항목은 드러난다
def test_orphans_are_reported_not_dropped():
    """`extract.resolve_product_name` 이 제품을 개명·병합할 수 있다(실측: 10 renamed, 5 folded).
    고아를 조용히 버리면 사람이 한 검수가 흔적 없이 사라진다 — 무음 드롭 금지."""
    data = so.record({}, "베이퍼", "와일드베리소프트콘", "scent", "코코넛과자향",
                     was=None, at=_AT)
    data = so.record(data, "쿨라임", "빠코볼", "slime_type", "폼볼", was=None, at=_AT)

    known = {("베이퍼", "와일드베리소프트콘")}          # 쿨라임→지나 로 병합된 상황
    assert so.orphans(data, known) == [("쿨라임", "빠코볼")]
    assert so.orphans(data, known | {("쿨라임", "빠코볼")}) == []
    assert len(so.orphans(data, set())) == 2, "specs 가 비면 전부 고아로 보고돼야 한다"
    print("✓ orphans: specs 에 없는 항목이 목록으로 드러난다 OK")


# ---------------------------------------------------------------- 부록: 허용 칸 밖은 거부
def test_unknown_field_is_rejected():
    """오타 한 번이 조용히 아무 데도 안 쓰이는 칸을 만들지 않게. `OVERRIDABLE` 이 정본이다."""
    try:
        so.record({}, "베이퍼", "와일드베리소프트콘", "official_scent", "딸기향",
                  was=None, at=_AT)
        raise AssertionError("표시층 이름(official_scent)이 통과했다 — DB 컬럼은 scent 다")
    except ValueError:
        pass
    assert set(so.QUEUE_FIELDS) <= set(so.OVERRIDABLE), "큐 칸이 편집 가능 칸의 부분집합이 아니다"
    print("✓ record: OVERRIDABLE 밖의 칸 이름 거부 OK")


if __name__ == "__main__":
    test_overlay_beats_nonnull_llm_value()
    test_apply_does_not_mutate_input()
    test_beads_alone_does_not_enter_queue()
    test_unknown_leaves_queue_without_creating_a_value()
    test_roundtrip_preserves_was()
    test_save_is_atomic()
    test_orphans_are_reported_not_dropped()
    test_unknown_field_is_rejected()
    print("\n1층 스펙 사람 검수 오버레이 게이트 통과 ✅")
