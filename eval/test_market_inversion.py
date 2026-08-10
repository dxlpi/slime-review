# -*- coding: utf-8 -*-
"""제품→마켓 역인덱스 게이트 — **채우는 것**과 **덮지 않는 것**의 균형.

이 파일이 지키는 문장 넷이고, 넷 다 반대 방향으로 당긴다:
  · 원문이 마켓을 아예 안 말했고 제품명이 한 마켓 소유면 채운다(그래야 후기가 화면에 닿는다).
  · 원문이 마켓을 말했는데 해소 실패한 경우는 **건드리지 않는다**(보류는 개체연결의 판정이다).
  · 1층(`specs`)이 그 이름을 알면 레지스트리는 보지 않는다 — 합치면 있던 판정이 보류로 퇴화한다.
  · 채운 행은 직접 매칭과 **구분 가능**해야 한다. 아니면 오귀속을 되돌릴 방법이 없다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_market_inversion`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag import linking                       # noqa: E402


def _kb() -> linking.KB:
    """가짜 KB — 디스크 KB 를 읽지 않는다(게이트가 데이터 변경에 흔들리면 안 된다).

    `ㅁㅁ` 를 공유하는 두 마켓을 일부러 넣는다: 초성 충돌 → 보류 경로가 이 파일의 핵심
    회귀 중 하나라, 충돌을 만들 수 있는 KB 가 아니면 그 케이스를 못 쓴다.
    """
    def m(word, cho, **kw):
        return {"market": word + "슬라임", "market_word": word, "handle": kw.get("handle", word),
                "handles_alt": [], "aliases": kw.get("aliases", []), "choseong": cho,
                "choseong_aliases": [], "products": []}
    return linking.KB({"markets": [
        m("지나", "ㅈㄴ", handle="slime_gina_", aliases=["슬지나"]),
        m("머머", "ㅁㅁ"),
        m("미미", "ㅁㅁ"),                            # 초성 충돌 상대
        m("늪지", "ㄴㅈ"),
        m("봄", "ㅂ"),
    ]})


KB = _kb()
# 1층: 지나가 빠코볼을, 늪지가 디폼클리어를 판다. `한줌` 은 두 마켓이 같은 이름을 쓴다.
SPEC_PAIRS = [("지나", "빠코볼"), ("늪지", "디폼클리어"), ("머머", "한줌"), ("미미", "한줌")]
# 레지스트리: 1층보다 넓다. `빠코볼` 을 **다른 마켓**으로도 갖고 있어 A2 회귀를 만든다.
REGISTRY = {"지나": ["아생케", "먹구름큐브"], "봄": ["허니넛츠시리얼", "빠코볼"],
            "늪지": ["액괴"], "머머": ["액괴"]}
INV = linking.build_market_inversion(SPEC_PAIRS, REGISTRY, excludes=["액괴"])


def _link(mentioned_market, product, inversion=INV):
    return linking.link(mentioned_market, product, kb=KB, inversion=inversion)


# ------------------------------------------------------------------ A1: 발동 조건
def test_fills_market_when_the_source_never_named_one():
    """마켓 미언급 + 제품명 유일소유 → 채운다. 이게 이 기능의 전부다."""
    r = _link(None, "빠코볼")
    assert r.market == "지나", r
    assert r.abstained is False
    assert r.market_confidence == linking.INVERSION_CONF_SPEC
    assert linking.REASON_INVERSION_SPEC in r.reason, r.reason
    print("✓ 마켓 미언급 + 유일소유 → 채움 OK")


def test_never_overrides_an_abstain_that_came_from_a_real_mention():
    """원문이 마켓을 **말했는데** 충돌/미발견으로 보류된 경우는 건드리지 않는다(A1).

    ⛔ 되돌리지 말 것 — 여기서 채우면 개체연결이 증거를 보고 내린 보류를 제품명으로
      조용히 뒤집는 것이 된다. `ㅁㅁ` 는 머머일 수도 미미일 수도 있고, 그 모호함이
      후기 원문의 사실이다. 제품명이 그 사실을 지울 권한은 없다.
    """
    for mentioned, label in (("ㅁㅁ", "초성 충돌"), ("자사몰", "미발견")):
        r = _link(mentioned, "빠코볼")
        assert r.market is None, f"{label}: 보류가 역인덱스로 뒤집혔다 → {r.market}"
        assert r.abstained is True
        assert linking.REASON_INVERSION_SPEC not in r.reason, r.reason
    print("✓ 언급이 있었던 보류는 역인덱스가 안 건드림 OK")


def test_a_resolved_market_is_left_alone():
    """직접 매칭이 성공한 행은 역인덱스가 손대지 않는다(확신도도 그대로)."""
    r = _link("슬지나", "빠코볼")
    assert (r.market, r.market_confidence) == ("지나", 0.95), r
    print("✓ 직접 매칭 성공분 무변경 OK")


# ------------------------------------------------------------------ A2: 1층 우선
def test_layer1_wins_and_the_registry_is_not_consulted():
    """1층이 그 이름을 알면 레지스트리는 **보지 않는다**.

    `빠코볼` 은 1층에서 지나 단독이고 레지스트리에는 봄에도 있다. 두 집합을 합치면
    2소유가 되어 보류로 **퇴화**한다 — 있던 판정이 사라지는 방향이라 화면엔
    '마켓 없는 후기'로만 보이고 원인이 안 보인다(R2).
    """
    r = _link(None, "빠코볼")
    assert r.market == "지나", f"1층 단일판정이 레지스트리 때문에 퇴화했다: {r}"
    assert r.market_confidence == linking.INVERSION_CONF_SPEC
    print("✓ 1층 우선(레지스트리 미참조) OK")


def test_layer1_ambiguity_does_not_fall_through_to_the_registry():
    """1층이 **알지만 못 고르는** 이름은 거기서 끝난다 — 레지스트리로 내려가지 않는다.

    '키가 없다'와 '값이 None 이다'는 다른 뜻이다. 후자에서 내려가면 1층의 모호함을
    레지스트리가 임의로 해소하게 되고, 그건 1층을 뒤에 놓은 것과 같다.
    """
    inv = linking.build_market_inversion(SPEC_PAIRS, {"봄": ["한줌"]}, excludes=[])
    r = linking.link(None, "한줌", kb=KB, inversion=inv)
    assert r.market is None, f"1층 모호가 레지스트리로 해소됐다: {r.market}"
    assert linking.REASON_INVERSION_AMBIGUOUS in r.reason, r.reason
    print("✓ 1층 모호 → 레지스트리 폴스루 없음 OK")


def test_registry_fills_only_names_layer1_never_heard_of():
    """1층이 모르는 이름만 레지스트리가 채우고, 확신도는 1층보다 낮다(A3)."""
    r = _link(None, "아생케")
    assert r.market == "지나", r
    assert r.market_confidence == linking.INVERSION_CONF_REGISTRY
    assert linking.REASON_INVERSION_REGISTRY in r.reason, r.reason
    print("✓ 레지스트리 폴백 OK")


def test_registry_ambiguity_holds():
    """레지스트리에서 여러 마켓이 같은 이름을 가지면 채우지 않는다."""
    inv = linking.build_market_inversion([], {"봄": ["공용이름"], "늪지": ["공용이름"]},
                                         excludes=[])
    r = linking.link(None, "공용이름", kb=KB, inversion=inv)
    assert r.market is None and r.abstained is True, r
    print("✓ 레지스트리 다중소유 → 보류 OK")


# ------------------------------------------------------------------ A3: 롤백 가능성
def test_inversion_confidences_are_distinguishable_from_direct_matches():
    """전용 확신도가 직접 매칭과도, 서로도 다르다 — 롤백의 유일한 열쇠다.

    ⚠️ 직접 매칭 값을 **상수로 적지 않는다**. 하드코딩하면 `resolve_market` 쪽 값이 바뀌어
      역인덱스 값과 충돌해도 이 테스트가 못 잡는다 — 실제 경로에서 뽑아 비교한다.
    """
    direct = {KB.resolve_market("지나")[1], KB.resolve_market("ㄴㅈ")[1]}   # 표면형·초성 단일
    assert len(direct) == 2, f"직접 매칭 값을 못 뽑았다: {direct}"
    for conf in linking.INVERSION_CONFS:
        assert conf not in direct, f"역인덱스 확신도가 직접 매칭과 겹친다: {conf}"
        assert conf < min(direct), "역인덱스는 직접 매칭보다 낮아야 한다"
    assert linking.INVERSION_CONF_SPEC != linking.INVERSION_CONF_REGISTRY, \
        "두 층이 같은 값이면 잡음 층만 골라 되돌릴 수 없다"
    assert set(linking.INVERSION_CONFS) == {linking.INVERSION_CONF_SPEC,
                                            linking.INVERSION_CONF_REGISTRY}
    # 보류선 아래로 내려가면 '채웠는데 확신도는 abstain 선 아래'라는 모순된 행이 남는다.
    from slime_rag.config import settings
    assert min(linking.INVERSION_CONFS) >= settings.link_abstain_threshold
    print("✓ 확신도 분리(롤백 가능) OK")


def test_rollback_predicate_casts_to_real():
    """롤백 조건절이 `::real` 캐스트를 갖는다 — **이 게이트가 없어서 한 번 놓쳤다.**

    `reviews.market_confidence` 는 `REAL`(4바이트)인데 리터럴·바인딩 파라미터는 `float8` 이다.
    캐스트를 빼면 `WHERE market_confidence = 0.80` 이 **0행**을 돌려준다(실측: `= 0.85` 0행 vs
    `= 0.85::real` 397행). 조용히 빈 결과라 '되돌릴 게 없다'와 구분되지 않는다 — 롤백 경로가
    있다고 믿으면서 실제로는 없는 상태가 된다.
    ⚠️ 파이썬 float 비교만 검사하던 앞 테스트로는 원리적으로 못 잡는다(DB 타입 문제라서).
      DB 없이 잡으려면 조건절 문자열을 계약으로 고정하는 수밖에 없다.
    """
    from slime_rag import pipeline
    assert "::real" in pipeline.INVERSION_ROLLBACK_WHERE, \
        f"캐스트가 빠졌다 — 이 조건절은 0행을 돌려준다: {pipeline.INVERSION_ROLLBACK_WHERE}"
    assert "market_confidence" in pipeline.INVERSION_ROLLBACK_WHERE
    import inspect
    src = inspect.getsource(pipeline.revert_market_inversion)
    assert "INVERSION_ROLLBACK_WHERE" in src, "롤백 함수가 정본 조건절을 안 쓴다"
    print("✓ 롤백 조건절 ::real 캐스트 OK")


def test_revert_can_target_the_registry_tier_alone():
    """레지스트리 층만 골라 되돌릴 수 있어야 한다 — 두 확신도를 가른 이유가 이것이다.

    잡음은 레지스트리 쪽에 있다(사람이 승격한 목록이 아니라 유도된 후보). 1층 층까지 같이
    날리면 멀쩡한 귀속 ~101행을 잃는다.
    """
    import inspect
    from slime_rag import pipeline
    sig = inspect.signature(pipeline.revert_market_inversion)
    assert "tier" in sig.parameters, "층별 롤백 인자가 없다"
    assert sig.parameters["dry_run"].default is True, "롤백도 dry_run 이 기본이어야 한다"
    # 인자 **존재**만 보면 매핑이 뒤집혀도 못 잡는다 — 각 tier 가 어떤 확신도를 고르는지 고정한다.
    src = inspect.getsource(pipeline.revert_market_inversion)
    assert '"spec": [linking.INVERSION_CONF_SPEC]' in src, "spec 층 매핑이 다르다"
    assert '"registry": [linking.INVERSION_CONF_REGISTRY]' in src, "registry 층 매핑이 다르다"
    assert "None: list(linking.INVERSION_CONFS)" in src, "미지정은 두 층 모두여야 한다"
    print("✓ 층별 롤백 가능 + 층↔확신도 매핑 고정 OK")


def test_the_registry_tier_is_off_by_default_at_ingest():
    """수집 경로의 역인덱스는 **1층 층만** 쓴다(레지스트리는 옵트인).

    ⛔ 기본값을 켬으로 되돌리지 말 것. 백필은 `dry_run` + 사람 검수(A4) + 층 선택이 앞을
      막는데, 레지스트리 추론이 수집 때만 **무검수로** 돌면 오귀속이 사람이 따라잡을 수 없는
      속도로 자란다(제외 목록은 언제나 사후다). 같은 저장소가 거울 방향으로 이미 이렇게
      판정했다 — `derive_product_registry` 는 고빈도 태그를 자동 배제하지 않고 사람이 승격한다.
      대가는 실측으로 작다: 레지스트리 층은 107행 중 6행(5.6%)이고 1층이 94%를 낸다.
    """
    import inspect
    from slime_rag import pipeline
    sig = inspect.signature(pipeline.market_inversion_index)
    assert sig.parameters["include_registry"].default is False, \
        "수집 경로에서 레지스트리 층이 기본으로 켜져 있다(무검수 추론)"
    # 백필은 반대로 두 층을 다 만든다 — 거기선 사람이 무엇을 채울지 보고 고른다.
    back = inspect.getsource(pipeline.backfill_market_from_product)
    assert "include_registry=True" in back, "백필이 레지스트리 층을 못 본다"
    print("✓ 레지스트리 층: 수집 기본 끔 · 백필 켬 OK")


# ------------------------------------------------------------------ 제외 목록
def test_excluded_names_are_never_filled():
    """사람이 검수해서 뺀 이름은 어느 층에서도 안 채운다(A4 의 집행 장치)."""
    r = _link(None, "액괴")
    assert r.market is None, f"제외 목록이 무시됐다: {r.market}"
    assert linking.REASON_INVERSION_EXCLUDED in r.reason, r.reason
    print("✓ 제외 목록 OK")


def test_a_name_written_into_the_overlay_file_reaches_the_index():
    """오버레이 **파일에 적은 이름**이 실제로 역인덱스 제외까지 도달한다.

    ⚠️ 목록이 비어 있는 동안 '파일이 존재한다'만 검사하면 그 테스트는 아무것도 안 지킨다 —
      형식(`{"excludes": [...]}`)을 잘못 읽어도 조용히 빈 집합이 되고, 그 상태가 정상과
      구분되지 않는다. 임시 파일로 왕복을 강제한다(정본 파일은 건드리지 않는다).
    """
    import json
    import tempfile
    from pathlib import Path

    original = linking.MARKET_INVERSION_EXCLUDES_PATH
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "excludes.json"
        path.write_text(json.dumps({"_note": "테스트", "excludes": ["봄날의 배달부"]},
                                   ensure_ascii=False), encoding="utf-8")
        linking.MARKET_INVERSION_EXCLUDES_PATH = path
        try:
            inv = linking.build_market_inversion(SPEC_PAIRS, {"지나": ["봄날의배달부"]})
            r = linking.link(None, "봄날의배달부", kb=KB, inversion=inv)
            assert r.market is None, f"파일의 제외 이름이 채워졌다: {r.market}"
            assert linking.REASON_INVERSION_EXCLUDED in r.reason, r.reason
        finally:
            linking.MARKET_INVERSION_EXCLUDES_PATH = original
    print("✓ 오버레이 파일 → 역인덱스 제외 왕복 OK")


def test_shipped_overlay_file_exists():
    """정본 오버레이가 저장소에 실제로 있다 — 없으면 런타임에 조용히 '제외 없음'이 된다.

    ⚠️ 이 단언은 **파일이 커밋돼야** CI 에서 통과한다(eval/CLAUDE.md 의 기존 교훈: gitignore
      되거나 미커밋인 파일에 의존하는 테스트는 로컬에서만 통과한다). 그게 의도다 —
      사람 검수 오버레이가 클론에 없으면 이 기능은 검수 없이 도는 상태가 된다.
    """
    ex = linking.load_market_inversion_excludes()
    assert isinstance(ex, set)
    assert linking.MARKET_INVERSION_EXCLUDES_PATH.exists(), \
        "data/market_inversion_excludes.json 이 없다 — 커밋했는지 확인"
    print(f"✓ 정본 오버레이 존재 OK (현재 {len(ex)}개 제외)")


# ------------------------------------------------------------------ 하위호환·경계
def test_absent_inversion_behaves_exactly_as_before():
    """미주입이면 이 기능이 없던 것과 똑같다 — 기존 호출부를 안 깨는 게 전제다."""
    r = linking.link(None, "빠코볼", kb=KB)
    assert r.market is None and r.abstained is True, r
    assert "마켓 미언급" in r.reason, r.reason
    print("✓ 역인덱스 미주입 시 무변경 OK")


def test_alias_normalised_name_is_the_lookup_key():
    """약칭 사전이 적용된 **정규 제품명**으로 조회한다.

    ⛔ 마켓 분기 뒤로 미루지 말 것 — 정규화 전 표면형으로 조회하게 되어
      `data/product_aliases.json` 시드가 역인덱스에만 안 먹는 절름발이가 된다.
    """
    r = linking.link(None, "빠코", kb=KB, aliases={"빠코": "빠코볼"}, inversion=INV)
    assert r.product == "빠코볼" and r.market == "지나", r
    print("✓ 약칭 정규화 후 조회 OK")


def test_link_post_can_fill_different_markets_per_product():
    """마켓을 안 밝힌 비교글이면 항목마다 다른 마켓이 붙는다 — 그게 옳다."""
    doc = {"market": None, "reviews": [{"mentioned_product": "빠코볼"},
                                       {"mentioned_product": "디폼클리어"}]}
    got = [lk.market for lk in linking.link_post(doc, kb=KB, inversion=INV)]
    assert got == ["지나", "늪지"], got
    print("✓ 비교글 제품별 마켓 분리 OK")


def test_linking_stays_db_free():
    """`linking` 은 DB 무의존이 계약이다 — 1층 쌍은 호출부가 주입한다.

    이 게이트가 없으면 '편한 김에 여기서 SELECT 한 번' 이 들어오고, 그 순간 이 파일을
    포함한 오프라인 테스트가 전부 Postgres 를 요구하게 된다.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(linking))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
    assert not (imported & {"db", ".db", "slime_rag.db", "psycopg"}), \
        f"linking 이 DB 를 import 한다: {imported}"
    print("✓ linking DB 무의존 OK")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\n전체 {len(fns)}개 통과")
