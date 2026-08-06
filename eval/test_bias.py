# -*- coding: utf-8 -*-
"""
편향 태깅 오프라인 테스트 — API/DB/LLM 미호출·무비용.

검증:
  - detect_promo: 대가·무상 제공 구문 → True, '광고 아님'/일반 캡션 → False.
  - seller_index / classify: KB 핸들 일치 → seller(market), 일반 유저 → promo|None.
  - partition: 혼합 입력 → sellers/users 정확 분리 + review_class 태깅.
  - consolidated: 홍보성이 headline(by_source/gap/summary)에 안 섞이고 promo_view 로 분리.
  - LAYER1_SCHEMA: OpenAI strict 구조 규약(additionalProperties:false + 전체 required).

실행:  python -m eval.test_bias   (repo 루트에서)
"""
from __future__ import annotations

from slime_rag import bias
from slime_rag.sources import RawReview
from slime_rag.linking import load_kb
from slime_rag.consolidated_view import build_consolidated
from slime_rag.extract import LAYER1_SCHEMA


def test_detect_promo_seed():
    assert bias.detect_promo("이 제품은 서포터즈 게시물입니다") == (True, "서포터즈")
    assert bias.detect_promo("영상 촬영 목적을 위해 무상으로 제공받았습니다")[0] is True
    assert bias.detect_promo("협찬 받은 슬라임")[0] is True
    assert bias.detect_promo("PPL 포함된 영상")[0] is True          # 대소문자 무관
    # 부정문 오탐 금지: '광고' 단독은 seed 에 없음
    assert bias.detect_promo("이 슬라임 광고 아님 내돈내산") == (False, None)
    assert bias.detect_promo("그냥 사서 써본 후기입니다") == (False, None)
    print("✓ detect_promo seed/부정문 OK")


def test_llm_fallback_hook():
    # seed 미매칭이어도 classify_fn 이 True 면 홍보성으로(정밀도 훅).
    hit = bias.detect_promo("특별한 문구 없음", classify_fn=lambda t: True)
    assert hit == (True, "llm")
    miss = bias.detect_promo("특별한 문구 없음", classify_fn=lambda t: False)
    assert miss == (False, None)
    print("✓ LLM 폴백 훅 OK")


def test_promo_markers_config_load():
    markers = bias.load_promo_markers()               # data/promo_markers.json 있으면 그걸로
    assert "서포터즈" in markers and "협찬" in markers
    assert "광고" not in markers                       # 단독 '광고'는 금지
    print(f"✓ promo_markers 로드 OK ({len(markers)}개)")


def test_seller_index_and_classify():
    kb = load_kb()
    sellers = bias.seller_index(kb)
    assert sellers.get("from.murmurslime") == "머머"
    assert sellers.get("murmurslime") == "머머"        # handles_alt 도 등록
    assert sellers.get("vinzzang_slime") == "빈짱"

    seller_raw = RawReview(text="신상 향료 라벤더 입고", url="s", platform="instagram",
                           meta={"owner_username": "FROM.murmurslime"})  # 대소문자 무관
    v = bias.classify(seller_raw, sellers)
    assert v.category == "seller" and v.market == "머머", v

    user_raw = RawReview(text="서포터즈로 받았어요 말랑좋음", url="u", platform="instagram",
                         meta={"owner_username": "random_kid"})
    v2 = bias.classify(user_raw, sellers)
    assert v2.category == "promo" and v2.marker == "서포터즈", v2

    plain = RawReview(text="내돈내산 향 좋고 쫀득", url="p", platform="instagram",
                      meta={"owner_username": "someone"})
    assert bias.classify(plain, sellers).category is None
    print("✓ seller_index / classify(우선순위 판매자>홍보성) OK")


def test_seller_priority_over_promo():
    """판매자가 협찬 문구를 써도 판매자 글(1층 라우팅 우선)."""
    kb = load_kb()
    sellers = bias.seller_index(kb)
    raw = RawReview(text="협찬 이벤트 진행중! 신상 출시", url="x", platform="instagram",
                    meta={"owner_username": "from.murmurslime"})
    v = bias.classify(raw, sellers)
    assert v.category == "seller", v                   # promo 아님
    print("✓ 우선순위 판매자>홍보성 OK")


def test_partition():
    kb = load_kb()
    raws = [
        RawReview(text="머머 신상 향료 라벤더", url="u1", platform="instagram",
                  meta={"owner_username": "from.murmurslime"}),
        RawReview(text="서포터즈로 받은 슬라임 말랑좋음", url="u2", platform="instagram",
                  meta={"owner_username": "user_a"}),
        RawReview(text="내돈내산 후기 향 좋고 쫀득해요", url="u3", platform="instagram",
                  meta={"owner_username": "user_b"}),
    ]
    sellers, users = bias.partition(raws, kb)
    assert len(sellers) == 1 and sellers[0].meta["seller_market"] == "머머"
    assert len(users) == 2
    by_url = {r.url: r.meta["review_class"] for r in users}
    assert by_url == {"u2": "promo", "u3": "genuine"}, by_url
    # 판매자 글엔 review_class 를 달지 않는다(후기 아님)
    assert "review_class" not in sellers[0].meta
    print("✓ partition 분리·태깅 OK")


class _StubLLM:
    """llm_ops.LLM 대역 — 정해진 dict 를 반환(무비용). complete 호출을 기록."""
    def __init__(self, resp, raises=False):
        self.resp = resp
        self.raises = raises
        self.calls: list = []

    def complete(self, prompt, **kw):
        self.calls.append((prompt, kw))
        if self.raises:
            raise RuntimeError("stub API 실패")
        return self.resp


def test_promo_verdict_plumbing():
    """promo_verdict 가 스키마 강제 호출 + 응답 매핑(is_sponsored→marker)을 하는지(무비용)."""
    yes = _StubLLM({"is_sponsored": True, "kind": "supporter", "evidence": "서포터"})
    assert bias.promo_verdict("서포터 게시물입니다", yes) == (True, "supporter")
    assert yes.calls[0][1].get("schema") is bias.PROMO_LLM_SCHEMA   # strict 스키마 전달
    assert yes.calls[0][1].get("label") == "bias.promo"

    no = _StubLLM({"is_sponsored": False, "kind": None, "evidence": None})
    assert bias.promo_verdict("내돈내산 후기", no) == (False, None)

    # kind 가 비어도 is_sponsored 면 마커는 'sponsored' 로 폴백
    yk = _StubLLM({"is_sponsored": True, "kind": None, "evidence": "협찬"})
    assert bias.promo_verdict("협찬받음", yk) == (True, "sponsored")
    print("✓ promo_verdict 플러밍(스키마·매핑) OK")


def test_llm_detector_is_primary_over_keyword():
    """LLM detector 를 주면 키워드를 건너뛰고 detector 가 전권 — '인용' 오탐 방지의 핵심."""
    kb = load_kb()
    sellers = bias.seller_index(kb)
    # 캡션에 '서포터'가 있지만 인용문 → LLM 은 genuine 판정. detector 가 이겨야 한다.
    quote = RawReview(text="서포터분들은 얄랑하다 했지만 제가 만졌을 땐 매트했어요",
                      url="q", platform="instagram", meta={"owner_username": "user_x"})
    genuine_detector = bias.make_llm_promo_detector(
        _StubLLM({"is_sponsored": False, "kind": None, "evidence": None}))
    v = bias.classify(quote, sellers, promo_detector=genuine_detector)
    assert v.category is None, v                    # 키워드였다면 '서포터'로 promo 오탐했을 것
    print("✓ LLM detector 우선(키워드 오탐 차단) OK")


def test_llm_detector_falls_back_on_error():
    """LLM 호출 실패 시 예외 없이 키워드 seed 로 폴백(수집 회복력)."""
    broken = bias.make_llm_promo_detector(_StubLLM(None, raises=True))
    assert broken("서포터즈 게시물입니다") == (True, "서포터즈")   # 키워드 폴백이 잡음
    assert broken("그냥 내돈내산 후기") == (False, None)
    print("✓ LLM 실패 시 키워드 폴백 OK")


def test_partition_with_llm_detector():
    """partition 이 promo_detector 를 태그 확정에 사용."""
    kb = load_kb()
    raws = [
        RawReview(text="머머 신상 향료 라벤더", url="s", platform="instagram",
                  meta={"owner_username": "from.murmurslime"}),
        RawReview(text="서포터분들 말론 얄랑, 근데 난 매트했음", url="u1", platform="instagram",
                  meta={"owner_username": "user_a"}),
    ]
    genuine_detector = bias.make_llm_promo_detector(
        _StubLLM({"is_sponsored": False, "kind": None, "evidence": None}))
    sellers, users = bias.partition(raws, kb, promo_detector=genuine_detector)
    assert len(sellers) == 1 and len(users) == 1
    assert users[0].meta["review_class"] == "genuine"   # 인용문 오탐 안 됨
    print("✓ partition + LLM detector OK")


# ---------------------------------------------------------------- 홍보성 게이트(캐스케이드)
def test_promo_gate_recall():
    """게이트는 recall 전용 — 모든 홍보/애매어 캡션을 통과시켜 LLM 판정 기회를 준다."""
    for cap in ["서포터 게시물입니다", "서포터즈 게시물입니다",
                "무상으로 제공받았습니다", "체험단으로 받은 슬라임", "협찬받아 올려요",
                "체험 이벤트 증정 나눔", "당첨되어 받았어요"]:
        assert bias.promo_gate(cap) is True, cap
    # 인용문도 게이트는 통과(precision 은 LLM 몫) — '서포터'가 있으므로 True
    assert bias.promo_gate("서포터분들은 얄랑하다 했지만 제가 만졌을 땐 매트했어요") is True
    print("✓ promo_gate recall(홍보/애매어/인용문 통과) OK")


def test_promo_gate_excludes_purchase_terms():
    """D7 — 순수 구매어(비매·서비스·할인·세일)만 있는 글은 게이트 단락(LLM 미대상)."""
    for cap in ["저번 차수 비매때부터 갈망이었는데 이번에 산 건데",
                "3개 사면 서비스로 하나 더 줌", "추가 할인이라 산 건데", "세일해서 샀어요"]:
        assert bias.promo_gate(cap) is False, cap
    # seed ⊆ gate 이지만 순수 구매어는 둘 다에 없음(직접 증거)
    assert set(bias.PROMO_SEED) <= set(bias.GATE_LEXICON)
    for w in ["비매", "서비스", "할인", "세일"]:
        assert w not in bias.GATE_LEXICON and w not in bias.PROMO_SEED, w
    print("✓ promo_gate 순수 구매어 제외(D7) OK")


def test_promo_gate_skips_plain():
    """순수 실사용 캡션은 게이트 단락 → LLM 미호출."""
    assert bias.promo_gate("말랑하고 향 좋아요 재구매각") is False
    assert bias.promo_gate("") is False
    assert bias.promo_gate("ㅁㅁ") is False           # 초성단독
    print("✓ promo_gate 순수 실사용 단락 OK")


def test_gated_detector_shortcircuits():
    """게이트 단락 시 stub LLM 호출 0건(단락 증거), 통과 시 정확히 1건."""
    stub = _StubLLM({"is_sponsored": False, "kind": None, "evidence": None})
    detect = bias.make_gated_llm_promo_detector(stub, terms=bias.GATE_LEXICON)
    assert detect("말랑 향 좋아요 재구매각") == (False, None)
    assert stub.calls == [], "게이트 단락인데 LLM 호출됨"
    detect("서포터 게시물입니다")
    assert len(stub.calls) == 1, stub.calls
    print("✓ gated detector 단락(LLM 미호출)/통과(1호출) OK")


def test_gated_detector_precision_preserved():
    """게이트 통과분을 LLM 이 판정 — 인용문은 genuine(precision 보존)."""
    stub = _StubLLM({"is_sponsored": False, "kind": None, "evidence": None})
    detect = bias.make_gated_llm_promo_detector(stub, terms=bias.GATE_LEXICON)
    # '서포터' 로 게이트 통과 → LLM 이 인용문을 genuine 판정 → 오탐 안 됨
    assert detect("서포터분들은 얄랑하다 했지만 제가 만졌을 땐 매트했어요") == (False, None)
    assert len(stub.calls) == 1                       # 게이트는 통과시켰다(LLM 이 정밀 판정)
    # 진짜 홍보는 통과 + LLM promo → (True, marker)
    promo = _StubLLM({"is_sponsored": True, "kind": "supporter", "evidence": "서포터"})
    d2 = bias.make_gated_llm_promo_detector(promo, terms=bias.GATE_LEXICON)
    assert d2("서포터 게시물입니다") == (True, "supporter")
    print("✓ gated detector precision 보존(인용문 genuine) OK")


def test_gated_detector_on_label_hook():
    """on_label 훅(D6): 게이트 통과분만 (text, is_promo, marker) 로 흘린다(ML distillation 준비)."""
    labels: list = []
    promo = _StubLLM({"is_sponsored": True, "kind": "supporter", "evidence": "서포터"})
    detect = bias.make_gated_llm_promo_detector(
        promo, terms=bias.GATE_LEXICON,
        on_label=lambda t, p, m: labels.append((t, p, m)))
    detect("말랑 향 좋아요")                            # 단락 → 라벨 없음
    detect("서포터 게시물입니다")                        # 통과 → 라벨 1건
    assert labels == [("서포터 게시물입니다", True, "supporter")], labels
    print("✓ gated detector on_label 훅(통과분만) OK")


def test_gate_terms_config_load():
    """data/promo_gate_terms.json 로드 — 서포터·체험 포함, 순수 구매어 미포함, '_' 무시."""
    terms = bias.load_gate_terms()
    assert "서포터" in terms and "체험" in terms
    for w in ["비매", "서비스", "할인", "세일"]:
        assert w not in terms, w
    assert set(bias.PROMO_SEED) <= set(terms)          # 파일도 superset 유지
    print(f"✓ promo_gate_terms 로드 OK ({len(terms)}개)")


def _rec(platform, sent, review_class="genuine", **blocks):
    r = {"source": {"platform": platform}, "overall": {"model_sentiment": sent},
         "review_class": review_class}
    r.update(blocks)
    return r


def test_consolidated_splits_promo():
    reviews = [
        _rec("instagram", "pos", scent={"perceived": "라벤더", "sentiment": "pos"}),
        _rec("dcinside", "neg", scent={"perceived": "비누향", "sentiment": "neg"}),
        # 홍보성 3건(전부 pos) — headline 에 섞이면 net 이 크게 왜곡됨
        _rec("instagram", "pos", "promo"),
        _rec("instagram", "pos", "promo"),
        _rec("instagram", "pos", "promo"),
    ]
    v = build_consolidated({"market": "머머", "product": "테스트"}, None, reviews)
    # headline 은 실사용 2건만
    assert v["n_reviews"] == 2, v["n_reviews"]
    ig = v["by_source"]["instagram"]
    assert ig["n"] == 1 and ig["net"] == 1.0, ig       # 홍보성 3건이 IG net 을 안 부풀림
    # 서포터(홍보성) 분리 버킷 — 향/질감/장단점 섹션 형태(LLM 미주입이라 내용은 비어있음).
    assert v["promo_view"] is not None
    assert v["promo_view"]["n_promo"] == 3, v["promo_view"]
    assert set(v["promo_view"]) >= {"n_promo", "scent", "texture", "pros", "cons"}, v["promo_view"]
    print("✓ consolidated: 서포터 분리(headline 미오염, promo_view.n_promo=3) OK")


def test_consolidated_no_promo_regression():
    """홍보성 없으면 promo_view=None (기존 데이터와 출력 동일 — 회귀 없음)."""
    reviews = [
        _rec("instagram", "pos"),
        _rec("dcinside", "neg"),
    ]
    v = build_consolidated({"market": "빈짱", "product": "x"}, None, reviews)
    assert v["promo_view"] is None
    assert v["n_reviews"] == 2
    print("✓ 홍보성 없을 때 promo_view=None (회귀 없음) OK")


def _assert_strict(node, path="root"):
    """OpenAI strict json_schema 규약 재귀 검증: object 는 additionalProperties:false + 전체 required."""
    if not isinstance(node, dict):
        return
    for sub in node.get("anyOf", []):
        _assert_strict(sub, path + ".anyOf")
    if node.get("type") == "object":
        assert node.get("additionalProperties") is False, f"{path}: additionalProperties!=false"
        props = node.get("properties", {})
        assert set(node.get("required", [])) == set(props), f"{path}: required != properties"
        for k, v in props.items():
            _assert_strict(v, f"{path}.{k}")
    if node.get("type") == "array":
        _assert_strict(node.get("items", {}), path + "[]")


def test_layer1_schema_strict():
    _assert_strict(LAYER1_SCHEMA)
    # 스펙 항목 필드가 실제로 존재하는지(계약 회귀 방지)
    item = LAYER1_SCHEMA["properties"]["products"]["items"]
    assert set(item["properties"]) == {"product", "scent", "base_combo", "slime_type",
                                       "official_texture", "beads", "evidence"}
    # beads = 오픈 어휘 문자열 배열(제품 구성요소, 없으면 [])
    assert item["properties"]["beads"]["type"] == "array"
    assert item["properties"]["beads"]["items"]["type"] == "string"
    # official_texture = 판매자가 쓴 질감 서술 요약. slime_type(통제어휘 분류)과 **별개 칸**이다 —
    # 합치자는 리팩터가 오면 화면의 '질감' 줄이 다시 분류코드만 보여주는 상태로 되돌아간다.
    assert item["properties"]["official_texture"]["type"] == ["string", "null"]
    assert "enum" not in item["properties"]["official_texture"]
    print("✓ LAYER1_SCHEMA strict 구조 OK (beads·official_texture 포함)")


if __name__ == "__main__":
    test_detect_promo_seed()
    test_llm_fallback_hook()
    test_promo_markers_config_load()
    test_seller_index_and_classify()
    test_seller_priority_over_promo()
    test_partition()
    test_promo_verdict_plumbing()
    test_llm_detector_is_primary_over_keyword()
    test_llm_detector_falls_back_on_error()
    test_partition_with_llm_detector()
    test_promo_gate_recall()
    test_promo_gate_excludes_purchase_terms()
    test_promo_gate_skips_plain()
    test_gated_detector_shortcircuits()
    test_gated_detector_precision_preserved()
    test_gated_detector_on_label_hook()
    test_gate_terms_config_load()
    test_consolidated_splits_promo()
    test_consolidated_no_promo_regression()
    test_layer1_schema_strict()
    print("\n모든 bias 오프라인 테스트 통과 ✅")
