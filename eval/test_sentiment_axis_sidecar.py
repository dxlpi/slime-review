# -*- coding: utf-8 -*-
"""감정 축 **수리 사이드카**(ADR-0018) — 써지는가가 아니라 **읽히는가**.

감사 D8: `소리` 축에 **탈출**(비즈·폼이 빠져나감)이 들어간 행들이다. 탈출은 지속력/질감
사안이지 소리가 아니다.

두 금지가 동시에 걸린 자리라 사이드카가 나왔다:
  · **인플레이스 수정 금지** — `attributes` 는 추출기가 실제로 뭐라고 했는지의 provenance
    스냅샷이고(`repair_dc_attribution` 이 그 칸을 일부러 안 고친다), 감사 골드의 안정 키가
    `attributes->>'mentioned_product'` 라 그 불변성에 계약이 얹혀 있다.
  · **방치 금지** — 잘못 배치된 판정은 **유료 요약을 재생성할 때마다 다시 오염시킨다.**

⚠️ 이 파일의 핵심은 마지막 케이스다: 사이드카가 **`_source_material` 출력을 바꾸는가**.
  컬럼에 써졌다는 것만 확인하면, 배관 한 곳(예: `_records_for` 의 SELECT)이 빠져도 통과하고
  요약은 계속 오염된 재료를 받는다 — 무언 실패다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_sentiment_axis_sidecar`
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slime_rag import consolidated_view as cv         # noqa: E402
from slime_rag import extract, pipeline               # noqa: E402


def _row(sound_ev, *, product="빠코볼"):
    return {"mentioned_product": product,
            "sound": {"sentiment": "neg", "evidence": sound_ev},
            "texture": {"sentiment": "pos", "evidence": "쫀득함"}}


def test_escape_in_sound_is_detected():
    """`비즈 탈출`·`후두둑 날라감` 은 소리가 아니다."""
    for ev in ("비즈 탈출 심함", "폼이 후두둑 날라감", "알갱이 날아감"):
        assert extract.escape_in_sound(_row(ev)), ev
    print("✓ 탈출 표지 검출 OK")


def test_normal_sound_evidence_is_untouched():
    """⚠️ **과잉 차단 회귀.** `걀걀거림` 은 사용자가 직접 못박은 정상 sound 근거다.

    (→ [MEMORY.md] 슬라임 속성 어휘 분류 규칙: 걀걀거림=sound, 질감 아님.)
    어휘를 넓히면 진짜 소리 후기가 통째로 사라지고, 그 손실은 화면에 안 보인다.
    """
    for ev in ("걀걀거림 좋음", "뽀득뽀득 소리", "찰박거림", "글루소리 큼"):
        assert not extract.escape_in_sound(_row(ev)), ev
    print("✓ 정상 소리 근거 무변경 OK")


def test_the_flag_never_moves_the_value_to_another_axis():
    """⛔ **다른 축으로 옮겨 담지 않는다** — 옮기면 없던 판단을 만든다(1급 규칙 위반).

    작성자가 지속력 얘기를 했는지는 이 함수가 알 수 없다.
    """
    doc = {"reviews": [_row("비즈 탈출 심함")]}
    before = {k: v for k, v in doc["reviews"][0].items() if k != "sound"}
    assert extract.flag_escape_in_sound(doc) == 1
    rv = doc["reviews"][0]
    assert rv["sound"] is None
    assert rv.get("longevity") is None, "판정을 지속력으로 옮겼다"
    assert {k: v for k, v in rv.items() if k != "sound"} == before, "다른 축이 바뀌었다"
    print("✓ 축 이동 금지 OK")


def test_the_backfill_writes_a_sidecar_and_never_touches_attributes():
    """적재분 백필은 **사이드카만** 쓴다 — `attributes` 는 한 바이트도 안 바뀐다.

    골드 키(`attributes->>'mentioned_product'`)가 그 불변성에 직접 의존한다.
    """
    src = inspect.getsource(pipeline.backfill_sentiment_axis)
    assert "attribute_repairs" in src, "사이드카 컬럼을 안 쓴다"
    assert "SET attributes" not in src and "attributes=%s" not in src, \
        "백필이 원본 attributes 를 덮는다(provenance 파괴 + 골드 키 계약 위반)"
    assert "repair_ledger" in src, "원장을 안 남긴다"
    assert "coalesce(attribute_repairs" in src, \
        "기존 사이드카를 덮어쓴다 — 다른 축의 수리 기록이 사라진다"
    print("✓ 백필: 사이드카 전용 · attributes 무변경 OK")


def test_the_sidecar_reaches_the_summary_material():
    """⚠️⚠️ **핵심 케이스** — 써진 사이드카가 `_source_material` 출력을 실제로 바꾼다.

    '컬럼이 써졌다'만 검사하면 배관 한 곳이 빠져도 통과하고, 요약은 계속 오염된 재료를
    받는다(무언 실패). 여기서는 재료 dict 의 축 자체가 사라지는지를 끝까지 본다.
    """
    dirty = {"sound": {"sentiment": "neg", "evidence": "비즈 탈출 심함"},
             "texture": {"sentiment": "pos", "evidence": "쫀득함"}}
    assert "sound" in cv._source_material([dict(dirty)])
    repaired = dict(dirty)
    repaired[cv.ATTR_REPAIRS_KEY] = {"sound": {"action": "drop", "why": "탈출"}}
    got = cv._source_material([repaired])
    assert "sound" not in got, f"사이드카가 재료에 안 닿았다: {sorted(got)}"
    assert "texture" in got, "다른 축까지 지웠다"
    print("✓ 사이드카 → 요약 재료 반영 OK")


def test_the_read_path_selects_the_sidecar_column():
    """`_records_for` 의 SELECT 에 컬럼이 없으면 사이드카는 소비처에 영원히 안 닿는다."""
    src = inspect.getsource(pipeline._records_for)
    assert "attribute_repairs" in src, "읽기 경로가 사이드카 컬럼을 안 뽑는다"
    assert "ATTR_REPAIRS_KEY" in src, "레코드에 얹는 키가 정본 상수가 아니다"
    schema = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS attribute_repairs JSONB" in schema, \
        "스키마 마이그레이션이 없다"
    print("✓ 읽기 경로 · 스키마 배관 OK")


def test_the_extraction_prompt_states_the_rule():
    """규칙은 프롬프트에도 적고 **강제는 코드가** 한다 — 전언 차단과 같은 이중화."""
    assert "탈출" in extract.LAYER2_SYSTEM, "추출 프롬프트에 규칙이 없다"
    print("✓ 프롬프트 규칙 명시 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
