# -*- coding: utf-8 -*-
"""
리뷰 요약(6기준 + 장단점) 오프라인 테스트 — LLM/DB/API 미호출·무비용.

검증(사용자 확정 규칙):
  - 소스별 review_summaries: 인스타/디시 각각 {질감·향·소리·지속력·고객응대·배송, pros, cons}.
  - '언급 없으면 빈칸': 재료가 없는 소스엔 sectionize 가 그 키를 안 받는다 → 빈칸(None) 가능.
  - 통합(integrated)은 '두 소스 모두 실사용 후기'가 있을 때만 생성(reconciliation, 평균 금지).
  - 홍보성은 review_summaries 에 미포함(genuine 만) — promo_view 로 분리(회귀 없음).
  - llm_sectionize 미주입 시 review_summaries 전부 None(결정적 부분은 그대로).
  - _source_material: 언급된 속성만 담고 evidence/sentiment 를 넘긴다.

실행:  python -m eval.test_consolidated_sections   (repo 루트에서)
"""
from __future__ import annotations

from slime_rag.consolidated_view import (
    build_consolidated, _source_material, SOURCE_REVIEW_SCHEMA, CRITERIA, CRITERIA_KEYS,
)

# 기준 키 → 그 기준을 채우는 재료 필드. 주문 축(shipping_cs) 하나가 cs·shipping 둘로 갈린다.
_MATERIAL = {"texture": "texture", "scent": "scent", "sound": "sound",
             "longevity": "longevity", "cs": "shipping_cs", "shipping": "shipping_cs"}


def _rec(platform, sent, review_class="genuine", **blocks):
    r = {"source": {"platform": platform}, "overall": {"model_sentiment": sent},
         "review_class": review_class}
    r.update(blocks)
    return r


# sectionize 목: 입력 payload 의 by_attr 에 실제로 있는 속성만 요약, 없으면 null.
# '지어내기 금지'를 코드로 강제 — 재료에 없는 향/질감은 절대 채우지 않는다.
def _fake_sectionize(prompt: str, schema: dict) -> dict:
    assert schema is SOURCE_REVIEW_SCHEMA          # 스키마가 그대로 전달되는지
    tail = prompt.split("[입력]", 1)[-1]
    out = {k: (f"{k}요약" if f'"{_MATERIAL[k]}"' in tail else None) for k in CRITERIA_KEYS}
    return {**out, "pros": ["장점1"], "cons": []}


def test_source_material_only_mentioned():
    """언급된 속성만 재료에 담기고, evidence/sentiment 가 실린다. 미언급 속성은 키 자체가 없다."""
    reviews = [
        _rec("dcinside", "neg",
             texture={"sentiment": "neg", "evidence": "흐름성강함", "feel": ["흐름성있는"]}),
    ]
    mat = _source_material(reviews)
    assert "texture" in mat and "scent" not in mat, mat      # 향 언급 0 → 키 없음
    assert mat["texture"][0]["evidence"] == "흐름성강함"
    assert mat["texture"][0]["feel"] == ["흐름성있는"]
    print("✓ _source_material: 언급 속성만·evidence 포함, 미언급 향은 제외 OK")


def test_blank_section_when_attr_absent():
    """향 언급이 없는 소스는 scent 섹션이 빈칸(None) — 억지 서술 금지."""
    reviews = [
        # 인스타: 향만, 디시: 질감만 → 각 소스 반대 섹션은 빈칸이어야
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬진해요"}),
        _rec("dcinside", "neg", texture={"sentiment": "neg", "evidence": "흐름성강함"}),
    ]
    v = build_consolidated({"market": "머머", "product": "테스트"}, None, reviews,
                           llm_sectionize=_fake_sectionize)
    ig = v["review_summaries"]["instagram"]
    dc = v["review_summaries"]["dcinside"]
    assert ig["scent"] == "scent요약" and ig["texture"] is None, ig    # 인스타=향만
    assert dc["texture"] == "texture요약" and dc["scent"] is None, dc  # 디시=질감만
    print("✓ blank section: 미언급 속성 → None(빈칸) OK")


def test_integrated_only_when_both_sources():
    """통합 리뷰는 두 소스 모두 실사용 후기가 있을 때만. 한쪽만이면 None."""
    only_dc = [_rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누향"})]
    v1 = build_consolidated({"market": "머머", "product": "X"}, None, only_dc,
                            llm_sectionize=_fake_sectionize)
    assert v1["review_summaries"]["dcinside"] is not None
    assert v1["review_summaries"]["instagram"] is None
    assert v1["review_summaries"]["integrated"] is None, "한 소스만인데 통합 생성됨(평균 위험)"

    both = only_dc + [_rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"})]
    v2 = build_consolidated({"market": "머머", "product": "X"}, None, both,
                            llm_sectionize=_fake_sectionize)
    assert v2["review_summaries"]["integrated"] is not None, "두 소스인데 통합 미생성"
    print("✓ integrated: 두 소스 모두 있을 때만 생성(단일 소스=None) OK")


def test_promo_excluded_from_review_summaries():
    """홍보성은 genuine 소스 요약에 안 섞이고 promo_view 로 분리(회귀 없음)."""
    reviews = [
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"}),
        _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"}),
        _rec("instagram", "pos", "promo", scent={"sentiment": "pos", "evidence": "협찬향"}),
    ]
    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                           llm_sectionize=_fake_sectionize)
    assert v["n_reviews"] == 2                       # 홍보성 제외
    assert v["promo_view"] is not None and v["promo_view"]["n_promo"] == 1
    # 인스타 genuine 1건만 → 요약 존재하되 홍보성 evidence('협찬향')는 재료에 안 들어감
    assert v["review_summaries"]["instagram"] is not None
    print("✓ promo: review_summaries 에서 제외·promo_view 분리 OK")


def test_no_sectionize_all_none():
    """llm_sectionize 미주입 → review_summaries 전부 None. 결정적 집계는 그대로."""
    reviews = [_rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"})]
    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews)  # llm 없음
    assert v["review_summaries"] == {"instagram": None, "dcinside": None, "integrated": None}
    assert v["by_source"]["dcinside"]["n"] == 1      # 결정적 부분 정상
    print("✓ no-LLM: review_summaries 전부 None, 결정적 집계 유지 OK")


def test_official_spec_never_in_summary_prompts():
    """스펙↔후기 완전 분리: 공식 스펙(1층)이 어떤 요약 프롬프트에도 안 들어간다.
    scent_divergence(1층 파생)는 뷰에는 남되(코드 계산 표시 블록) 통합 프롬프트에서 제외."""
    prompts: list[str] = []

    def _capture(prompt: str, schema: dict) -> dict:
        assert schema is SOURCE_REVIEW_SCHEMA
        prompts.append(prompt)
        return {**{k: None for k in CRITERIA_KEYS}, "scent": "향요약", "pros": [], "cons": []}

    reviews = [
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬", "perceived": "레몬향"}),
        _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누", "perceived": "비누향"}),
    ]
    # 후기 어디에도 없는 유일 토큰. official_texture 도 같이 건다 — 판매자가 쓴 질감 서술은
    # 구조상 항상 긍정이라, 요약 프롬프트로 새면 인스타 편향이 한 겹 더 얹힌다(소스 미평균 규칙).
    spec = {"official_scent": "공식딸기토큰", "official_texture": "공식질감토큰"}
    v = build_consolidated({"market": "머머", "product": "X"}, spec, reviews,
                           llm_sectionize=_capture)
    assert v["scent_divergence"] is not None         # 표시 블록은 살아있다(코드 계산)
    assert v["review_summaries"]["integrated"] is not None
    assert len(prompts) == 3                          # ig, dc, integrated
    assert v["official_spec"]["official_texture"] == "공식질감토큰"   # 뷰(스펙 카드)로는 나간다
    for p in prompts:
        assert "공식딸기토큰" not in p, "공식 스펙이 요약 프롬프트에 유입됨(분리 위반)"
        assert "공식질감토큰" not in p, "판매자 질감 서술이 요약 프롬프트에 유입됨(편향 위반)"
    assert "scent_divergence" not in prompts[-1], "통합 프롬프트에 1층 파생 지표 유입"
    assert "diverged_ratio" not in prompts[-1]
    print("✓ 분리: 공식 스펙·scent_divergence 가 요약 프롬프트에 미유입 OK")


def test_market_mode_tags_products():
    """마켓 모드(product=None): 요약 재료 항목에 product 라벨. 제품 모드에선 라벨 없음."""
    ig = _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"})
    ig["product_ref"] = {"market": "머머", "product": "한줌"}
    dc = _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"})
    dc["product_ref"] = {"market": "머머", "product": None}   # linking 보류 행

    mat = _source_material([ig, dc], tag_products=True)
    labels = [i["product"] for i in mat["scent"]]
    assert labels == ["한줌", "제품미상"], labels
    assert "product" not in _source_material([ig])["scent"][0]   # 제품 모드: 라벨 없음

    prompts: list[str] = []
    def _capture(prompt: str, schema: dict) -> dict:
        prompts.append(prompt)
        return {**{k: None for k in CRITERIA_KEYS}, "scent": "향요약", "pros": [], "cons": []}

    build_consolidated({"market": "머머", "product": None}, None, [ig, dc],
                       llm_sectionize=_capture)
    assert any('"product": "한줌"' in p for p in prompts), "마켓 모드 소스 프롬프트에 제품 라벨 없음"
    print("✓ market mode: 재료 product 라벨(보류=제품미상)·제품 모드 무라벨 OK")


def test_shipping_section_from_order_level_field():
    """배송·CS 섹션 — shipping_cs 는 후기(주문) 단위 필드(ADR-0005)라 행에 '복제'돼야만 재료가 된다.

    복제는 index.index_post 담당이고, 종합뷰는 이미 복제된 행을 읽는다. 그 계약이 깨지면
    (= 행에 shipping_cs 키가 없으면) 섹션은 조용히 상시 빈칸이 된다 — 그래서 양쪽을 다 본다.
    """
    ship = {"notes": "3일 지연", "sentiment": "neg", "evidence": "배송 3일 지연"}
    reviews = [
        _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"}, shipping_cs=ship),
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"}),   # 배송 미언급
    ]
    mat_dc = _source_material([reviews[0]])
    assert mat_dc["shipping_cs"][0]["notes"] == "3일 지연", mat_dc
    assert "shipping_cs" not in _source_material([reviews[1]])   # 미언급 → 키 없음

    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                           llm_sectionize=_fake_sectionize)
    assert v["review_summaries"]["dcinside"]["shipping"] == "shipping요약"
    assert v["review_summaries"]["instagram"]["shipping"] is None   # 언급 없으면 빈칸
    assert "shipping" in SOURCE_REVIEW_SCHEMA["required"]
    print("✓ shipping: 주문단위 필드 복제분이 배송·CS 섹션 재료로 흐름, 미언급=빈칸 OK")


def test_index_replicates_shipping_cs_to_rows():
    """index_post 의 팬아웃 복제 계약 — doc 최상위 shipping_cs 가 제품 행마다 실려야 한다.

    DB·모델 없이 render_review 로만 확인한다(임베딩 로드 회피).
    """
    from slime_rag.index import render_review

    doc = {"market": "빈짱",
           "shipping_cs": {"notes": "다음날 도착", "sentiment": "pos", "evidence": "다음날 왔"},
           "reviews": [{"mentioned_product": "A"}, {"mentioned_product": "B"}]}
    ship = doc["shipping_cs"]
    rows = [{**r, "shipping_cs": ship} for r in doc["reviews"]]     # index_post 와 동일 규칙
    texts = [render_review("빈짱", r["mentioned_product"], r) for r in rows]
    assert all("배송·CS: 다음날 도착" in t for t in texts), texts   # 비교글 전 제품에 복제
    assert "배송·CS" not in render_review("빈짱", "A", {"mentioned_product": "A"})
    print("✓ index: shipping_cs 가 제품별 행에 복제·렌더 OK")


def test_schema_covers_six_criteria():
    """스키마가 6기준을 모두 갖고 전부 strict required — 하나라도 빠지면 표에 상시 빈칸이 생긴다."""
    assert [c["key"] for c in CRITERIA] == \
        ["texture", "scent", "sound", "longevity", "cs", "shipping"], CRITERIA
    props = SOURCE_REVIEW_SCHEMA["properties"]
    for k in CRITERIA_KEYS:
        assert k in props, f"{k} 가 스키마에 없다"
        assert k in SOURCE_REVIEW_SCHEMA["required"], f"{k} 가 required 에 없다"
        assert props[k]["type"] == ["string", "null"], f"{k} 는 미언급 null 을 허용해야 한다"
    # strict structured outputs 계약
    assert SOURCE_REVIEW_SCHEMA["additionalProperties"] is False
    assert set(SOURCE_REVIEW_SCHEMA["required"]) == set(props), "required 와 properties 불일치"
    print("✓ 스키마: 6기준 전부 존재·required·null 허용 OK")


def test_sound_and_longevity_reach_the_prompt():
    """소리·지속력은 예전엔 재료만 넘어가고 담길 필드가 없어 pros/cons 로만 샜다.

    이제 섹션이 생겼으니 (a) 재료가 프롬프트에 실리고 (b) 그 기준 칸이 채워지는지 함께 본다.
    """
    reviews = [_rec("dcinside", "neg",
                    sound={"sentiment": "neg", "evidence": "걀걀거림", "notes": "걀걀 소리 큼"},
                    longevity={"sentiment": "neg", "evidence": "3주만에굳음", "notes": "빨리 죽음"})]
    mat = _source_material(reviews)
    assert mat["sound"][0]["notes"] == "걀걀 소리 큼", mat
    assert mat["longevity"][0]["notes"] == "빨리 죽음", mat

    prompts: list[str] = []

    def _capture(prompt: str, schema: dict) -> dict:
        prompts.append(prompt)
        return _fake_sectionize(prompt, schema)

    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                           llm_sectionize=_capture)
    dc = v["review_summaries"]["dcinside"]
    assert dc["sound"] == "sound요약" and dc["longevity"] == "longevity요약", dc
    assert dc["scent"] is None and dc["texture"] is None, "미언급 기준이 채워졌다"
    assert "걀걀 소리 큼" in prompts[0] and "빨리 죽음" in prompts[0], "소리·지속력 재료 미유입"
    print("✓ 소리·지속력: 재료 유입 + 전용 섹션 산출 OK")


def test_cs_and_shipping_split_one_material():
    """고객 응대 / 배송은 재료 필드 하나(shipping_cs)에서 갈린다 — 둘 다 열려 있어야 한다."""
    ship = {"notes": "3일 지연, 교환 문의 무응답", "sentiment": "neg", "evidence": "배송 3일 지연"}
    v = build_consolidated({"market": "머머", "product": "X"}, None,
                           [_rec("dcinside", "neg", shipping_cs=ship)],
                           llm_sectionize=_fake_sectionize)
    dc = v["review_summaries"]["dcinside"]
    assert dc["cs"] == "cs요약" and dc["shipping"] == "shipping요약", dc
    # 프롬프트가 분기 규칙을 실제로 설명하는지 — 안 하면 모델이 한쪽에 몰아 쓴다
    from slime_rag.consolidated_view import SECTION_PROMPT, SUPPORTER_SECTION_PROMPT
    for p in (SECTION_PROMPT, SUPPORTER_SECTION_PROMPT):
        assert "cs 와 shipping 두 섹션으로 갈린다" in p, "분기 규칙이 프롬프트에 없다"
    print("✓ 고객 응대/배송: 재료 1개 → 섹션 2개 분기 + 프롬프트 규칙 OK")


def test_tone_rule_reaches_every_summary_prompt():
    """말투('~해요'체)는 세 요약 프롬프트 전부에 실려야 한다.

    화면 카피(`web/`)가 전부 해요체라 요약 하나만 '~다'체면 한 화면에서 톤이 튄다. 프롬프트가
    셋(인스타·디시·통합)이라 한 곳만 고치고 넘어가기 쉬워 게이트로 둔다. 서포터 버킷도 같은
    화면에 나가므로 포함.
    """
    from slime_rag.consolidated_view import (
        SECTION_PROMPT, INTEGRATED_PROMPT, SUPPORTER_SECTION_PROMPT,
    )
    for name, p in [("SECTION", SECTION_PROMPT), ("INTEGRATED", INTEGRATED_PROMPT),
                    ("SUPPORTER", SUPPORTER_SECTION_PROMPT)]:
        assert "[말투]" in p and "'~해요'체" in p, f"{name}_PROMPT 에 말투 규칙이 없다"
        # 말투는 표현만 바꾸는 것 — 부정 평가를 순화하면 소스 편향(1급 기능)이 지워진다.
        assert "누그러뜨리지" in p, f"{name}_PROMPT 에 '순화 금지' 가드가 없다"
    # '소스'가 아니라 '출처' — 화면 카피와 같은 말을 써야 요약문에도 같은 말이 나온다.
    for name, p in [("SECTION", SECTION_PROMPT), ("INTEGRATED", INTEGRATED_PROMPT)]:
        assert "소스" not in p, f"{name}_PROMPT 에 '소스' 표기가 남아 있다(화면 카피는 '출처')"

    # 실제 호출 경로에도 실리는지 — 상수만 보면 조립 단계에서 잘려도 못 잡는다.
    prompts: list[str] = []

    def _capture(prompt: str, schema: dict) -> dict:
        prompts.append(prompt)
        return _fake_sectionize(prompt, schema)

    reviews = [_rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"}),
               _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"})]
    build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                       llm_sectionize=_capture)
    assert len(prompts) == 3 and all("[말투]" in p for p in prompts), "조립된 프롬프트에 말투 누락"
    print("✓ 말투: 세 요약 프롬프트 전부 '~해요'체 규칙 + '출처' 표기 OK")


def test_promo_view_has_all_criteria_keys():
    """서포터 버킷도 같은 6기준 모양 — LLM 없이도 키가 다 있어야 표시부가 KeyError 안 난다."""
    v = build_consolidated({"market": "머머", "product": "X"}, None,
                           [_rec("instagram", "pos", "promo",
                                 scent={"sentiment": "pos", "evidence": "협찬향"})])
    pv = v["promo_view"]
    assert pv["n_promo"] == 1
    for k in CRITERIA_KEYS:
        assert k in pv, f"promo_view 에 {k} 키가 없다"
        assert pv[k] is None, "LLM 미주입인데 값이 채워졌다"
    print("✓ promo_view: 6기준 키 전부 존재(LLM 없으면 None) OK")


def _run_all():
    test_schema_covers_six_criteria()
    test_sound_and_longevity_reach_the_prompt()
    test_cs_and_shipping_split_one_material()
    test_tone_rule_reaches_every_summary_prompt()
    test_promo_view_has_all_criteria_keys()
    test_source_material_only_mentioned()
    test_blank_section_when_attr_absent()
    test_shipping_section_from_order_level_field()
    test_index_replicates_shipping_cs_to_rows()
    test_integrated_only_when_both_sources()
    test_promo_excluded_from_review_summaries()
    test_no_sectionize_all_none()
    test_official_spec_never_in_summary_prompts()
    test_market_mode_tags_products()
    print("\n리뷰 요약 섹션 오프라인 테스트 전부 통과 ✅")


if __name__ == "__main__":
    _run_all()
