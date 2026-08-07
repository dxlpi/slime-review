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
    build_consolidated, build_order_view, _source_material, _scrub_section, _violations,
    _fold_orders, criterion_stats, EMPTY_CRITERION,
    PRODUCT_REVIEW_SCHEMA, ORDER_REVIEW_SCHEMA, PRODUCT_AXIS, ORDER_AXIS,
    CRITERIA, CRITERIA_KEYS, CRITERION_MATERIAL, CRITERION_SCOPE,
    PRODUCT_KEYS, MARKET_KEYS,
)

# 기준 키 → 그 기준을 채우는 재료 필드. 주문 축(shipping_cs) 하나가 cs·shipping 둘로 갈린다.
# 백엔드 상수를 그대로 쓴다 — 테스트가 사본을 들면 둘이 조용히 갈라진다.
_MATERIAL = CRITERION_MATERIAL


def _verdict(section: dict, key: str):
    """기준 한 칸의 다수 의견. 미언급이면 None."""
    return (section or {}).get(key, {}).get("verdict")


def _rec(platform, sent, review_class="genuine", **blocks):
    r = {"source": {"platform": platform}, "overall": {"model_sentiment": sent},
         "review_class": review_class}
    r.update(blocks)
    return r


# sectionize 목: 입력 payload 의 by_attr 에 실제로 있는 속성만 요약, 없으면 null.
# '지어내기 금지'를 코드로 강제 — 재료에 없는 향/질감은 절대 채우지 않는다.
def _keys_of(schema: dict) -> list[str]:
    """스키마 → 그 축의 기준 키. 축이 둘이라(ADR-0015) 목도 받은 스키마를 보고 답해야 한다."""
    assert schema in (PRODUCT_REVIEW_SCHEMA, ORDER_REVIEW_SCHEMA), "모르는 스키마가 넘어왔다"
    return [k for k in schema["required"] if k in CRITERION_MATERIAL]


def _fake_sectionize(prompt: str, schema: dict) -> dict:
    tail = prompt.split("[입력]", 1)[-1]
    out = {k: ({"verdict": f"{k}요약", "minority": None} if f'"{_MATERIAL[k]}"' in tail
               else dict(EMPTY_CRITERION))
           for k in _keys_of(schema)}
    if "pros" in schema["required"]:
        out.update({"pros": ["장점1"], "cons": []})
    return out


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
    assert _verdict(ig, "scent") == "scent요약" and _verdict(ig, "texture") is None, ig
    assert _verdict(dc, "texture") == "texture요약" and _verdict(dc, "scent") is None, dc
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
        prompts.append(prompt)
        return {**{k: dict(EMPTY_CRITERION) for k in _keys_of(schema)},
                "scent": {"verdict": "향요약", "minority": None}, "pros": [], "cons": []}

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
        return {**{k: dict(EMPTY_CRITERION) for k in _keys_of(schema)},
                "scent": {"verdict": "향요약", "minority": None}, "pros": [], "cons": []}

    build_consolidated({"market": "머머", "product": None}, None, [ig, dc],
                       llm_sectionize=_capture)
    assert any('"product": "한줌"' in p for p in prompts), "마켓 모드 소스 프롬프트에 제품 라벨 없음"
    print("✓ market mode: 재료 product 라벨(보류=제품미상)·제품 모드 무라벨 OK")


def test_shipping_section_from_order_level_field():
    """배송·CS 섹션 — shipping_cs 는 후기(주문) 단위 필드(ADR-0005)라 행에 '복제'돼야만 재료가 된다.

    복제는 index.index_post 담당이고, 종합뷰는 이미 복제된 행을 읽는다. 그 계약이 깨지면
    (= 행에 shipping_cs 키가 없으면) 섹션은 조용히 상시 빈칸이 된다 — 그래서 양쪽을 다 본다.
    이제 이 섹션은 **주문 축**(마켓 단위)이다(ADR-0015) — `build_order_view` 가 만든다.
    """
    ship = {"notes": "3일 지연", "sentiment": "neg", "evidence": "배송 3일 지연"}
    reviews = [
        _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"}, shipping_cs=ship),
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"}),   # 배송 미언급
    ]
    mat_dc = _source_material([reviews[0]], fields=ORDER_AXIS["fields"])
    assert mat_dc["shipping_cs"][0]["notes"] == "3일 지연", mat_dc
    assert not _source_material([reviews[1]], fields=ORDER_AXIS["fields"])   # 미언급 → 재료 없음

    ov = build_order_view("머머", reviews, llm_sectionize=_fake_sectionize)
    assert _verdict(ov["review_summaries"]["dcinside"], "shipping") == "shipping요약"
    # 배송을 한 마디도 안 한 출처는 **요약 호출 자체를 안 한다**(전 칸 null 인 요약에 과금 금지)
    assert ov["review_summaries"]["instagram"] is None
    assert ov["review_summaries"]["integrated"] is None    # 한쪽뿐이면 통합 없음(평균 금지)
    assert "shipping" in ORDER_REVIEW_SCHEMA["required"]
    print("✓ shipping: 주문단위 필드가 주문 축(마켓) 요약 재료로 흐름, 무언급 출처는 미호출 OK")


def test_order_axis_absent_from_product_summary():
    """제품 축 요약에 고객 응대·배송 칸이 **없다**(ADR-0015).

    있으면 비교글 하나의 배송 불만이 그 글이 언급한 모든 제품의 요약에 각각 관측된 것처럼
    실린다 — 표시 문제가 아니라 귀속(attribution) 오류다.
    """
    ship = {"notes": "3일 지연", "sentiment": "neg", "evidence": "배송 3일 지연"}
    v = build_consolidated({"market": "머머", "product": "X"}, None,
                           [_rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"},
                                 shipping_cs=ship)],
                           llm_sectionize=_fake_sectionize)
    dc = v["review_summaries"]["dcinside"]
    for k in MARKET_KEYS:
        assert k not in dc, f"제품 축 요약에 주문 기준 {k} 가 들어 있다"
        assert k not in v["criterion_stats"], f"제품 축 집계에 주문 기준 {k} 가 들어 있다"
    assert _verdict(dc, "scent") == "scent요약"                 # 제품 기준은 그대로
    # 재료도 아예 안 넘어간다 — 담을 칸이 없는 내용을 보여주면 남는 칸에 밀어 넣는다
    assert "shipping_cs" not in _source_material([_rec("dcinside", "neg", shipping_cs=ship)],
                                                 fields=PRODUCT_AXIS["fields"])
    print("✓ 축 분리: 제품 축 요약·집계에 주문 기준 미포함, 재료도 미유입 OK")


def test_fanout_copies_are_folded_once():
    """팬아웃 복제분은 **조각 단위로 접힌 뒤** 세어진다(ADR-0015).

    한 글이 제품 3개를 언급하면 `index_post` 가 같은 `shipping_cs` 를 3행에 복제한다. 접지
    않으면 한 주문의 배송 불만이 3건으로 세어진다 — ADR-0014 '유보'에 적혀 있던 부풀림이다.
    """
    ship = {"notes": "3일 지연", "sentiment": "neg", "evidence": "배송 3일 지연"}
    ref = {"platform": "dcinside", "url": "https://gall.dcinside.com/x?no=1", "thread_no": "1"}
    fanout = [{**_rec("dcinside", "neg", shipping_cs=ship), "source_ref": ref,
               "product_ref": {"market": "머머", "product": p}} for p in ("A", "B", "C")]

    assert len(_fold_orders(fanout)) == 1, "같은 조각의 복제 3행이 안 접혔다"
    st = criterion_stats(fanout, MARKET_KEYS)
    assert st["shipping"]["total"]["n"] == 1, st["shipping"]["total"]
    # 제품 축은 접지 않는다 — 거긴 행 하나가 곧 제품 하나라 복제가 아니다
    tex = [{**r, "texture": {"sentiment": "pos", "evidence": "말랑"}} for r in fanout]
    assert criterion_stats(tex, ["texture"])["texture"]["total"]["n"] == 3

    ov = build_order_view("머머", fanout, llm_sectionize=_fake_sectionize)
    assert ov["n_orders"] == 1, ov["n_orders"]
    print("✓ 접기: 팬아웃 복제분이 조각 1건으로 접히고 제품 축은 안 접힘 OK")


def test_fold_keeps_distinct_pieces():
    """접기가 **서로 다른 조각**을 삼키지 않는다 — 과소 집계는 과대 집계보다 알아채기 어렵다.

    같은 스레드의 서로 다른 댓글, 그리고 식별자가 아예 없는 행(ADR-0009 이전 색인분)은
    각각 살아남아야 한다.
    """
    ship = {"notes": "빨라요", "sentiment": "pos", "evidence": "다음날 왔"}
    url = "https://gall.dcinside.com/x?no=1"
    rows = [
        {**_rec("dcinside", "pos", shipping_cs=ship),
         "source_ref": {"platform": "dcinside", "url": url, "comment_no": str(i)}}
        for i in (1, 2, 3)
    ]
    assert len(_fold_orders(rows)) == 3, "한 스레드의 다른 댓글이 하나로 접혔다"
    # 식별자 없는 행은 접을 키가 없다 → 그대로 남는다(백필 없음, 재수집으로만 해결)
    nokey = [_rec("dcinside", "pos", shipping_cs=ship) for _ in range(2)]
    assert len(_fold_orders(nokey)) == 2
    print("✓ 접기: 서로 다른 댓글·식별자 없는 행 보존(과소 집계 회귀) OK")


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
    """6기준이 **두 축에 나뉘어** 전부 존재하고 전부 strict required(ADR-0011 + ADR-0015).

    하나라도 빠지면 표에 상시 빈칸이 생긴다. 축이 갈렸어도 화면 표의 행은 여전히 6줄이다 —
    갈린 건 '어디서 만드는가'이지 '무엇을 보여주는가'가 아니다.
    """
    assert [c["key"] for c in CRITERIA] == \
        ["texture", "scent", "sound", "longevity", "cs", "shipping"], CRITERIA
    # scope 는 CRITERIA 하나가 정한다 — 파생 리스트가 그 값과 어긋나면 축이 조용히 갈라진다.
    assert PRODUCT_KEYS == ["texture", "scent", "sound", "longevity"], PRODUCT_KEYS
    assert MARKET_KEYS == ["cs", "shipping"], MARKET_KEYS
    assert set(PRODUCT_KEYS) | set(MARKET_KEYS) == set(CRITERIA_KEYS), "축이 6기준을 안 덮는다"
    assert not (set(PRODUCT_KEYS) & set(MARKET_KEYS)), "한 기준이 두 축에 걸쳐 있다"
    assert all(CRITERION_SCOPE[k] == "product" for k in PRODUCT_KEYS)
    assert all(CRITERION_SCOPE[k] == "market" for k in MARKET_KEYS)

    for schema, keys, pros in ((PRODUCT_REVIEW_SCHEMA, PRODUCT_KEYS, True),
                               (ORDER_REVIEW_SCHEMA, MARKET_KEYS, False)):
        props = schema["properties"]
        for k in keys:
            assert k in props, f"{k} 가 스키마에 없다"
            assert k in schema["required"], f"{k} 가 required 에 없다"
            # 기준 한 칸 = {verdict, minority}(ADR-0014). 문자열 한 칸으로 되돌리면 대립 평가가
            # 다시 한 문장에 병렬로 눕고, 어느 쪽이 다수인지 읽는 사람이 알 수 없게 된다.
            cell = props[k]
            assert cell["type"] == "object", f"{k} 는 다수/소수 두 칸이어야 한다"
            assert cell["additionalProperties"] is False
            assert set(cell["required"]) == {"verdict", "minority"}, cell["required"]
            for slot in ("verdict", "minority"):
                assert cell["properties"][slot]["type"] == ["string", "null"], \
                    f"{k}.{slot} 는 미언급 null 을 허용해야 한다"
        # 다른 축의 기준은 **없어야** 한다 — 있으면 그 축이 남의 사실을 쓰게 된다
        for other in set(CRITERIA_KEYS) - set(keys):
            assert other not in props, f"{other} 가 남의 축 스키마에 있다"
        # 장단점은 제품 축에만 있다 — 한 화면에 장단점 목록이 둘이면 어느 쪽인지 알 수 없다
        assert ("pros" in props) is pros and ("cons" in props) is pros
        # strict structured outputs 계약
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(props), "required 와 properties 불일치"
    print("✓ 스키마: 6기준이 두 축에 분리·required·다수/소수 두 칸·타축 미포함 OK")


def test_criterion_stats_counts_by_source():
    """기준별 건수는 **코드**가 센다 — 화면 배지('갈림 7:3'·'인스타만')의 유일한 근거.

    LLM 에게 세게 하면 표본에 없는 확신이 산문에 섞이고, '출처 갭이 큰 편이라' 같은 메타
    설명이 본문으로 샌다(2026-08-07 실측). 숫자는 여기서 나오고 문장은 내용만 쓴다.
    """
    reviews = [
        _rec("instagram", "pos", texture={"sentiment": "pos", "evidence": "크리미"}),
        _rec("instagram", "pos", texture={"sentiment": "pos", "evidence": "유들"},
             scent={"sentiment": "pos", "evidence": "과자향"}),
        _rec("dcinside", "neg", texture={"sentiment": "neg", "evidence": "유분기"}),
    ]
    st = criterion_stats(reviews)
    assert st["texture"]["total"] == {"pos": 2, "neg": 1, "neu": 0, "n": 3}
    assert st["texture"]["by_source"]["dcinside"] == {"pos": 0, "neg": 1, "neu": 0, "n": 1}
    assert st["texture"]["split"] is True, "pos·neg 가 함께 있는데 갈림으로 안 잡혔다"
    assert st["texture"]["only_source"] is None
    # 한 출처에서만 언급된 기준 → 배지 '인스타만'
    assert st["scent"]["only_source"] == "instagram" and st["scent"]["split"] is False
    # 언급 0 인 기준도 키는 있어야 한다(표시부가 KeyError 안 나게)
    assert st["sound"]["total"]["n"] == 0 and st["sound"]["only_source"] is None
    print("✓ criterion_stats: 소스별 건수·갈림·단일출처 판정 OK")


def test_stats_reach_view_and_prompts_without_gap():
    """카운트는 뷰(`criterion_stats`)와 프롬프트로 가고, **갭 수치는 프롬프트에 안 간다.**

    갭을 넣었더니 모델이 '출처 갭이 큰 편이라' 하고 메타 설명을 본문에 적었다 — 갭은 코드가
    계산해 화면이 배지로 보여줄 값이지 요약의 소재가 아니다(ADR-0014).
    """
    prompts: list[str] = []

    def _capture(prompt: str, schema: dict) -> dict:
        prompts.append(prompt)
        return _fake_sectionize(prompt, schema)

    reviews = [_rec("instagram", "pos", texture={"sentiment": "pos", "evidence": "크리미"}),
               _rec("dcinside", "neg", texture={"sentiment": "neg", "evidence": "유분기"})]
    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                           llm_sectionize=_capture)
    assert v["criterion_stats"]["texture"]["split"] is True      # 뷰에 실린다
    assert v["sentiment_gap"] is not None                        # 갭 자체는 계속 계산된다
    assert all('"counts"' in p for p in prompts), "다수 판정 재료(counts)가 프롬프트에 없다"
    for p in prompts:
        assert "sentiment_gap" not in p, "갭 수치가 요약 프롬프트로 유입됨(메타 유출 경로)"
        assert "instagram_net" not in p and "dcinside_net" not in p
    # 소스별 요약은 **그 출처 카운트만** 본다 — 다른 출처를 보면 소스 미평균 규칙이 샌다
    ig_prompt = prompts[0]
    assert '"pos": 1' in ig_prompt and '"neg": 1' not in ig_prompt, ig_prompt
    print("✓ counts: 프롬프트 유입·출처별 격리 + 갭 수치 미유입 OK")


def test_absence_and_meta_are_stripped():
    """부재 서술·메타 설명은 **코드가** 잘라낸다 — 프롬프트에만 맡기지 않는다.

    전언(hearsay) 차단과 같은 이유다: 같은 규칙을 프롬프트에 적어 둬도 모델은 종종 보류 근거
    ('~는 없어요')와 메타('출처 갭이 큰 편이라')를 본문에 흘린다. 아래 문장들은 전부 실제
    저장된 요약에서 가져온 것이다(2026-08-07 DB).
    """
    dirty = {
        # 실제 유출 1 — 부재 서술이 내용에 붙어 한 문장을 이룬 경우
        "longevity": {"verdict": "재미없다고 방치했다는 말은 있지만, 빨리 죽거나 굳는다는 "
                                 "식의 지속력 평가는 없어요.", "minority": None},
        # 실제 유출 2 — '어느 출처에만 있다'는 메타. 배지가 할 일이다
        "scent": {"verdict": "향 평가는 인스타에서만 나와요.",
                  "minority": "일부는 비누향이라고 해요."},
        # 실제 유출 3 — 갭 서술 + 출처 대비를 문장 안에 녹인 경우
        "texture": {"verdict": "다만 출처 갭이 큰 편이라 세부 평가는 갈려요. 크리미하다는 "
                               "평가가 다수예요.", "minority": None},
        "sound": {"verdict": "기포가 팡팡 터지는 소리가 좋다는 반응이에요.", "minority": None},
        "cs": dict(EMPTY_CRITERION), "shipping": dict(EMPTY_CRITERION),
        "pros": ["[디시] 손에 잘 안 붙어요"], "cons": [],
    }
    assert len(_violations(dirty)) == 3, _violations(dirty)   # 재시도 트리거

    out = _scrub_section(dirty)
    assert out["longevity"] == EMPTY_CRITERION, "부재 서술이 남았다"
    # 메타 문장이 잘리고 남은 소수 의견이 다수 칸으로 올라온다(내용 보존)
    assert out["scent"] == {"verdict": "일부는 비누향이라고 해요.", "minority": None}, out["scent"]
    # 메타 문장만 잘리고 실제 내용 문장은 살아남는다
    assert out["texture"]["verdict"] == "크리미하다는 평가가 다수예요.", out["texture"]
    assert out["sound"]["verdict"] == "기포가 팡팡 터지는 소리가 좋다는 반응이에요."
    # 장단점의 출처 표기(`[디시]`)는 건드리지 않는다 — 거긴 구조가 아니라 항목 라벨이다
    assert out["pros"] == ["[디시] 손에 잘 안 붙어요"]
    print("✓ 스크럽: 부재 서술·메타·출처 대비 문장 제거, 실내용·pros 보존 OK")


def test_clean_summary_survives_scrub():
    """정상 문장은 하나도 잘리지 않는다 — 과잉 차단이면 요약이 통째로 빈칸이 된다.

    특히 '없다'가 **내용**인 문장('손에 잘 안 붙고 잔여감도 없다')을 부재 서술로 오인하면
    안 된다. 부재 서술은 '언급/평가/말이 없다'이지 '잔여감이 없다'가 아니다.
    """
    clean = {
        "texture": {"verdict": "크리미하고 유들유들하다는 평가가 다수예요.",
                    "minority": "일부는 유분기가 많고 손붙음이 있대요."},
        "longevity": {"verdict": "손에 잘 안 붙고 잔여감도 없다는 말이 있어요.", "minority": None},
        "sound": {"verdict": "수명이 길고 다음 날 만져도 재밌다는 반응이 있어요.", "minority": None},
        "scent": dict(EMPTY_CRITERION), "cs": dict(EMPTY_CRITERION),
        "shipping": dict(EMPTY_CRITERION), "pros": [], "cons": [],
    }
    assert _violations(clean) == []
    assert _scrub_section(clean) == clean, "정상 문장이 잘렸다(과잉 차단)"
    print("✓ 스크럽: 정상 문장·'내용으로서의 없다' 보존 OK")


def test_retry_once_on_violation():
    """위반이 있으면 **1회 재시도**한다(결정성 정책의 파싱 재시도와 같은 모양).

    스크럽은 문장 단위라 '내용 + 부재'가 한 문장에 붙으면 내용까지 날아간다. 재시도가 먼저
    제대로 쓸 기회를 주고, 스크럽은 그래도 남은 것만 걷어내는 그물이다.
    """
    calls: list[str] = []

    def _once_dirty(prompt: str, schema: dict) -> dict:
        calls.append(prompt)
        if len(calls) % 2 == 1:      # 첫 호출은 부재 서술을 흘린다
            return {**{k: dict(EMPTY_CRITERION) for k in _keys_of(schema)},
                    "scent": {"verdict": "향 언급은 없어요.", "minority": None},
                    "pros": [], "cons": []}
        return {**{k: dict(EMPTY_CRITERION) for k in _keys_of(schema)},
                "scent": {"verdict": "레몬향이 진하다는 말이 많아요.", "minority": None},
                "pros": [], "cons": []}

    v = build_consolidated({"market": "머머", "product": "X"}, None,
                           [_rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"})],
                           llm_sectionize=_once_dirty)
    assert len(calls) == 2, f"재시도가 안 돌았다(호출 {len(calls)}회)"
    assert "직전 응답이 아래 규칙을 어겼다" in calls[1], "재시도 프롬프트에 위반 목록이 없다"
    assert _verdict(v["review_summaries"]["instagram"], "scent") == "레몬향이 진하다는 말이 많아요."
    print("✓ 재시도: 위반 1회 재요청 + 위반 목록 전달 OK")


def _all_prompts() -> list[tuple[str, str]]:
    """축 2 × 종류 3 = 여섯 프롬프트. 공통 규칙 게이트는 전부를 훑어야 한다(ADR-0015).

    축이 갈리면서 프롬프트가 셋에서 여섯이 됐다 — 한 곳만 고치고 넘어갈 자리가 두 배가 됐다는 뜻.
    """
    return [(f"{axis['name']}.{slot}", axis[slot])
            for axis in (PRODUCT_AXIS, ORDER_AXIS)
            for slot in ("section", "integrated", "supporter")]


def test_no_meta_rule_reaches_every_prompt():
    """부재·메타 금지 규칙이 여섯 프롬프트 전부에 실린다 — 한 곳만 고치면 반드시 어긋난다."""
    for name, p in _all_prompts():
        assert "부재 서술 금지" in p, f"{name} 에 부재 서술 금지가 없다"
        assert "메타 설명 금지" in p, f"{name} 에 메타 설명 금지가 없다"
        assert "verdict" in p and "minority" in p, f"{name} 에 다수/소수 규칙이 없다"
        assert "나란히 놓지 마라" in p, f"{name} 에 대립 병렬 금지가 없다"
    print("✓ 프롬프트: 부재·메타 금지 + 다수/소수 규칙 여섯 프롬프트 도달 OK")


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
    assert _verdict(dc, "sound") == "sound요약" and _verdict(dc, "longevity") == "longevity요약", dc
    assert _verdict(dc, "scent") is None and _verdict(dc, "texture") is None, "미언급 기준이 채워졌다"
    assert "걀걀 소리 큼" in prompts[0] and "빨리 죽음" in prompts[0], "소리·지속력 재료 미유입"
    print("✓ 소리·지속력: 재료 유입 + 전용 섹션 산출 OK")


def test_cs_and_shipping_split_one_material():
    """고객 응대 / 배송은 재료 필드 하나(shipping_cs)에서 갈린다 — 둘 다 열려 있어야 한다."""
    ship = {"notes": "3일 지연, 교환 문의 무응답", "sentiment": "neg", "evidence": "배송 3일 지연"}
    ov = build_order_view("머머", [_rec("dcinside", "neg", shipping_cs=ship)],
                          llm_sectionize=_fake_sectionize)
    dc = ov["review_summaries"]["dcinside"]
    assert _verdict(dc, "cs") == "cs요약" and _verdict(dc, "shipping") == "shipping요약", dc
    # 프롬프트가 분기 규칙을 실제로 설명하는지 — 안 하면 모델이 한쪽에 몰아 쓴다
    for p in (ORDER_AXIS["section"], ORDER_AXIS["supporter"]):
        assert "cs 와 shipping 두 섹션으로 갈린다" in p, "분기 규칙이 프롬프트에 없다"
    print("✓ 고객 응대/배송: 재료 1개 → 섹션 2개 분기 + 프롬프트 규칙 OK")


def test_axis_prompts_never_mention_the_other_axis():
    """한 축의 프롬프트에 **다른 축의 기준 키가 등장하지 않는다**(ADR-0015).

    담을 칸이 없는 기준을 설명하면 모델이 그 내용을 남은 칸에 밀어 넣는다. 재료 화이트리스트가
    1차 방어지만, 프롬프트가 '배송도 써라'라고 적혀 있으면 모델은 없는 걸 지어내기도 한다.
    """
    for slot in ("section", "integrated", "supporter"):
        for axis, mine, theirs in ((PRODUCT_AXIS, PRODUCT_KEYS, MARKET_KEYS),
                                   (ORDER_AXIS, MARKET_KEYS, PRODUCT_KEYS)):
            p = axis[slot]
            for k in mine:
                assert f"- {k}:" in p, f"{axis['name']}.{slot} 에 자기 기준 {k} 설명이 없다"
            for k in theirs:
                assert f"- {k}:" not in p, \
                    f"{axis['name']}.{slot} 이 남의 축 기준 {k} 를 설명한다"
    # 주문 축은 제품 라벨을 붙이지 않는다 — 주문 경험은 제품별 사실이 아니다
    assert "제품명을 문장에 넣지 마라" in ORDER_AXIS["section"]
    assert "제품명을 표기해 구분한다" in PRODUCT_AXIS["section"]
    print("✓ 프롬프트: 축 간 기준 누출 없음 + 제품 라벨 규칙 축별 분기 OK")


def test_tone_rule_reaches_every_summary_prompt():
    """말투('~해요'체)는 여섯 요약 프롬프트 전부에 실려야 한다.

    화면 카피(`web/`)가 전부 해요체라 요약 하나만 '~다'체면 한 화면에서 톤이 튄다. 축이 갈리면서
    프롬프트가 셋에서 여섯이 됐고(축 2 × 종류 3), 한 곳만 고치고 넘어갈 자리도 두 배가 됐다 —
    그래서 게이트로 둔다. 서포터 버킷도 같은 화면에 나가므로 포함.
    """
    for name, p in _all_prompts():
        assert "[말투]" in p and "'~해요'체" in p, f"{name} 에 말투 규칙이 없다"
        # 말투는 표현만 바꾸는 것 — 부정 평가를 순화하면 소스 편향(1급 기능)이 지워진다.
        assert "누그러뜨리지" in p, f"{name} 에 '순화 금지' 가드가 없다"
        # '소스'가 아니라 '출처' — 화면 카피와 같은 말을 써야 요약문에도 같은 말이 나온다.
        assert "소스" not in p, f"{name} 에 '소스' 표기가 남아 있다(화면 카피는 '출처')"

    # 실제 호출 경로에도 실리는지 — 상수만 보면 조립 단계에서 잘려도 못 잡는다. 축 둘 다 본다.
    prompts: list[str] = []

    def _capture(prompt: str, schema: dict) -> dict:
        prompts.append(prompt)
        return _fake_sectionize(prompt, schema)

    ship = {"sentiment": "neg", "evidence": "3일 걸렸"}
    reviews = [_rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"},
                    shipping_cs={"sentiment": "pos", "evidence": "다음날 왔"}),
               _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"},
                    shipping_cs=ship)]
    build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                       llm_sectionize=_capture)
    build_order_view("머머", reviews, llm_sectionize=_capture)
    assert len(prompts) == 6, f"축별 3개씩 총 6회여야 하는데 {len(prompts)}회"
    assert all("[말투]" in p for p in prompts), "조립된 프롬프트에 말투 누락"
    print("✓ 말투: 여섯 요약 프롬프트 전부 '~해요'체 규칙 + '출처' 표기 OK")


def test_promo_view_has_all_criteria_keys():
    """서포터 버킷도 **자기 축의** 기준 키를 다 갖는다 — LLM 없이도 표시부가 KeyError 안 나게."""
    promo = _rec("instagram", "pos", "promo", scent={"sentiment": "pos", "evidence": "협찬향"},
                 shipping_cs={"sentiment": "pos", "evidence": "서포터 발송 빨랐"})
    pv = build_consolidated({"market": "머머", "product": "X"}, None, [promo])["promo_view"]
    assert pv["n_promo"] == 1
    for k in PRODUCT_KEYS:
        assert k in pv and pv[k] == EMPTY_CRITERION, f"promo_view(제품 축)에 {k} 문제"
    for k in MARKET_KEYS:
        assert k not in pv, f"제품 축 promo_view 에 주문 기준 {k} 가 있다"

    opv = build_order_view("머머", [promo])["promo_view"]
    assert opv["n_promo"] == 1
    for k in MARKET_KEYS:
        assert k in opv and opv[k] == EMPTY_CRITERION, f"promo_view(주문 축)에 {k} 문제"
    # 서포터 발송은 일반 주문과 경로가 달라 섞지 않는다 — 프롬프트가 그 이유를 실제로 싣는다
    assert "서포터 발송은 일반 주문과 경로가 달라" in ORDER_AXIS["supporter"]
    print("✓ promo_view: 축별 기준 키 전부 존재·타축 미포함(LLM 없으면 None) OK")


def _run_all():
    test_schema_covers_six_criteria()
    test_criterion_stats_counts_by_source()
    test_stats_reach_view_and_prompts_without_gap()
    test_absence_and_meta_are_stripped()
    test_clean_summary_survives_scrub()
    test_retry_once_on_violation()
    test_no_meta_rule_reaches_every_prompt()
    test_sound_and_longevity_reach_the_prompt()
    test_cs_and_shipping_split_one_material()
    test_axis_prompts_never_mention_the_other_axis()
    test_tone_rule_reaches_every_summary_prompt()
    test_promo_view_has_all_criteria_keys()
    test_source_material_only_mentioned()
    test_blank_section_when_attr_absent()
    test_shipping_section_from_order_level_field()
    test_order_axis_absent_from_product_summary()
    test_fanout_copies_are_folded_once()
    test_fold_keeps_distinct_pieces()
    test_index_replicates_shipping_cs_to_rows()
    test_integrated_only_when_both_sources()
    test_promo_excluded_from_review_summaries()
    test_no_sectionize_all_none()
    test_official_spec_never_in_summary_prompts()
    test_market_mode_tags_products()
    print("\n리뷰 요약 섹션 오프라인 테스트 전부 통과 ✅")


if __name__ == "__main__":
    _run_all()
