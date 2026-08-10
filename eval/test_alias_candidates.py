# -*- coding: utf-8 -*-
"""
별칭 후보 유도 오프라인 테스트 — 네트워크·LLM 미접촉/무비용, DB 는 대역(fake conn)만 쓴다.

검증 대상은 '어느 이름이 같은 제품이다'라는 **판정**이 아니다(그건 사람이
`data/product_aliases.json` 을 손으로 고쳐서 내리는 판정이고, 여긴 후보만 낸다) — 순수
쌍 생성 규칙(접두·편집거리1·레지스트리 조회)과, **자동 병합이 절대 없다**는 성질이다
(쿨라임 교훈, MEMORY.md).

실행:  python -m eval.test_alias_candidates   (repo 루트에서)
"""
from __future__ import annotations
import dataclasses
import json
import tempfile
from pathlib import Path

from slime_rag import config, pipeline
from slime_rag.llm_ops import summary

ALIASES_PATH = Path(__file__).resolve().parent.parent / "data" / "product_aliases.json"


# ---------------------------------------------------------------- 순수 함수 단위(무 DB)
def test_levenshtein_basic():
    assert pipeline._levenshtein("가나다", "가나다") == 0
    assert pipeline._levenshtein("진저브레드", "진저브래드") == 1     # 치환 1
    assert pipeline._levenshtein("버건디", "버건드") == 1            # 치환 1(끝글자)
    assert pipeline._levenshtein("빠코볼", "빠코볼미니") == 2         # 삽입 2(접두 케이스)
    assert pipeline._levenshtein("", "가") == 1
    print("✓ Levenshtein 기본값 OK")


def test_prefix_pairs_found_and_no_self_pair():
    """접두 쌍은 찾되, 동일 이름은 자기 자신과 쌍이 되지 않는다."""
    names = ["빠코볼", "빠코볼미니", "빠코볼", "허니푸냥이"]
    pairs = pipeline._prefix_pairs(sorted(set(names)))
    assert ("빠코볼", "빠코볼미니") in pairs
    assert all(a != b for a, b in pairs), "동일 이름이 쌍으로 나왔다"
    assert not any(a == "빠코볼" and b == "빠코볼" for a, b in pairs)
    print("✓ 접두 쌍 탐지 + 자기쌍 배제 OK")


def test_prefix_pairs_ignore_whitespace_and_case():
    names = ["아바 크림", "아바크림듬뿍"]
    pairs = pipeline._prefix_pairs(names)
    assert pairs == [("아바 크림", "아바크림듬뿍")], f"공백 무시 접두 판정 실패: {pairs}"
    print("✓ 접두 판정 공백 무시 OK")


def test_edit1_pairs_found_edit2_excluded():
    """편집거리 1 쌍은 잡히고, 편집거리 2 이상은 후보에서 빠진다."""
    names = ["진저브레드", "진저브래드", "완전다른이름프로덕트"]
    pairs = pipeline._edit1_pairs(names)
    assert ("진저브레드", "진저브래드") in pairs, f"편집거리 1 쌍이 안 잡혔다: {pairs}"

    edit2_names = ["가나다라", "가카타파"]     # 세 글자가 달라 편집거리 2 이상
    assert pipeline._levenshtein("가나다라", "가카타파") >= 2
    pairs2 = pipeline._edit1_pairs(edit2_names)
    assert pairs2 == [], f"편집거리 2 이상이 후보로 나왔다: {pairs2}"
    print("✓ 편집거리 1 탐지 / 편집거리 2 배제 OK")


def test_edit1_pairs_require_same_first_char_bucket():
    """비용 가드: 첫 글자가 다르면 편집거리 1이어도(삽입) 이 생성기는 놓친다 — 의도된 절충."""
    # '아빠코볼'과 '빠코볼'은 편집거리 1(어두 삽입)이지만 첫 글자가 달라 버킷이 갈린다.
    assert pipeline._levenshtein("아빠코볼", "빠코볼") == 1
    pairs = pipeline._edit1_pairs(["아빠코볼", "빠코볼"])
    assert pairs == [], "어두 삽입 오타까지 잡으면 문서화된 절충과 어긋난다"
    print("✓ 첫 글자 버킷 절충 확인 OK")


def test_alias_candidates_pure_function_labels_kinds():
    """`_alias_candidates` 는 DB 없이 이름→통계 dict 만으로 3종을 낸다."""
    name_stats = {
        "빠코볼": {"n_reviews": 5, "markets": ["지나"]},
        "빠코볼미니": {"n_reviews": 2, "markets": []},
        "진저브레드": {"n_reviews": 3, "markets": ["봄"]},
        "진저브래드": {"n_reviews": 1, "markets": []},
    }
    # `빠코볼` 은 후기에 쓰인 이름 → 레지스트리 조회 후보가 된다.
    # `허니푸냥이` 는 레지스트리에만 있고 후기엔 0건 → **후보가 아니다**(아래 케이스 참조).
    registry_lookup = {"빠코볼": ["지나"], "허니푸냥이": ["봄"]}
    out = pipeline._alias_candidates(name_stats, registry_lookup)
    kinds = {c["kind"] for c in out}
    assert kinds <= {"prefix", "edit1", "registry"}
    prefix = [c for c in out if c["kind"] == "prefix"]
    edit1 = [c for c in out if c["kind"] == "edit1"]
    registry = [c for c in out if c["kind"] == "registry"]
    assert any(c["name_a"] == "빠코볼" and c["name_b"] == "빠코볼미니" for c in prefix)
    assert any({c["name_a"], c["name_b"]} == {"진저브레드", "진저브래드"} for c in edit1)
    assert any(c["name_a"] == "빠코볼" and c["registry_market"] == "지나" for c in registry)
    # 건수·마켓이 그대로 실렸는지
    p = next(c for c in prefix if c["name_a"] == "빠코볼")
    assert p["n_reviews_a"] == 5 and p["markets_a"] == ["지나"]
    assert p["n_reviews_b"] == 2 and p["markets_b"] is None   # 빈 리스트는 null 로
    print("✓ 순수 후보 생성 3종 라벨링 OK")


def test_registry_kind_only_covers_names_reviews_actually_use():
    """레지스트리 조회 후보는 **후기에 실제로 쓰인 이름**만 낸다.

    ⛔ 되돌리지 말 것 — 레지스트리 전량을 돌면 후기 0건짜리 '자기 자신과의 짝'이 쏟아진다.
      실측(2026-08-10): 그렇게 뽑았더니 2,442건 중 **2,358건(97%)** 이 그 모양이었고 파일이
      660KB/29,800줄로 부풀어, 정작 볼 가치가 있는 84건(prefix 52 · edit1 32)을 덮었다.
      이 파일의 존재 이유는 **사람이 훑어 승격하는 것**이라, 훑을 수 없으면 기능이 없는 것과 같다.
      (그 표기가 어느 마켓 것인지 '확인'해 주는 게 이 종류의 목적인데, 후기가 한 번도 안 쓴
       이름은 확인해 줄 대상 자체가 없다.)
    """
    name_stats = {"쓰인이름": {"n_reviews": 3, "markets": []}}
    registry_lookup = {"쓰인이름": ["봄"], "안쓰인이름": ["봄"]}
    out = pipeline._alias_candidates(name_stats, registry_lookup)
    names = {c["name_a"] for c in out if c["kind"] == "registry"}
    assert names == {"쓰인이름"}, f"후기 0건 이름이 후보에 실렸다: {names}"

    # 후기 건수 0 으로 등록된 이름도 제외된다(딕셔너리에 키만 있는 경우).
    out2 = pipeline._alias_candidates({"영건": {"n_reviews": 0, "markets": []}},
                                      {"영건": ["봄"]})
    assert [c for c in out2 if c["kind"] == "registry"] == [], "후기 0건인데 후보가 났다"
    print("✓ 레지스트리 조회는 후기에 쓰인 이름만 OK")


def test_registry_kind_requires_exactly_one_market():
    """레지스트리 조회는 마켓이 **정확히 하나**일 때만 낸다 — 여럿이면 모호해서 보류."""
    name_stats = {"애매한이름": {"n_reviews": 1, "markets": []}}
    registry_lookup = {"애매한이름": ["지나", "봄"]}
    out = pipeline._alias_candidates(name_stats, registry_lookup)
    assert out == [], f"마켓 다중 일치인데 레지스트리 후보를 냈다: {out}"
    print("✓ 레지스트리 조회 다중마켓 보류 OK")


def test_deterministic_sort_order():
    """같은 입력이면 같은 순서 — 파일 diff 가 깨끗해야 한다."""
    name_stats = {
        "다": {"n_reviews": 1, "markets": []}, "다나": {"n_reviews": 1, "markets": []},
        "가": {"n_reviews": 1, "markets": []}, "가나": {"n_reviews": 1, "markets": []},
    }
    out1 = pipeline._alias_candidates(name_stats)
    out2 = pipeline._alias_candidates(dict(reversed(list(name_stats.items()))))
    assert out1 == out2, "입력 순서에 따라 출력이 달라졌다 — 결정적이지 않다"
    print("✓ 결정적 정렬 OK")


# ---------------------------------------------------------------- 공개 API(대역 conn)
class _FakeConn:
    """`pipeline.connect()` 대역. 쿼리 순서(reviews → specs)에 맞춰 고정 결과를 준다."""

    def __init__(self, review_rows, spec_rows):
        self._results = [review_rows, spec_rows]
        self._i = 0
        self.committed = False

    def execute(self, sql, *_a, **_k):
        self._pending = self._results[self._i]
        self._i += 1
        return self

    def fetchall(self):
        return self._pending

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _with_fake_db(review_rows, spec_rows, fn):
    original = pipeline.connect
    pipeline.connect = lambda: _FakeConn(review_rows, spec_rows)
    try:
        return fn()
    finally:
        pipeline.connect = original


def _isolated(tmp: Path):
    patched = dataclasses.replace(config.settings,
                                  product_alias_candidates_path=tmp / "product_alias_candidates.json")
    pipeline.settings = patched
    return patched


def test_dry_run_default_writes_nothing():
    """`dry_run` 은 기본 True 이고, 그때는 후보 파일을 쓰지 않는다."""
    tmp = Path(tempfile.mkdtemp(prefix="alias-cand-"))
    _isolated(tmp)
    review_rows = [("빠코볼", "지나"), ("빠코볼미니", None)]
    out = _with_fake_db(review_rows, [], lambda: pipeline.derive_alias_candidates())
    assert out["dry_run"] is True
    assert out["path"] is None
    assert not (tmp / "product_alias_candidates.json").exists(), "dry_run 인데 파일을 썼다"
    print("✓ dry_run 기본값 True · 무기록 OK")


def test_dry_run_false_writes_file_with_no_body_text():
    """`dry_run=False` 면 파일을 쓰고, 그 파일엔 원문 본문이 없어야 한다(ADR-0013)."""
    tmp = Path(tempfile.mkdtemp(prefix="alias-cand-"))
    _isolated(tmp)
    review_rows = [("빠코볼", "지나"), ("빠코볼미니", None), ("진저브레드", "봄"),
                   ("진저브래드", None)]
    out = _with_fake_db(review_rows, [("지나", "빠코볼")],
                        lambda: pipeline.derive_alias_candidates(dry_run=False))
    assert out["dry_run"] is False
    path = tmp / "product_alias_candidates.json"
    assert path.exists() and out["path"] == str(path)
    blob = path.read_text(encoding="utf-8")
    doc = json.loads(blob)
    assert "candidates" in doc and len(doc["candidates"]) == out["candidates"]
    # ADR-0013: 이름·건수·마켓만 있어야 한다 — 캡션/본문 문자열이 새지 않았는지 확인.
    forbidden = ("캡션", "body", "본문")
    for c in doc["candidates"]:
        assert set(c) <= {"kind", "name_a", "name_b", "n_reviews_a", "n_reviews_b",
                          "markets_a", "markets_b", "registry_market"}
    assert not any(w in blob for w in forbidden), "산출물에 원문/본문 계열 문자열이 섞였다"
    print("✓ dry_run=False 기록 + 캡션 본문 미유출 OK")


def test_zero_llm_calls():
    tmp = Path(tempfile.mkdtemp(prefix="alias-cand-"))
    _isolated(tmp)
    before = summary()["calls"]
    _with_fake_db([("빠코볼", "지나")], [],
                 lambda: pipeline.derive_alias_candidates(dry_run=False))
    assert summary()["calls"] == before, "LLM 을 불렀다 — 이 경로는 무과금이어야 한다"
    print("✓ LLM 호출 0회 OK")


def test_product_aliases_json_never_touched():
    """**자동 병합 없음** — `data/product_aliases.json` 은 이 함수가 절대 못 건드린다.

    `settings` 를 임시 경로로 갈아끼워도 `product_aliases.json` 은 별개 상수 경로
    (`linking.load_product_aliases` 의 `ROOT/data/product_aliases.json`)로 남는다 —
    여기선 실제 저장소 파일의 mtime/내용이 이 함수 호출 전후로 그대로인지를 확인한다.
    """
    before_mtime = ALIASES_PATH.stat().st_mtime if ALIASES_PATH.exists() else None
    before_text = ALIASES_PATH.read_text(encoding="utf-8") if ALIASES_PATH.exists() else None

    tmp = Path(tempfile.mkdtemp(prefix="alias-cand-"))
    _isolated(tmp)
    review_rows = [("빠코볼", "지나"), ("빠코볼미니", None)]
    _with_fake_db(review_rows, [("지나", "빠코볼")],
                 lambda: pipeline.derive_alias_candidates(dry_run=False))

    if before_mtime is not None:
        assert ALIASES_PATH.stat().st_mtime == before_mtime, \
            "product_aliases.json 의 mtime 이 바뀌었다 — 자동 병합이 일어났다"
        assert ALIASES_PATH.read_text(encoding="utf-8") == before_text
    else:
        assert not ALIASES_PATH.exists(), "존재하지 않던 product_aliases.json 이 생성됐다"
    print("✓ product_aliases.json 미접촉(자동 병합 없음) OK")


if __name__ == "__main__":
    test_levenshtein_basic()
    test_prefix_pairs_found_and_no_self_pair()
    test_prefix_pairs_ignore_whitespace_and_case()
    test_edit1_pairs_found_edit2_excluded()
    test_edit1_pairs_require_same_first_char_bucket()
    test_alias_candidates_pure_function_labels_kinds()
    test_registry_kind_only_covers_names_reviews_actually_use()
    test_registry_kind_requires_exactly_one_market()
    test_deterministic_sort_order()
    test_dry_run_default_writes_nothing()
    test_dry_run_false_writes_file_with_no_body_text()
    test_zero_llm_calls()
    test_product_aliases_json_never_touched()
    print("\n모든 오프라인 테스트 통과 ✅")
