# -*- coding: utf-8 -*-
"""
제품 레지스트리 유도 오프라인 테스트 — 네트워크·LLM·DB 미접촉/무비용.

검증 대상은 '해시태그 → 제품 후보' 규칙 그 자체가 아니라(그건 `extract.product_hashtags`
소관이고 `test_product_repair.py` 가 지킨다) **집계와 분류**다:
빈도로 마켓태그를 갈라내되 자동 배제하지 않는다는 성질, 그리고 LLM 을 안 부른다는 성질.

실행:  python -m eval.test_product_registry   (repo 루트에서)
"""
from __future__ import annotations
import dataclasses
import json
import tempfile
from pathlib import Path

from slime_rag import config, pipeline, rawstore
from slime_rag.llm_ops import summary

KIND = "ig_profile_feed"
HANDLE = "slime_gina_"          # KB 실재 마켓(슬라임지나, market_word=지나, aliases=슬지나/쿨라임)
MARKET = "지나"


def _isolated(tmp: Path):
    """`settings` 를 임시 디렉터리로 갈아끼운다.

    `pipeline`·`rawstore` 가 `from .config import settings` 로 **값을 잡아 두므로**
    두 모듈의 이름을 다 바꿔야 한다 — 한쪽만 바꾸면 테스트가 실제 `data/` 를 건드린다.
    """
    patched = dataclasses.replace(config.settings, raw_dir=tmp / "raw",
                                  product_registry_path=tmp / "product_registry.json")
    for mod in (pipeline, rawstore):
        mod.settings = patched
    return patched


def _post(code: str, caption: str, ts: str = "2026-01-01T00:00:00Z") -> dict:
    return {"shortCode": code, "caption": caption, "timestamp": ts,
            "url": f"https://www.instagram.com/p/{code}/"}


def _seed(tmp: Path, posts: list[dict]) -> None:
    rawstore.save_run(posts, actor="apify/instagram-scraper", kind=KIND, key=HANDLE,
                      root=tmp / "raw")


def test_market_and_generic_tags_excluded():
    """마켓 자기이름(`#슬라임지나`·`#쿨라임`)과 광역어(`#슬라임`)는 제품이 아니다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post("A", "신상이에요 #슬라임지나 #쿨라임 #슬라임 #슬라임추천 #빠코볼")])
    pipeline.derive_product_registry(markets=[MARKET])
    doc = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))
    names = [p["name"] for p in doc["markets"][MARKET]["products"]]
    assert names == ["빠코볼"], f"마켓명/광역어가 새어 들어왔다: {names}"
    print("✓ 마켓명·광역어 제외 OK")


def test_high_coverage_tag_is_split_out_not_deleted():
    """개인 태그(`#꼼픽`)는 **분리**되되 사라지지 않는다 — 승격은 사람 몫.

    이게 이 함수의 존재 이유다: 해시태그 규칙만으로는 `#꼼픽`(개인 태그)과 `#빠코볼`
    (제품)을 가를 수 없다. 피드 전량이 있으면 빈도로 갈린다.
    """
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    posts = [_post(f"P{i}", f"오늘의 슬라임이에요 #꼼픽 #제품{i}") for i in range(10)]
    posts.append(_post("PX", "재입고예요 #꼼픽 #빠코볼"))
    posts.append(_post("PY", "빠코볼 재고 있어요 #꼼픽 #빠코볼"))
    _seed(tmp, posts)
    pipeline.derive_product_registry(markets=[MARKET])
    entry = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))["markets"][MARKET]
    prods = {p["name"] for p in entry["products"]}
    wide = {p["name"] for p in entry["market_tag_candidates"]}
    assert "꼼픽" in wide, f"전 게시물 태그가 제품으로 남았다: {wide}"
    assert "꼼픽" not in prods, "마켓태그 후보가 제품 목록에도 들어 있다"
    assert "빠코볼" in prods, f"진짜 제품이 후보에서 빠졌다: {prods}"
    assert entry["n_posts"] == 12
    cov = next(p for p in entry["market_tag_candidates"] if p["name"] == "꼼픽")["coverage"]
    assert cov == 1.0, f"커버리지 계산이 틀림: {cov}"
    print("✓ 고빈도 개인태그 분리(삭제 아님) OK")


def test_small_feed_never_classifies_market_tags():
    """게시물이 적으면 비율이 근거가 못 된다 — 3건 중 2건은 0.67 이어도 아무 뜻이 없다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post("A", "신상 #빠코볼"), _post("B", "재입고 #빠코볼"), _post("C", "품절 #버블")])
    pipeline.derive_product_registry(markets=[MARKET])
    entry = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))["markets"][MARKET]
    assert entry["market_tag_candidates"] == [], \
        f"표본 {entry['n_posts']}건에서 마켓태그를 단정했다: {entry['market_tag_candidates']}"
    assert {p["name"] for p in entry["products"]} == {"빠코볼", "버블"}
    print("✓ 소표본 마켓태그 단정 금지 OK")


def test_counts_are_per_post_not_per_tag_occurrence():
    """한 게시물이 같은 태그를 두 번 달아도 1건 — 아니면 커버리지가 1을 넘는다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post("A", "#빠코볼 재입고 #빠코볼 진짜 #빠코볼")])
    pipeline.derive_product_registry(markets=[MARKET])
    entry = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))["markets"][MARKET]
    rec = entry["products"][0]
    assert rec["n_posts"] == 1 and rec["coverage"] == 1.0, f"중복 계수: {rec}"
    print("✓ 게시물 단위 계수(태그 등장 횟수 아님) OK")


def test_date_range_and_permalinks_recorded():
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post("A", "#빠코볼 첫 출시예요", "2026-01-01T00:00:00Z"),
                _post("B", "#빠코볼 재입고예요", "2026-06-01T00:00:00Z")])
    pipeline.derive_product_registry(markets=[MARKET])
    rec = json.loads((tmp / "product_registry.json").read_text(
        encoding="utf-8"))["markets"][MARKET]["products"][0]
    assert rec["first_seen"] == "2026-01-01T00:00:00Z"
    assert rec["last_seen"] == "2026-06-01T00:00:00Z"
    assert rec["permalinks"] and all(u.startswith("https://") for u in rec["permalinks"])
    print("✓ 날짜 범위·permalink 기록 OK")


def test_no_caption_text_in_output():
    """산출물에 캡션 본문이 없어야 커밋 가능하다(ADR-0013)."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    secret = "이건절대저장되면안되는캡션본문이에요"
    _seed(tmp, [_post("A", f"{secret} #빠코볼")])
    pipeline.derive_product_registry(markets=[MARKET])
    blob = (tmp / "product_registry.json").read_text(encoding="utf-8")
    assert secret not in blob, "캡션 본문이 레지스트리로 샜다 — 재배포 규칙 위반"
    print("✓ 캡션 본문 미유출 OK")


def test_type_words_are_split_out_regardless_of_frequency():
    """KB `slime_types` 표면형은 **빈도와 무관하게** 제품이 아니다.

    회귀 근거(2026-08-09 실측 9마켓 1462게시물): 진통제·베이퍼는 제품명이 아니라 **분류**로
    태그를 단다(`#크런치슬라임`·`#디폼슬라임`). 커버리지 기준은 상위만 잡고 `#지글리`·
    `#빨대슬라임` 처럼 드문 종류단어는 놓친다 — 표본이 아니라 **어휘**의 문제라 빈도로는
    끝까지 안 갈린다. 그래서 KB 가 이미 가진 `slime_types` 를 따로 쓴다.
    """
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post("A", "오늘 신상이에요 #지글리 #빠코볼"),          # 저빈도 종류단어
                _post("B", "재입고예요 #디폼슬라임 #촉감류 #버블")])
    pipeline.derive_product_registry(markets=[MARKET])
    entry = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))["markets"][MARKET]
    types = {p["name"] for p in entry["type_tag_candidates"]}
    prods = {p["name"] for p in entry["products"]}
    assert {"지글리", "디폼슬라임", "촉감류"} <= types, f"종류단어가 제품으로 남았다: {prods}"
    assert prods == {"빠코볼", "버블"}, f"진짜 제품이 흔들렸다: {prods}"
    assert "지글리" not in pipeline.load_product_registry()[MARKET]
    print("✓ 종류단어 분리(빈도 무관) OK")


def test_single_occurrence_is_not_treated_as_noise():
    """`n_posts==1` 은 정상이다 — 이 도메인은 제품 하나에 게시물 하나가 기본이다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post(f"P{i}", f"신상이에요 #제품{i}") for i in range(30)])
    pipeline.derive_product_registry(markets=[MARKET])
    entry = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))["markets"][MARKET]
    assert len(entry["products"]) == 30, f"1회 등장 제품이 걸러졌다: {len(entry['products'])}"
    assert all(p["n_posts"] == 1 for p in entry["products"])
    print("✓ 1회 등장 제품 보존 OK")


def test_zero_llm_calls():
    """무과금 파생물이라는 주장을 코드로 강제한다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    _seed(tmp, [_post("A", "#빠코볼 신상이에요")])
    before = summary()["calls"]
    pipeline.derive_product_registry(markets=[MARKET])
    assert summary()["calls"] == before, "LLM 을 불렀다 — 이 경로는 무과금이어야 한다"
    print("✓ LLM 호출 0회 OK")


def test_load_registry_excludes_market_tag_candidates():
    """소비 API 는 제품만 준다 — 마켓태그 후보가 섞이면 유령 제품이 되살아난다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    posts = [_post(f"P{i}", f"오늘 #꼼픽 #제품{i}") for i in range(10)]
    _seed(tmp, posts)
    pipeline.derive_product_registry(markets=[MARKET])
    known = pipeline.load_product_registry()
    assert "꼼픽" not in known[MARKET], "마켓태그 후보가 known_products 로 샜다"
    assert "제품0" in known[MARKET]
    print("✓ load_product_registry 마켓태그 배제 OK")


def test_missing_registry_is_empty_not_fatal():
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    assert pipeline.load_product_registry() == {}
    print("✓ 레지스트리 부재 회복력 OK")


def test_no_raw_data_yields_empty_market_not_crash():
    """원문을 아직 안 받은 마켓도 예외 없이 빈 항목으로 나와야 한다."""
    tmp = Path(tempfile.mkdtemp(prefix="registry-"))
    _isolated(tmp)
    out = pipeline.derive_product_registry(markets=[MARKET])
    entry = json.loads((tmp / "product_registry.json").read_text(encoding="utf-8"))["markets"][MARKET]
    assert entry["n_posts"] == 0 and entry["products"] == []
    assert out["product_candidates"] == 0
    print("✓ 원문 없는 마켓 회복력 OK")


if __name__ == "__main__":
    test_market_and_generic_tags_excluded()
    test_high_coverage_tag_is_split_out_not_deleted()
    test_small_feed_never_classifies_market_tags()
    test_counts_are_per_post_not_per_tag_occurrence()
    test_date_range_and_permalinks_recorded()
    test_no_caption_text_in_output()
    test_type_words_are_split_out_regardless_of_frequency()
    test_single_occurrence_is_not_treated_as_noise()
    test_zero_llm_calls()
    test_load_registry_excludes_market_tag_candidates()
    test_missing_registry_is_empty_not_fatal()
    test_no_raw_data_yields_empty_market_not_crash()
    print("\n모든 오프라인 테스트 통과 ✅")
