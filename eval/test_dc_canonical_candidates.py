# -*- coding: utf-8 -*-
"""디시 약칭·오타 **정본 후보** 유도(감사 D6) — 자동 병합 금지 + 마켓 스코프.

기존 `derive_alias_candidates` 는 코퍼스 전역 **접두/편집거리** 규칙이라 감사가 잡은
오타(`갈배괴물`→`간배괴물`)·조사결합(`뼈갈우나`)·띄어쓰기(`와이풀 그린티`)를 원리적으로 못
잡는다. 그래서 부분일치 + `difflib`(cutoff 0.86)로 승격하되, **하드 제약이 하나** 붙는다:

  ⛔ **마켓을 모르면 접지 않는다.** 감사 문서를 만들면서 전 마켓을 한 풀로 놓고 접었더니
    `진저브레드`(ㅋㅋㅁ 제품)가 `진저브레드아이싱키트`(베이퍼)로 접혔다 — D2c 와 같은
    이름 충돌 실패다. 그 결과 이 Phase 의 커버리지는 **미등재 마켓 KB 등록(사람 입력)에
    종속**되고, ㅋㅋㅁ 이 등록되지 않으면 그 스레드의 D6 행은 **후보조차 안 나오는 것이
    정답**이다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_dc_canonical_candidates`
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slime_rag import pipeline                        # noqa: E402

# 감사 시점 마켓 스코프를 고정한 픽스처 — `difflib` 는 후보 풀 **정렬**에 민감해서,
# 풀 구성 방식이 다르면 같은 이름에 다른 정본이 조용히 붙는다.
NUPJI = {"간배괴물", "엉겁처치", "오마카슬", "오마카슬3", "뼈갈우나맛사탕"}
VAPOR = {"진저브레드아이싱키트", "와이풀그린티"}


def test_short_name_typos_are_human_rulings_not_derived_candidates():
    """⚠️ **cutoff 0.86 은 짧은 이름의 1글자 오타를 안 잡는다** — 그게 의도다.

    `갈배괴물`→`간배괴물` 은 4글자 중 1글자 차이라 유사도 0.75 다. 0.86 을 넘기려면 cutoff
    를 내려야 하는데, 내리면 서로 다른 짧은 제품명끼리 붙기 시작한다(과잉 병합의 손실은
    화면에 안 보인다 — 후기 두 건이 한 건이 될 뿐 빈칸이 안 생긴다).

    그래서 감사도 이 건들을 **규칙이 아니라 `OVERRIDES` 로 손수** 판정했다(ROW#397·1139).
    그런 판정이 사는 자리는 이 함수가 아니라 `data/product_aliases.json` 이다 —
    `spec_overrides.json`·`market_inversion_excludes.json` 과 같은 가족(사람 판단 오버레이).
    ⛔ 이 케이스를 통과시키려고 cutoff 를 내리지 말 것.
    """
    assert pipeline.dc_canonical_target("갈배괴물", NUPJI) == (None, None)
    assert pipeline.dc_canonical_target("엉겁처지", NUPJI) == (None, None)
    print("✓ 짧은 이름 오타는 유도 대상이 아님(사람 판정 자리) OK")


def test_spacing_and_particle_variants_fold():
    """`와이풀 그린티`(띄어쓰기) · `뼈갈우나`(조사 결합) — 전역 접두 규칙으론 원리적으로 못 잡는다.

    띄어쓰기 변형은 **부분일치가 아니다**(공백 때문에 substring 이 성립하지 않는다) —
    `difflib` 근사가 잡는다. 그래서 cutoff 0.86 이 이 함수의 계약이다.
    """
    canon, why = pipeline.dc_canonical_target("와이풀 그린티", VAPOR)
    assert (canon, why) == ("와이풀그린티", "철자 근사 1건"), (canon, why)
    canon, why = pipeline.dc_canonical_target("뼈갈우나맛", NUPJI)
    assert canon == "뼈갈우나맛사탕", (canon, why)
    # 이미 정본이면 아무것도 제안하지 않는다.
    assert pipeline.dc_canonical_target("와이풀그린티", VAPOR) == (None, None)
    assert pipeline._CANON_CUTOFF == 0.86, "감사 `canon_candidates` 와 cutoff 가 갈렸다"
    print("✓ 띄어쓰기·조사 결합 정본화 OK")


def test_ordinal_suffixes_are_never_auto_folded():
    """차수 접미(`오마카슬` vs `오마카슬3`)는 **사람이 골라야** 한다 — 후보에서 뺀다."""
    canon, why = pipeline.dc_canonical_target("오마카슬", {"오마카슬", "오마카슬3"})
    assert canon is None, canon
    canon, why = pipeline.dc_canonical_target("오마카", {"오마카3"})
    assert canon is None and "차수 접미" in (why or ""), (canon, why)
    print("✓ 차수 접미 후보 제외 OK")


def test_gingerbread_never_folds_across_markets():
    """⛔ **이름 붙인 회귀** — `진저브레드`(ㅋㅋㅁ)가 `진저브레드아이싱키트`(베이퍼)로 안 접힌다.

    마켓 스코프가 하드 제약인 이유 전부가 이 한 줄이다. 베이퍼 풀 안에서라면 접히는 게
    맞지만, `진저브레드` 를 쓴 행의 마켓은 **ㅋㅋㅁ 이고 KB 에 없다** — 그래서 그 행은
    애초에 후보 생성 대상이 아니어야 한다(아래 `market IS NULL` 케이스).
    """
    # 베이퍼 풀에서는 접힌다(규칙 자체는 맞다).
    assert pipeline.dc_canonical_target("진저브레드", VAPOR)[0] == "진저브레드아이싱키트"
    # 그런데 그 행의 마켓 풀이 없으면(=미등재) 아무 후보도 안 나온다.
    assert pipeline.dc_canonical_target("진저브레드", set()) == (None, None)
    # 전 마켓 합집합을 쓰면 정확히 그 사고가 난다 — 그래서 합치지 않는다.
    merged = NUPJI | VAPOR
    assert pipeline.dc_canonical_target("진저브레드", merged)[0] == "진저브레드아이싱키트", \
        "합집합에서 안 접히면 이 케이스의 전제가 바뀐 것 — 다시 볼 것"
    print("✓ 진저브레드 교차 마켓 접기 금지(픽스처 회귀) OK")


def test_the_deriver_never_touches_rows_without_a_market():
    """`market IS NULL` 행은 SQL 단계에서 제외된다 — 후보를 **내지 않는 것이 정답**이다."""
    src = inspect.getsource(pipeline.derive_dc_canonical_candidates)
    assert "market IS NOT NULL" in src, "마켓 미상 행이 후보 생성에 들어간다"
    assert "_market_product_pools" in src, "마켓 스코프 풀을 안 쓴다"
    print("✓ 마켓 미상 행 후보 0 OK")


def test_promotion_stays_manual():
    """⛔ 자동 병합 금지 — 승격은 사람이 `data/product_aliases.json` 에서 한다."""
    # ⚠️ 경로를 **문자열 리터럴**로 찾는다 — 독스트링의 백틱 인용(`data/product_aliases.json`)
    #   까지 잡으면, 금지를 설명하는 문장을 쓴 것만으로 게이트가 깨진다.
    src = inspect.getsource(pipeline.derive_dc_canonical_candidates)
    assert '"product_aliases.json"' not in src and "'product_aliases.json'" not in src, \
        "후보 유도가 정본 사전을 직접 쓴다"
    assert '"product_alias_candidates.json"' in src, "후보 파일이 아닌 곳에 쓴다"
    # 백필은 **승격된 사전만** 읽는다(후보 파일을 읽지 않는다).
    back = inspect.getsource(pipeline.backfill_product_aliases)
    assert "load_product_aliases" in back
    assert "product_alias_candidates" not in back, "백필이 미승격 후보를 적용한다"
    print("✓ 자동 병합 금지 OK")


def test_the_alias_backfill_reports_collisions_before_writing():
    """개명은 `UNIQUE(source, post_id, product)` 와 충돌하면 UPDATE 가 예외로 죽는다.

    이 저장소가 이미 판정한 자리다 — 자동 DELETE 가 아니라 **수동 판단**이 필요하다.
    그래서 ① dry_run 이 `collision_list` 를 먼저 내고 ② 접기는 `repair_dc_attribution` 의
    규칙(내용 점수 높은 쪽 생존)을 재사용하며 ③ **삭제 먼저, 개명 나중** 순서를 지킨다.
    """
    src = inspect.getsource(pipeline.backfill_product_aliases)
    assert "collision_list" in src, "충돌 목록을 안 낸다"
    assert "_filled_score" in src, "접기 생존자 선택 규칙이 다르다"
    assert src.index("DELETE FROM reviews") < src.index("UPDATE reviews SET product"), \
        "개명이 삭제보다 먼저다 — 제약 위반으로 죽는다"
    assert "repair_ledger" in src, "개명 원장이 없다(확신도로는 표현이 안 된다)"
    # 마켓 스코프 — 전 마켓 표를 합쳐 쓰면 쿨라임 사고가 재현된다.
    assert "aliases.get(market)" in src, "약칭 표를 마켓별로 안 좁혔다"
    print("✓ 별칭 백필: 충돌 선보고 · 삭제→개명 순서 · 원장 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
