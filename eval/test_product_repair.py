# -*- coding: utf-8 -*-
"""제품명 귀속 복구 게이트 — 유령 제거와 **진짜 제품 보존**의 균형.

이 파일이 지키는 건 두 문장이고, 둘은 서로 반대 방향으로 당긴다:
  · 캡션의 스펙 줄(풀조합·향료)이 제품명이 되면 안 된다.
  · 그렇다고 1층에 아직 없는 **진짜 제품**을 유령 취급해 흡수·삭제하면 안 된다.
한쪽으로만 세게 당기면 반대쪽이 조용히 부서진다 — 실제로 개발 중 양쪽 다 한 번씩 부쉈다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_product_repair`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag import extract                        # noqa: E402

# 실제 수집된 캡션(2026-08-07 인스타). 형태를 바꾸지 말 것 — 회귀는 늘 실물 형태에서 났다.
CAP_SPECLINE = ("#슬라임지나 #빠코볼\n\n아마존 우드 점토 / 코코넛과자향\n\n"
                "이제야 처음 만져보는 빠코볼..!! 향이 너무 맛있었당")
CAP_TWO_PRODUCTS = ("#슬라임지나 #빠코볼\n빠코폼이랑 빠코볼은 재구매 여러번 했는데요,,, "
                    "저는 예전부터 빠코폼 파였는데, 오랜만이라 그런지 빠코볼도 존잼이었음")
CAP_GENERIC = "#슬라임지나 #빠코볼\n빅말차쿠키디 만졌던 기억\n#슬라임리뷰 #슬라임영상"
GINA = {"market": "슬라임지나", "market_word": "지나", "handle": "slime_gina_",
        "aliases": ["슬지나", "쿨라임"]}
EXCL = extract.market_tag_exclusions(GINA)
L1 = {"빠코볼", "푸딩크런키"}                          # 지나 1층 제품(불완전한 게 정상)


def _resolve(name, text, taken=()):
    return extract.resolve_product_name(name, text, exclude=EXCL,
                                        known_products=L1, taken=taken)


def test_glue_and_scent_lines_become_the_hashtagged_product():
    """풀조합·향료가 제품명으로 올라오면 캡션 해시태그의 진짜 제품으로 되돌린다."""
    for phantom in ("아마존 우드 점토", "코코넛과자향"):
        got, why = _resolve(phantom, CAP_SPECLINE)
        assert got == "빠코볼", f"{phantom} → {got} ({why})"
    print("✓ 풀조합·향료 → 해시태그 제품으로 복구 OK")


def test_market_tag_never_becomes_a_product():
    """마켓 태그(`#슬라임지나`)는 어떤 경우에도 제품이 되지 않는다(AC6 회귀)."""
    got, _why = _resolve("아마존 우드 점토", CAP_SPECLINE)
    assert got != "슬라임지나" and got != "지나", f"마켓 태그가 제품이 됐다: {got}"
    print("✓ 마켓 태그는 제품 후보에서 제외 OK")


def test_hashtagged_name_is_kept_even_when_absent_from_layer1():
    """제품명이 캡션 해시태그면 `specs` 에 없어도 유지한다(①).

    ⛔ `known_products` 에 있는지로 판정하도록 되돌리지 말 것 — 1층은 프로필 액터가 최신
      ~12글만 주므로 **구조적으로 불완전**하다. `specs` 부재는 '제품 아님'이 아니라 '미수집'이다.
    """
    cap = "#머머슬라임 #레몬커드쉘도넛 #앵두찜콩\n둘 다 좋았어요"
    excl = extract.market_tag_exclusions({"market": "머머슬라임", "market_word": "머머"})
    got, why = extract.resolve_product_name("앵두찜콩", cap, exclude=excl, known_products=set())
    assert (got, why) == ("앵두찜콩", "keep"), f"1층에 없다고 버렸다: {got} ({why})"
    print("✓ 해시태그면 1층 부재와 무관하게 유지 OK")


def test_a_real_second_product_is_not_absorbed_into_the_tagged_one():
    """같은 글이 이미 그 제품 행을 갖고 있으면 다른 이름을 거기로 흡수하지 않는다.

    ⛔ 이 케이스가 실제로 두 번 깨졌다(2026-08-07):
      1차 — 가드가 없어 `빠코폼` 이 `빠코볼` 로 **개명**됐다. 비교 후기의 한 축이 사라진다.
      2차 — 가드를 넣었더니 이번엔 `None` 으로 **비웠다**. 맞는 이름을 지운 건 더 나쁘다.
    정답은 **건드리지 않는 것**이다. 판단 근거가 없는 자리이므로 최소 개입한다.
    """
    got, why = _resolve("빠코폼", CAP_TWO_PRODUCTS, taken=["빠코볼"])
    assert got == "빠코폼", f"진짜 제품이 사라졌다: {got} ({why})"
    assert why == "keep_distinct"
    print("✓ 같은 글의 별개 제품은 개명도 삭제도 하지 않음 OK")


def test_generic_community_tags_never_become_products():
    """광역 태그(`#슬라임리뷰`)가 제품명이 되면 안 된다.

    실측 회귀: 한글 `슬라임리뷰` 가 `GENERIC_TAGS` 에 없어(영어 `slimereview` 만 있었다)
    `빅말차쿠키디 → 슬라임리뷰` 로 복구될 뻔했다. 유령을 다른 유령으로 바꾼 셈.
    """
    got, _why = _resolve("빅말차쿠키디", CAP_GENERIC, taken=["빠코볼"])
    assert got not in ("슬라임리뷰", "슬라임영상"), f"광역 태그가 제품이 됐다: {got}"
    print("✓ 광역 커뮤니티 태그는 제품 후보 아님 OK")


def test_dcinside_input_without_hashtags_is_untouched():
    """해시태그 없는 입력(디시)은 무변경 — 이 게이트는 인스타 전용이다(AC7)."""
    got, why = _resolve("한글과자한줌", "ㅂㅉ 한줌 비교글인데 한글과자가 더 좋았음")
    assert (got, why) == ("한글과자한줌", "no_tags"), f"디시 입력을 건드렸다: {got} ({why})"
    print("✓ 해시태그 없는 소스는 무변경 OK")


def test_ambiguous_multi_product_post_holds_instead_of_guessing():
    """1층과 일치하는 후보가 0개거나 2개 이상이면 보류한다 — 지어내지 않는다(AC5)."""
    cap = "#슬라임지나 #빠코볼 #푸딩크런키\n본문에 없는 이름"
    got, why = _resolve("정체불명", cap)
    assert got is None and why == "hold_ambiguous", f"찍었다: {got} ({why})"
    print("✓ 후보 모호하면 보류 OK")


def test_two_phantoms_from_one_post_fold_into_one_row():
    """한 글이 유령 2개를 내면 복구 후 **1행으로 접힌다** — 이중 계상 방지(AC3).

    접지 않으면 유령 2행이 진짜 제품 2행이 될 뿐이고, 한 사람의 한 의견이 두 번 세어진다.
    `criterion_stats` 가 행을 세어 다수/소수를 정하므로 그대로 편향 집계 오염이다.
    """
    doc = {"reviews": [
        {"mentioned_product": "아마존 우드 점토", "overall": {"summary": "좋아요"},
         "texture": {"sentiment": "pos"}},
        {"mentioned_product": "코코넛과자향", "overall": {"summary": "좋아요"}},
    ]}
    out = extract.repair_product_names(doc, CAP_SPECLINE, exclude=EXCL, known_products=L1)
    names = [r["mentioned_product"] for r in out["reviews"]]
    assert names == ["빠코볼"], f"접히지 않았다: {names}"
    print("✓ 같은 글의 유령 2개 → 1행 접기 OK")


def test_fold_keeps_the_richer_item():
    """접을 때는 평가가 더 많이 찬 항목을 남긴다 — 내용이 적은 쪽으로 덮이면 안 된다."""
    doc = {"reviews": [
        {"mentioned_product": "코코넛과자향", "overall": {"summary": "좋아요"}},
        {"mentioned_product": "아마존 우드 점토", "overall": {"summary": "좋아요"},
         "texture": {"sentiment": "pos"}, "scent": {"sentiment": "pos"}},
    ]}
    out = extract.repair_product_names(doc, CAP_SPECLINE, exclude=EXCL, known_products=L1)
    assert len(out["reviews"]) == 1
    assert out["reviews"][0].get("texture"), "내용이 적은 항목이 남았다"
    print("✓ 접기 생존자는 더 많이 찬 항목 OK")


def test_hold_items_are_not_folded_together():
    """보류(None)끼리는 접지 않는다 — 서로 다른 제품일 수 있어 합치면 다른 의견이 한 건이 된다."""
    cap = "#슬라임지나 #빠코볼 #푸딩크런키\n본문"
    doc = {"reviews": [{"mentioned_product": "정체불명A"}, {"mentioned_product": "정체불명B"}]}
    out = extract.repair_product_names(doc, cap, exclude=EXCL, known_products=L1)
    assert len(out["reviews"]) == 2, f"보류분이 접혔다: {out['reviews']}"
    assert all(r["mentioned_product"] is None for r in out["reviews"])
    print("✓ 보류분은 접지 않음 OK")


# ---------------------------------------------------------------- ③′ 레지스트리 폴백
# 1층(`specs`)은 캡션이 두꺼운 제품만 담는다(제품성 게이트: 네 칸 전부 null 이면 드롭).
# 제품 후보 레지스트리는 판매자 피드 전량의 해시태그라 훨씬 넓다(실측 408행 대 약 2,200후보).
# ③이 1층에서 **0건**일 때만 그 넓은 쪽을 본다 — 순서가 뒤집히면 잡음이 1층을 이긴다.
REG = {"빠코볼", "푸딩크런키", "빅말차쿠키디", "키위스쿱"}   # 지나 레지스트리(1층의 상위집합)


def test_registry_breaks_a_tie_layer1_cannot():
    """후보 둘 다 1층에 없고 그중 **하나만** 레지스트리에 있으면 그쪽으로 복구한다(③′).

    이게 없으면 보류(④)로 떨어져 후기 한 건이 제품 없이 남는다 — 1층이 구조적으로
    불완전한 동안(프로필 액터 ~12글 창) 흔한 경우다.
    """
    cap = "#슬라임지나 #빅말차쿠키디 #꼼픽\n본문에 없는 이름"
    got, why = extract.resolve_product_name("정체불명", cap, exclude=EXCL,
                                            known_products=L1, known_fallback=REG)
    assert (got, why) == ("빅말차쿠키디", "registry_tiebreak"), f"{got} ({why})"
    print("✓ ③′ 1층이 못 짚는 타이를 레지스트리가 가른다 OK")


def test_registry_never_overrides_a_layer1_decision():
    """⛔ **합집합 금지 회귀.** 1층이 정확히 하나를 짚었으면 레지스트리는 개입하지 않는다.

    두 집합을 하나로 합치면 1층 단독 판정이던 글이 레지스트리 쪽 후보가 끼어들어
    `hold_ambiguous` 로 **퇴화**한다 — 있던 판정이 사라지는 방향의 회귀라 화면에서는
    '제품 없는 후기'로만 보이고 원인이 안 보인다. 2단은 판정을 더하기만 한다(단조).
    """
    cap = "#슬라임지나 #빠코볼 #키위스쿱\n본문에 없는 이름"      # 빠코볼만 1층, 둘 다 레지스트리
    solo, why_solo = extract.resolve_product_name("정체불명", cap, exclude=EXCL,
                                                  known_products=L1)
    both, why_both = extract.resolve_product_name("정체불명", cap, exclude=EXCL,
                                                  known_products=L1, known_fallback=REG)
    assert (solo, why_solo) == ("빠코볼", "l1_tiebreak"), f"전제가 깨졌다: {solo} ({why_solo})"
    assert (both, why_both) == (solo, why_solo), \
        f"레지스트리가 1층 판정을 바꿨다: {both} ({why_both})"
    print("✓ ③′ 는 1층 판정을 못 뒤집는다(단조) OK")


def test_registry_holds_when_it_matches_more_than_one():
    """레지스트리도 둘 이상이면 보류다 — 넓은 목록일수록 '하나'를 요구해야 한다.

    레지스트리는 사람이 승격한 목록이 아니라 **유도된 후보**라 잡음이 섞인다
    (실측: 늪지의 `액괴`·`워터글루`·`jigglyslime`). 다수 일치를 추측으로 메우면
    그 잡음이 제품명이 된다.
    """
    cap = "#슬라임지나 #빅말차쿠키디 #키위스쿱\n본문에 없는 이름"
    got, why = extract.resolve_product_name("정체불명", cap, exclude=EXCL,
                                            known_products=L1, known_fallback=REG)
    assert (got, why) == (None, "hold_no_l1_match"), f"둘 다 걸렸는데 골랐다: {got} ({why})"
    print("✓ ③′ 다중 일치는 보류 OK")


def test_registry_does_not_resurrect_dropped_rules():
    """폴백은 ①②와 마켓/광역 태그 배제보다 **뒤**다 — 앞 규칙을 되살리지 않는다.

    레지스트리에 마켓명이 잘못 들어와도 `exclude` 가 먼저 걸러야 하고(`#슬라임지나`),
    해시태그가 아예 없는 입력(디시)은 여전히 무변경이어야 한다.
    """
    dirty = REG | {"슬라임지나", "슬라임리뷰"}
    got, _why = extract.resolve_product_name("아마존 우드 점토", CAP_SPECLINE, exclude=EXCL,
                                             known_products=set(), known_fallback=dirty)
    assert got == "빠코볼", f"마켓 태그가 레지스트리를 타고 제품이 됐다: {got}"

    got2, why2 = extract.resolve_product_name("한글과자한줌", "ㅂㅉ 한줌 비교글", exclude=EXCL,
                                              known_products=set(), known_fallback=dirty)
    assert (got2, why2) == ("한글과자한줌", "no_tags"), f"디시 입력을 건드렸다: {got2} ({why2})"
    print("✓ ③′ 는 앞 규칙(배제·해시태그 없음)을 되살리지 않는다 OK")


if __name__ == "__main__":
    test_glue_and_scent_lines_become_the_hashtagged_product()
    test_market_tag_never_becomes_a_product()
    test_hashtagged_name_is_kept_even_when_absent_from_layer1()
    test_a_real_second_product_is_not_absorbed_into_the_tagged_one()
    test_generic_community_tags_never_become_products()
    test_dcinside_input_without_hashtags_is_untouched()
    test_ambiguous_multi_product_post_holds_instead_of_guessing()
    test_two_phantoms_from_one_post_fold_into_one_row()
    test_fold_keeps_the_richer_item()
    test_hold_items_are_not_folded_together()
    test_registry_breaks_a_tie_layer1_cannot()
    test_registry_never_overrides_a_layer1_decision()
    test_registry_holds_when_it_matches_more_than_one()
    test_registry_does_not_resurrect_dropped_rules()
    print("\n제품명 귀속 복구 오프라인 테스트 통과 ✅")
