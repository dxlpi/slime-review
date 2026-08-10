# -*- coding: utf-8 -*-
"""
전언(hearsay) 추출 하드닝 테스트 — 계획 `.omc/plans/kind-axis-resolution.md` AC15.

왜 필요한가(§1-C): `LAYER2_SYSTEM` 에 전언 배제 절이 없어서 "다들 좋다고 하는" 같은 표현이
긍정 평가로 추출되면 가짜 후기 행이 적재되고, 그게 **디시 긍정 카운트를 부풀려** 1급 기능인
소스 편향을 왜곡한다. 관련성 게이트(kind 축)로는 막히지 않는 축이라 프롬프트에서 막는다.

두 층으로 나눈다:
- **프롬프트 스냅샷**(키 불필요·결정적): 배제 절·직접지각 대조·과교정 가드가 실제로 프롬프트에 있는가.
- **결정적 게이트**(키 불필요): `firsthand_evidence` 가 null 인 항목을 코드가 버리는가.
  ⚠️ 계획 C-3 은 "프롬프트만 수정, 스키마 불변"이라고 적었지만, 프롬프트만으로는 **실측상 못 막는다**
  (같은 입력 4회 호출 → 4번 다 다른 답). 그래서 스키마에 근거 필드를 하나 추가하고 코드로 확정했다.
  1층에서 '제품명은 반드시 캡션 해시태그'로 유령 제품을 막은 것과 같은 수법이다.
- **통합**(OPENAI_API_KEY 있을 때만): 실제 추출 결과가 전언을 항목으로 만들지 않는가 + 본인 후기는 살아있는가.

실행:  python -m eval.test_extract_hearsay   (repo 루트에서)
"""
from __future__ import annotations

import os

from slime_rag.extract import LAYER2_SCHEMA, LAYER2_SYSTEM, drop_hearsay_reviews

# 계획 §1-C·AC15 의 고정 케이스.
HEARSAY_FIXTURE = "친구가 허니푸냥이 샀는데 걀걀거림 심하다고 함"
DC015_FIXTURE = (
    "ㅂ 얘네 살만한가?\n"
    "이미 ㅂ 유슬은 다 시켰는데 다들 좋다고 하는 것 중에 아직 안산게\n"
    "그래놀라딸기토스트 베어브런치타임 베리팝스 크림앤플라워 인데 살말?\n"
    "슬린이라 ㅂ은 첫굼인고 솝 들어간거 만져본거라곤 ㅇㅉ거 밖에 없는데 좀 많이 실망함."
)
FIRSTPERSON_FIXTURE = "ㅂ 그래놀라딸토 만져봤는데 향은 호불호 갈려도 질감 쫀득하고 개좋았음"
COMPARE_FIXTURE = (
    "ㅂ 카피바라랑 푸냥이 둘다 만져본 후기\n"
    "카피바라는 솝퐁말이라 사르르한 느낌이고, 푸냥이는 실키하면서 물먹은 퐁말임"
)


# ---------------------------------------------------------------- 프롬프트 스냅샷 (키 불필요)
def test_hearsay_clause_present():
    """전언 배제 절이 프롬프트에 실재하고, 대표 전언 표지를 명시한다."""
    assert "전언" in LAYER2_SYSTEM, "전언 배제 절이 LAYER2_SYSTEM 에 없음"
    for marker in ("-다더라", "-다고 함", "들었는데", "다들"):
        assert marker in LAYER2_SYSTEM, f"전언 표지 '{marker}' 가 프롬프트에 없음"
    assert HEARSAY_FIXTURE in LAYER2_SYSTEM, "AC15 고정 케이스가 프롬프트 예시로 없음"
    print("✓ AC15 전언 배제 절 + 표지 목록 present OK")


def test_direct_perception_contrast_present():
    """직접지각(-더라/-던데)을 전언과 '대조'로 명시 — 둘을 뭉뚱그리면 본인 후기가 죽는다."""
    assert "직접 지각" in LAYER2_SYSTEM, "직접지각 대조 절이 없음"
    for marker in ("-더라", "만져봤는데", "써보니"):
        assert marker in LAYER2_SYSTEM, f"직접지각 표지 '{marker}' 가 프롬프트에 없음"
    # '-더라'(직접) 와 '-다더라'(전언) 를 혼동하지 말라는 지시가 있어야 한다.
    assert "혼동 금지" in LAYER2_SYSTEM, "'-더라' vs '-다더라' 혼동 경고가 없음"
    print("✓ AC15 직접지각 대조(-더라 vs -다더라) OK")


def test_overcorrection_guard_present():
    """과교정 가드 — 표지가 없으면 1인칭으로 간주하라는 지시."""
    assert "과교정 금지" in LAYER2_SYSTEM, "과교정 가드가 없음"
    assert "1인칭으로 간주" in LAYER2_SYSTEM, "'표지 없으면 1인칭' 기본값 지시가 없음"
    print("✓ AC15 과교정 가드 OK")


def test_deterministic_firsthand_gate():
    """
    프롬프트만으로는 못 막는다 — 결정적 게이트가 있어야 한다.

    실측(같은 입력 `dc-015`, 4회 호출): `[]` / `['ㅇㅉ거','ㅂ 유슬']` /
    `['ㅇㅉ거', 전언제품 4개]` / `[]` — 매번 달랐다. 가짜 후기 행 하나가 디시 긍정 카운트를
    부풀려 소스 편향을 왜곡하므로 비결정에 맡기지 않는다.
    """
    props = LAYER2_SCHEMA["properties"]["reviews"]["items"]
    assert "firsthand_evidence" in props["properties"], "본인경험 근거 필드가 스키마에 없음"
    assert "firsthand_evidence" in props["required"], "근거 필드가 required 가 아님(strict 위반)"

    src = "허니푸냥이 만져봤는데 좋았음 근데 베리팝스는 다들 좋다고 하더라"
    doc = {"reviews": [
        {"mentioned_product": "허니푸냥이", "firsthand_evidence": "만져봤는데 좋았음"},
        {"mentioned_product": "베리팝스", "firsthand_evidence": None},         # ① 근거 없음
        {"mentioned_product": "크림앤플라워", "firsthand_evidence": "  "},      # ① 공백
        {"mentioned_product": "초코비", "firsthand_evidence": "초코비 개좋았음"},  # ② 원문에 없음(지어냄)
        {"mentioned_product": "베리팝스", "firsthand_evidence": "다들 좋다고 하더라"},  # ③ 근거가 전언
    ]}
    kept = [r["mentioned_product"] for r in drop_hearsay_reviews(doc, src)["reviews"]]
    assert kept == ["허니푸냥이"], f"결정적 게이트 오작동: {kept}"

    # 과교정 가드: 원문을 안 넘기면 ②(지어낸 인용) 검사는 건너뛴다(하위호환).
    doc2 = {"reviews": [{"mentioned_product": "초코비", "firsthand_evidence": "초코비 개좋았음"}]}
    assert len(drop_hearsay_reviews(doc2)["reviews"]) == 1, "source_text 없이 과교정됨"
    print("✓ AC15 결정적 게이트 3겹(빈 근거·지어낸 인용·전언 근거) OK")


def test_candidate_span_gate_drops_shopping_lists():
    """4번째 겹 — 근거 조각이 **구매 예정 표지**면 폐기한다(장바구니 목록 ≠ 후기).

    실측(2026-08-10, 아모스갤): `담았는데 우뗘??` 한 조각이 제품 6행을 냈다. 사지도 않은
    제품 6건이 중립 후기로 집계에 들어간 것이다.
    """
    src = ("허니푸냥이 만져봤는데 좋았음 개굴악어 빠코볼 담았는데 우뗘?? "
           "레몬스쿱 빼야겟다 ㅂㅇㅍ는 좀 고민")
    doc = {"reviews": [
        {"mentioned_product": "허니푸냥이", "firsthand_evidence": "만져봤는데 좋았음"},
        {"mentioned_product": "개굴악어", "firsthand_evidence": "담았는데 우뗘??"},
        {"mentioned_product": "빠코볼", "firsthand_evidence": "담았는데 우뗘??"},
        {"mentioned_product": "레몬스쿱", "firsthand_evidence": "레몬스쿱 빼야겟다"},
        {"mentioned_product": "베이퍼것", "firsthand_evidence": "ㅂㅇㅍ는 좀 고민"},
    ]}
    kept = [r["mentioned_product"] for r in drop_hearsay_reviews(doc, src)["reviews"]]
    assert kept == ["허니푸냥이"], f"구매 예정 게이트 오작동: {kept}"
    print("✓ AC15 4번째 겹(구매 예정 근거) 폐기 OK")


def test_candidate_span_gate_keeps_terse_ownership_ratings():
    """⛔ **과교정 회귀 — 여기가 이 게이트에서 가장 비싼 자리다.**

    짧은 평점 나열은 진짜 보유 후기다. 스레드 142738 은 `이렇개 만져봤고` 라고 밝힌
    보유 평가글인데, 항목이 `잭두콩 썸` 처럼 두 단어라 '후보 목록'처럼 **보인다**.
    표지가 없으면 통과시켜야 한다 — 회수 손실은 화면에 안 보이고, 그중 부정 후기의
    손실은 1급 기능(출처 편향)을 직접 깎는다.
    """
    src = "이렇개 만져봤고 잭두콩 썸 핑키별 쏘쏘 1믹스 2허밍 향은 궁금해서 사본 거임"
    doc = {"reviews": [
        {"mentioned_product": "잭두콩", "firsthand_evidence": "잭두콩 썸"},
        {"mentioned_product": "핑키별", "firsthand_evidence": "핑키별 쏘쏘"},
        {"mentioned_product": "믹스", "firsthand_evidence": "1믹스 2허밍"},
        # `궁금` 은 어휘에 **없다** — 유일한 실측 매칭이 `궁금해서 사본`(산 뒤 후기)이었다.
        {"mentioned_product": "레몬커드쉘도넛", "firsthand_evidence": "궁금해서 사본"},
    ]}
    kept = [r["mentioned_product"] for r in drop_hearsay_reviews(doc, src)["reviews"]]
    assert kept == ["잭두콩", "핑키별", "믹스", "레몬커드쉘도넛"], \
        f"진짜 보유 후기를 버렸다(과교정): {kept}"
    print("✓ AC15 4번째 겹 과교정 방지(짧은 평점·순위·구매 후 서술 보존) OK")


def test_single_syllable_ratings_survive_the_name_restatement_gate():
    """⛔ **회귀 게이트: 1음절 평점.** `_EVIDENCE_MIN_RESIDUE` 를 2로 올리면 여기서 죽는다.

    근거가 제품명 재기입인지 보는 게이트는 '잔여 길이'로 판정하는데, 이 갤의 평점 어휘엔
    **1음절**이 있다. 실측(2026-08-10 아모스갤, 저장된 근거 전량): 아래 7행이 잔여 1자인데
    전부 진짜 보유 평가다. 임계가 2면 이것들만 사라지고 같은 글의 `쏘쏘`(2음절)는 살아남아
    **한 평점 나열의 절반만** 없어진다 — 조용하고 앞뒤가 안 맞는 손실이다.
    계획서의 구속 정정(스레드 142738 = `이렇개 만져봤고` 라고 밝힌 보유 평가글)이 이 자리다.
    """
    real = [("잭두콩", "잭두콩 썸"), ("허밍", "허밍 썸"), ("미봉", "미봉 썸"),
            ("베스", "베스 썸"), ("밀키휘핑", "밀키휘핑 썸"), ("코코바게트", "코코바게트 썸"),
            ("디스코팡", "디스코팡 썸"), ("핑키별", "핑키별 쏘쏘")]
    src = " ".join(ev for _p, ev in real) + " 이렇개 만져봤고"
    doc = {"reviews": [{"mentioned_product": p, "firsthand_evidence": ev} for p, ev in real]}
    kept = [r["mentioned_product"] for r in drop_hearsay_reviews(doc, src)["reviews"]]
    assert kept == [p for p, _ev in real], f"1음절 평점이 죽었다: {kept}"

    # 반대 방향은 그대로 — 제품명을 **그대로 다시 적은** 근거(잔여 0자)는 여전히 버린다.
    doc2 = {"reviews": [{"mentioned_product": "헝잭버거", "firsthand_evidence": "헝잭버거"}]}
    assert drop_hearsay_reviews(doc2, "헝잭버거 먹어봄")["reviews"] == [], \
        "제품명 재기입 근거가 살아남았다(게이트가 꺼졌다)"
    print("✓ AC15 1음절 평점 보존 + 제품명 재기입 폐기 OK")


def test_candidate_gate_is_not_an_axis():
    """구매 예정 판정은 **관련성 축이 아니다** — 게이트가 이걸로 조각을 버리면 안 된다.

    첨삭 스레드는 후보에서 빠지면 안 된다: 거기 달린 **댓글**엔 남이 쓴 진짜 후기가 있다.
    """
    from slime_rag import relevance_rules as rules
    assert rules.is_candidate_span("담았는데 우뗘??") is True
    assert set(rules.axes("개굴악어 빠코볼 담았는데 우뗘??")) == {"M", "Q", "E"}, "축이 늘었다"
    print("✓ 구매 예정 판정이 M/Q/E 축에 안 섞임 OK")


def test_doc_shape_unchanged_by_hardening():
    """
    문서 모양·호출부는 불변이어야 한다(계획 C-3 의 의도).
    하드닝으로 늘어난 건 reviews[] 항목 안의 근거 필드 하나뿐 — 최상위 계약은 그대로라
    `index.index_post` 등 하류는 손댈 필요가 없다.
    """
    props = LAYER2_SCHEMA["properties"]
    assert set(props) == {"market", "shipping_cs", "reviews", "flags"}, \
        f"LAYER2_SCHEMA 최상위 키가 바뀜: {sorted(props)}"
    print("✓ AC15 최상위 문서 계약 불변(하류 무변경) OK")


# ---------------------------------------------------------------- 통합 (키 있을 때만)
def _products(doc: dict) -> list[str]:
    return [(r.get("mentioned_product") or "") for r in (doc.get("reviews") or [])]


def test_live_hearsay_dropped():
    """실호출: 전언은 항목이 안 생기고, 본인 경험 후기·비교글은 정상 추출된다."""
    if not os.getenv("OPENAI_API_KEY"):
        print("· AC15 통합테스트 skip (OPENAI_API_KEY 없음)")
        return
    from slime_rag.extract import extract_review
    from slime_rag.llm_ops import LLM

    llm = LLM()

    doc = extract_review(HEARSAY_FIXTURE, llm)
    assert not doc.get("reviews"), f"전언인데 항목이 생김: {_products(doc)}"

    doc = extract_review(DC015_FIXTURE, llm)
    hearsay_products = {"그래놀라딸기토스트", "베어브런치타임", "베리팝스", "크림앤플라워"}
    got = {p.strip() for p in _products(doc) if p}
    leaked = got & hearsay_products
    assert not leaked, f"'다들 좋다고 하는' 제품이 항목으로 샘: {leaked}"

    # --- 과교정 대조군: 게이트가 본인 경험 항목을 **하나도 지우면 안 된다** ---
    # 항목 '개수'로 재지 않는다. 짧은 후기에서 모델이 항목을 0~2개로 흔드는 건 이 게이트와
    # 무관한 기존 변동성이라(실측: 같은 비교글 4회 → 2/1/2/2건), 개수로 재면 게이트가 아니라
    # 모델 변동성을 테스트하게 된다. 재야 할 건 "게이트 전후로 항목이 줄었는가"다.
    from slime_rag.extract import LAYER2_SCHEMA, drop_hearsay_reviews
    for name, fixture in (("본인 경험 후기", FIRSTPERSON_FIXTURE),
                          ("본인 비교글", COMPARE_FIXTURE)):
        raw = llm.complete(fixture, system=LAYER2_SYSTEM, schema=LAYER2_SCHEMA,
                           label="test.overcorrection")
        n_raw = len(raw.get("reviews") or [])
        n_kept = len(drop_hearsay_reviews(dict(raw), fixture)["reviews"])
        assert n_kept == n_raw, f"{name}이 과교정으로 깎임: {n_raw} → {n_kept}"
        assert n_raw >= 1, f"{name}에서 모델이 항목을 하나도 안 만듦(게이트 이전 문제)"
    print("✓ AC15 통합: 전언 드롭 + 본인 후기/비교글 무손실(게이트 전후 동수) OK")


if __name__ == "__main__":
    test_hearsay_clause_present()
    test_direct_perception_contrast_present()
    test_overcorrection_guard_present()
    test_deterministic_firsthand_gate()
    test_candidate_span_gate_drops_shopping_lists()
    test_candidate_span_gate_keeps_terse_ownership_ratings()
    test_single_syllable_ratings_survive_the_name_restatement_gate()
    test_candidate_gate_is_not_an_axis()
    test_doc_shape_unchanged_by_hardening()
    test_live_hearsay_dropped()
    print("\n전언 하드닝 테스트 통과 ✅")
