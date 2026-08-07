# -*- coding: utf-8 -*-
"""1층(판매자 스펙) 수집 경로 오프라인 게이트 — 누적성 회귀 방지.

이 파일이 지키는 건 한 문장이다: **1층 수집은 누적이지 교체가 아니다.**
프로필 액터가 계정당 최신 ~12글만 주므로 한 마켓의 제품 목록은 여러 번의 실행에 걸쳐
쌓인다. 그 전제를 깨는 방식이 두 가지 있었고 둘 다 실제로 있었던 버그다:

  1. `_upsert_spec` 이 값 칸을 무조건 덮어써서, 향을 안 적은 글이 나중에 잡히면
     이미 저장된 향이 null 로 날아갔다.
  2. `ingest_seller_profiles` 의 기본 대상이 '스펙이 0개인 마켓'이라, 두 번째 실행부터
     대상이 빈 목록이 됐다 — 누적하라고 만든 경로가 누적을 못 했다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_layer1_collection`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag import linking, pipeline           # noqa: E402


# 판매자 캡션에서 값이 실리는 칸 — 재수집이 여기를 null 로 덮으면 수집이 누적이 아니게 된다.
_VALUE_COLS = ("scent", "base_combo", "slime_type", "official_texture", "source_permalink")


class _CaptureCursor:
    """`_upsert_spec` 이 실제로 보내는 SQL 을 잡아두는 커서 대역."""
    def __init__(self):
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params or ()


def test_upsert_never_overwrites_a_stored_value_with_null():
    """모든 값 칸이 COALESCE 로 보호된다 — 미언급(null)이 기존 값을 지우지 않는다.

    ⚠️ 되돌리는 방식은 `scent=EXCLUDED.scent` 처럼 맨 대입으로 쓰는 것이다. 그러면 같은
      제품이 향을 안 적은 다른 글에서 다시 잡힐 때 저장돼 있던 향이 사라진다. 프로필
      경로는 최신 ~12글만 주므로 한 제품이 여러 글에 걸쳐 잡히는 게 **정상**이고,
      따라서 이건 이론적 위험이 아니라 예정된 사고다.
    """
    cur = _CaptureCursor()
    pipeline._upsert_spec(cur, "지나", "빠코볼", None, None, None, None, None, None)
    flat = " ".join(cur.sql.split())
    assert "ON CONFLICT (market, product) DO UPDATE" in flat, "upsert 가 아니다"
    for col in _VALUE_COLS:
        assert f"{col}=COALESCE(EXCLUDED.{col}, specs.{col})" in flat, \
            f"`{col}` 이 COALESCE 로 보호되지 않는다 — 재수집이 기존 값을 지운다: {flat}"
    print(f"✓ 값 칸 {len(_VALUE_COLS)}개 전부 COALESCE 보호 OK")


def test_upsert_keeps_stored_beads_when_new_extraction_has_none():
    """`beads` 는 배열이라 NULL 이 아니라 **빈 배열**이 '미언급'이다.

    COALESCE 로는 안 걸린다(`{}` 는 NULL 이 아니므로 그대로 덮어쓴다) — cardinality 로 가른다.
    """
    cur = _CaptureCursor()
    pipeline._upsert_spec(cur, "지나", "빠코볼", None, None, None, beads=[])
    flat = " ".join(cur.sql.split())
    assert "cardinality(EXCLUDED.beads) > 0" in flat, \
        f"빈 배열을 '미언급'으로 가르지 않는다 — 저장된 비즈가 날아간다: {flat}"
    print("✓ beads 빈 배열이 저장값을 덮지 않음 OK")


class _FakeKB:
    def __init__(self, markets):
        self.markets = markets


def _with_kb(markets, fn):
    original = linking.load_kb
    linking.load_kb = lambda: _FakeKB(markets)
    try:
        return fn()
    finally:
        linking.load_kb = original


_MARKETS = [
    {"handle": "slime_gina_", "market_word": "지나"},      # specs 이미 있음
    {"handle": "bom__slime",  "market_word": "봄"},        # specs 이미 있음
    {"handle": "yeonzzi_slime", "market_word": "연찌"},    # specs 없음
    {"market": "핸들없음"},                                 # handle 키 자체가 없음 → 대상 아님
]


def test_default_targets_include_markets_that_already_have_specs():
    """기본 대상은 **KB 전체**다 — 스펙이 이미 있는 마켓도 포함된다.

    이 함수의 존재 이유가 '액터가 최신 ~12글만 주니 주기적으로 돌려 앞으로 쌓는 것'이라,
    기본 대상이 '스펙 0개인 마켓'이면 두 번째 실행부터 대상이 빈 목록이 된다.
    ⚠️ 되돌리면 재수집이 조용히 no-op 가 된다 — 실패가 아니라 **성공처럼 보이는 무동작**이라
      가장 알아채기 어렵다.
    """
    out = _with_kb(_MARKETS, lambda: pipeline.ingest_seller_profiles(dry_run=True))
    assert set(out["targets"]) == {"지나", "봄", "연찌"}, \
        f"핸들 있는 마켓 전부가 대상이어야 한다, 실제 {out['targets']}"
    assert "핸들없음" not in out["targets"], "핸들 없는 마켓은 스크랩할 주소가 없다"
    print(f"✓ 기본 대상 = KB 전체 {len(out['targets'])}마켓(스펙 보유분 포함) OK")


def test_only_missing_still_available_as_an_explicit_option():
    """옛 동작(스펙 0개인 마켓만)은 **명시 옵션**으로 남는다 — 첫 훑기용 싼 경로."""
    calls = {"n": 0}

    class _FakeConn:
        def execute(self, *_a, **_k):
            calls["n"] += 1
            return self

        def fetchall(self):
            return [("지나",), ("봄",)]          # 이 둘은 이미 스펙 보유

        def __enter__(self): return self
        def __exit__(self, *exc): return False

    original = pipeline.connect
    pipeline.connect = lambda: _FakeConn()
    try:
        out = _with_kb(_MARKETS, lambda: pipeline.ingest_seller_profiles(
            only_missing=True, dry_run=True))
    finally:
        pipeline.connect = original
    assert out["targets"] == ["연찌"], f"스펙 없는 마켓만 남아야 한다, 실제 {out['targets']}"
    assert calls["n"] == 1, "only_missing 일 때만 DB 를 본다"
    print("✓ only_missing=True 는 스펙 없는 마켓만 OK")


def test_dry_run_touches_nothing_and_reports_cost():
    """`dry_run=True` 는 네트워크·LLM·DB 미접촉이고 예상 비용을 돌려준다.

    유료 경로라 '실수로 돌렸다'가 없어야 한다 — 기본값이 dry_run 인 것도 같은 이유다.
    """
    out = _with_kb(_MARKETS, lambda: pipeline.ingest_seller_profiles(dry_run=True))
    assert out["dry_run"] is True
    assert "collected_posts" not in out, "dry_run 인데 수집 결과가 있다 — 네트워크를 탔다"
    assert out["apify_cost_usd"] > 0 and "handles" in out
    print(f"✓ dry_run 무접촉 · 예상비용 ${out['apify_cost_usd']} 보고 OK")


if __name__ == "__main__":
    test_upsert_never_overwrites_a_stored_value_with_null()
    test_upsert_keeps_stored_beads_when_new_extraction_has_none()
    test_default_targets_include_markets_that_already_have_specs()
    test_only_missing_still_available_as_an_explicit_option()
    test_dry_run_touches_nothing_and_reports_cost()
    print("\n1층 수집 경로 오프라인 테스트 통과 ✅")
