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


def test_excluded_tag_is_not_protected_as_a_distinct_product():
    """보존 가드가 **마켓·광역 태그까지** 지키면 안 된다.

    실측 회귀(2026-08-09 라이브 색인): 캡션 `#슬라임지나 #빠코볼` 에서 같은 글의 다른 행이
    `빠코볼` 을 이미 claim 하자, `슬라임지나` 가 `keep_distinct` 로 살아남아 **마켓 이름이
    제품 행**이 됐다(`꼼픽` 도 같은 경로로 새 행이 생겼다).
    ⚠️ 그러면서도 **진짜 2번째 제품은 계속 지켜야 한다** — 이 가드가 원래 막던 사고
      (`빠코폼` 이 `빠코볼` 로 흡수·삭제되는 것)는 그대로 막혀 있어야 한다. 양방향이다.
    """
    for noise in ("슬라임지나", "지나", "슬라임리뷰"):
        got, why = _resolve(noise, "#슬라임지나  #빠코볼", taken=["빠코볼"])
        assert (got, why) == (None, "hold_excluded_name"), f"{noise} → {got} ({why})"

    kept, why = _resolve("빠코폼", CAP_TWO_PRODUCTS, taken=["빠코볼"])
    assert (kept, why) == ("빠코폼", "keep_distinct"), \
        f"진짜 2번째 제품이 제외 규칙에 휩쓸렸다: {kept} ({why})"
    print("✓ 보존 가드: 마켓·광역 태그는 보류 · 진짜 2번째 제품은 유지 OK")


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


# ---------------------------------------------------------------- 비제품 라벨 게이트(B)
# 제품이 아니라 **판매 형식**을 가리키는 말(`비매품`·`이번차수`)이 제품명 칸에 들어온 실측
# 11행 / 8개 이름(2026-08-10)이 계기다. 유령 제품과 같은 방향의 실패지만 원인이 다르다 —
# 저건 캡션의 스펙 줄을 들어올린 것이고, 이건 원문이 실제로 그렇게 부른 것이다.
#
# ⚠️ 이 블록의 절반은 **과잉 차단 회귀**다. 계획서는 `비매` 계열을 통째로 지우자고 했는데,
#   실측해 보니 `연찌비매17`·`푸딩비매품`·`웨이즈1월비매` 는 **1층 `specs` 에 실재하는
#   제품**이었다(specs 64행이 이 모양). 연찌·웨이즈는 비매품에 번호를 붙여 해시태그로 판다.
#   부분일치로 지웠으면 그 제품들이 통째로 사라졌을 것이고, 그 손실은 화면에 안 보인다.
KNOWN = {"연찌비매17", "푸딩비매품", "웨이즈1월비매", "웨이즈할로윈비매3", "빠코볼", "나비매듭"}


def test_bare_labels_are_never_products():
    """맨몸 라벨은 `known_products` 와 무관하게 비제품이다 — 무엇도 식별하지 못하는 이름이다."""
    for label in ("비매", "비매품", "비매5", "비매품 1번", "이번비매", "저번 비매",
                  "이번차수", "차수", "3차수", "랜덤박스", "랜박"):
        assert extract.is_non_product_label(label, KNOWN), f"라벨을 놓쳤다: {label}"
        assert extract.is_non_product_label(label), f"known 없이도 걸러야 한다: {label}"
    print("✓ 맨몸 라벨 → 비제품 OK")


def test_real_products_that_merely_contain_a_label_word_survive():
    """부분일치 금지 회귀 — 진짜 제품명 안에 `비매`·`차수` 가 들어갈 수 있다.

    ⛔ `'비매' in name` 으로 되돌리지 말 것. 이 손실은 유령 제품과 반대 방향이라
      **화면에 흔적이 없다** — 후기가 그냥 제품 없이 사라진다.
    """
    for real in ("나비매듭", "말차수플레", "홍차수플레", "빠코볼", "구아검지글리"):
        assert not extract.is_non_product_label(real, KNOWN), f"진짜 제품을 지웠다: {real}"
        assert not extract.is_non_product_label(real), f"known 없이도 살아야 한다: {real}"
    print("✓ 라벨어를 품은 진짜 제품명 생존 OK")


def test_numbered_seller_series_are_real_products():
    """`연찌비매17` 류는 **1층에 실재하는 제품**이다 — 판매 형식이 아니라 제품 식별자다.

    실측(2026-08-10): `specs` 64행이 이 모양이다(연찌 57 · 웨이즈 6 · 푸딩 1).
    계획서가 예상하지 못한 반례이고, 이 테스트가 그 실측을 코드로 붙잡아 둔다.
    """
    for real in ("연찌비매17", "푸딩비매품", "웨이즈1월비매", "웨이즈할로윈비매3"):
        assert not extract.is_non_product_label(real, KNOWN), f"1층 제품을 라벨로 봤다: {real}"
    print("✓ 번호 붙은 판매자 비매품 시리즈는 제품 OK")


def test_a_numbered_bare_label_that_is_a_real_product_survives():
    """`비매품50` 은 **연찌가 해시태그로 파는 실제 제품**이다(레지스트리 실측).

    ⛔ '라벨 + 숫자면 무조건 비제품'으로 되돌리지 말 것 — 그 규칙이 정확히 이 이름을 지웠다.
      `비매5`(가짜)와 `비매품50`(진짜)은 **구조가 같아서** 구조로는 못 가른다. 증거로만 갈린다.
    ⚠️ 그러면서 맨몸 `비매품` 은 계속 지워져야 한다 — 레지스트리에 판매자가 실제로 단
      `#비매품` 태그가 후보로 올라와 있어서, 증거를 물으면 맨몸 라벨이 되살아난다.
    """
    known = KNOWN | {"비매품50", "비매품"}          # 레지스트리엔 맨몸 `비매품` 도 있다(잡음)
    assert not extract.is_non_product_label("비매품50", known), \
        "번호 붙은 진짜 비매품 제품을 지웠다"
    assert extract.is_non_product_label("비매품", known), \
        "맨몸 라벨이 레지스트리 잡음으로 되살아났다"
    assert extract.is_non_product_label("비매5", known), "가짜는 계속 걸러야 한다"
    print("✓ 비매품50(진짜) 생존 · 맨몸 비매품(잡음) 제거 OK")


def test_the_evidence_set_must_not_be_narrowed_per_market_or_per_thread():
    """같은 이름은 어느 경로에서 봐도 **같은 판정**이어야 한다 — 좁힌 집합이 그걸 깬다.

    ⛔ 실제로 이 방향으로 두 번 잘못 짰다: 인스타 경로는 마켓별 집합을, 디시 경로는
      **스레드 본문에 등장한** 후보만 넘기고 있었다. 좁힌 집합은 비어 있지 않으므로
      페일세이프(③)가 안 걸리고, 진짜 제품이 '증거 있는데 불일치'로 읽혀 지워진다.
      백필은 전량을 보므로 같은 이름에 경로마다 반대 판정이 붙는다.
    """
    full = {"푸딩비매품", "빠코볼"}
    narrowed = {"빠코볼"}                            # 그 스레드/마켓에서만 보이는 좁은 집합
    assert not extract.is_non_product_label("푸딩비매품", full)
    assert extract.is_non_product_label("푸딩비매품", narrowed), \
        "좁힌 집합이 진짜 제품을 지운다는 사실 자체가 이 테스트의 전제다"
    # 그래서 호출부는 전량을 넘겨야 한다 — 시그니처로 그 의도를 고정한다.
    import inspect
    assert "label_known" in inspect.signature(extract.repair_product_names).parameters, \
        "라벨 증거는 타이브레이크 인자와 **별도 인자**여야 한다(섞으면 마켓별로 좁혀진다)"
    assert "label_known" in inspect.signature(extract.extract_thread).parameters
    assert "label_known" in inspect.signature(extract.extract_collected).parameters
    print("✓ 라벨 증거 집합은 전용 인자(좁힘 금지) OK")


def test_qualified_labels_need_evidence_to_be_dropped():
    """수식된 라벨은 `known_products` 가 **있을 때만** 지운다(페일세이프).

    `베이퍼비매` 와 `연찌비매17` 은 구조가 같다(마켓 + 라벨). 구조만으로는 못 가르므로
    1층/레지스트리 증거에 묻는다. 증거가 없으면 **건드리지 않는다** — 증거 없이 지우는
    쪽이 화면에 안 보이는 손실이라 더 나쁘다(`enforce_product_vocab` ③과 같은 규칙).
    """
    for qualified in ("베이퍼비매", "교동 지글리 비매"):
        assert extract.is_non_product_label(qualified, KNOWN), f"놓쳤다: {qualified}"
        assert not extract.is_non_product_label(qualified), \
            f"증거 없이 지웠다: {qualified}"
    print("✓ 수식된 라벨: 증거 있을 때만 제거 OK")


def test_the_gate_nulls_the_name_but_keeps_the_review():
    """제품명만 비우고 **후기 항목은 남긴다** — 1급 규칙은 '미언급 → null' 이지 드롭이 아니다.

    그 조각의 배송·CS 는 마켓 축(ADR-0015)에 그대로 들어가야 한다. 항목을 버리면
    주문 축 집계에서 조용히 빠진다.
    """
    doc = {"reviews": [{"mentioned_product": "비매품 1번", "overall": {"summary": "별로"}},
                       {"mentioned_product": "빠코볼", "overall": {"summary": "좋음"}}]}
    n = extract.drop_non_product_labels(doc, KNOWN)
    assert n == 1, n
    assert len(doc["reviews"]) == 2, f"후기 항목이 버려졌다: {doc['reviews']}"
    assert doc["reviews"][0]["mentioned_product"] is None
    assert doc["reviews"][0]["overall"]["summary"] == "별로", "내용이 손상됐다"
    assert doc["reviews"][1]["mentioned_product"] == "빠코볼"
    print("✓ 라벨은 null · 후기 항목은 보존 OK")


def test_the_gate_runs_before_the_hashtag_gate_so_dcinside_is_covered():
    """B2 회귀 — 해시태그가 없는 입력(디시)에도 걸려야 한다.

    ⛔ `product_hashtags(text)` 의 조기 반환 **뒤로** 옮기지 말 것. 그 자리에 있어서
      `비매품 1번` 이 살아남았다(실측 id=53). 인스타 전용 게이트 하나에 규칙을 얹으면
      '모든 소스에 적용된다'가 조용히 거짓이 된다.
    """
    doc = {"reviews": [{"mentioned_product": "비매품 1번", "overall": {"summary": "별로"}}]}
    out = extract.repair_product_names(doc, "해시태그 하나도 없는 디시 본문",
                                       exclude=EXCL, known_products=L1, known_fallback=REG)
    assert out["reviews"][0]["mentioned_product"] is None, \
        f"디시 입력에 라벨 게이트가 안 돌았다: {out['reviews']}"
    print("✓ 해시태그 없는 소스에도 라벨 게이트 적용 OK")


def test_the_label_gate_does_not_disturb_normal_instagram_repair():
    """라벨 게이트가 앞에 붙어도 기존 복구 판정은 그대로다(회귀)."""
    doc = {"reviews": [{"mentioned_product": "아마존 우드 점토", "overall": {"summary": "좋아요"}}]}
    out = extract.repair_product_names(doc, CAP_SPECLINE, exclude=EXCL, known_products=L1)
    assert out["reviews"][0]["mentioned_product"] == "빠코볼", out["reviews"]
    print("✓ 라벨 게이트 추가 후에도 기존 복구 무변경 OK")


# ---------------------------------------------------------------- 보류분 말더듬 접기
def _held(**blocks) -> dict:
    return {"mentioned_product": None, **blocks}


def test_identical_held_items_fold_as_extractor_stutter():
    """내용이 **글자 하나까지 같은** 보류 항목은 한 건이다 — 추출기 말더듬 제거.

    실측: 한 조각이 `아쿠아 자몽 후르츠 프쿠 썸파` 를 두고 완전히 동일한 항목을 3개
    내보냈다. 이름이 없어 `UNIQUE(source, post_id, product)` 도 못 거른다 — Postgres 는
    NULL 을 서로 다른 값으로 보기 때문에 그대로 3행이 된다.
    """
    stutter = _held(texture={"feel": ["쫀득"], "sentiment": "pos", "evidence": "쫀득해"},
                    overall={"summary": "괜찮아요", "model_sentiment": "pos"},
                    firsthand_evidence="만져보니 쫀득")
    out = extract._fold_by_product([dict(stutter), dict(stutter), dict(stutter)])
    assert len(out) == 1, f"동일 보류 3건이 안 접혔다: {len(out)}"
    print("✓ 동일 내용 보류분 접기(말더듬 제거) OK")


def test_held_items_that_differ_in_any_block_stay_apart():
    """⛔ 내용이 다르면 **절대** 접지 않는다 — 이름 없는 두 항목은 서로 다른 제품일 수 있다."""
    base = dict(texture={"feel": ["쫀득"], "sentiment": "pos"},
                overall={"summary": "괜찮아요", "model_sentiment": "pos"})
    other_attr = {**base, "texture": {"feel": ["말랑"], "sentiment": "pos"}}
    other_overall = {**base, "overall": {"summary": "별로였어요", "model_sentiment": "neg"}}
    other_ev = {**base, "firsthand_evidence": "다른 문장"}
    for variant, why in ((other_attr, "속성 블록"), (other_overall, "총평"),
                         (other_ev, "근거 조각")):
        out = extract._fold_by_product([_held(**base), _held(**variant)])
        assert len(out) == 2, f"{why} 가 다른데 접혔다 — 다른 의견이 한 건이 됐다"
    print("✓ 내용이 다른 보류분은 분리 유지 OK")


def test_contentless_held_items_are_never_folded():
    """내용이 **하나도 없는** 보류 둘은 말더듬의 증거가 아니다 — 접지 않는다.

    이 자리엔 원래 이름(`정체불명A`/`정체불명B`)이 이미 안 남아 있어 구분할 재료가 없다.
    과소 집계는 과대 집계보다 알아채기 어렵다(`_fold_orders` 와 같은 판단).
    """
    out = extract._fold_by_product([_held(), _held()])
    assert len(out) == 2, f"빈 보류분이 접혔다: {out}"
    print("✓ 내용 없는 보류분 미접기 OK")


def test_named_fold_is_unaffected_by_the_held_rule():
    """이름 있는 항목의 접기 규칙(더 많이 찬 쪽 생존)은 그대로다."""
    thin = {"mentioned_product": "빠코볼"}
    rich = {"mentioned_product": "빠코볼", "texture": {"feel": ["쫀득"], "sentiment": "pos"},
            "overall": {"summary": "좋아요", "model_sentiment": "pos"}}
    out = extract._fold_by_product([thin, rich])
    assert len(out) == 1 and out[0] is rich, out
    print("✓ 이름 있는 접기 규칙 무변경 OK")


# ---------------------------------------------------------------- 비제품 '단어' 게이트
def test_type_words_are_never_products():
    """종류어는 제품명이 아니다(사용자 규칙) — **완전일치**로만 비운다.

    실측(2026-08-10, 아모스갤): `디폼` 3행 · `클리어` 3행 · `수수깡` 3행 · `빨대` 1행 ·
    `크런치` 1행 · `빈백` 1행이 제품 행이었다. 이 이름으로는 어느 `specs` 와도 안 조인된다.
    """
    for word in ("디폼", "클리어", "수수깡", "빨대", "빈백", "크런치", "폼볼", "지글리"):
        assert extract.is_non_product_word(word), f"종류어를 놓쳤다: {word}"
    print("✓ 종류어 완전일치 → 비제품 OK")


def test_glue_and_base_material_words_are_never_products():
    """풀·베이스 재료어도 제품이 아니다 — 캡션 스펙 줄이 제품명으로 들어올려진 자국이다."""
    for word in ("글루올", "택키", "아마존", "우드", "우마존", "생베", "점토", "화이트글루"):
        assert extract.is_non_product_word(word), f"재료어를 놓쳤다: {word}"
    print("✓ 풀·재료어 완전일치 → 비제품 OK")


def test_composites_containing_a_type_or_glue_word_survive():
    """⛔ **부분일치 금지 회귀.** 라벨 게이트(`나비매듭`)와 정확히 같은 실패 모드다.

    실측(2026-08-10, `specs` 제품명 1,980개): 종류어·재료어와 **완전히 같은** 제품명은
    0개인데, 그 단어를 **품은** 제품명은 16개다. 부분일치로 넓히면 그 16개가 통째로
    사라지고, 그 손실은 화면에 안 보인다(후기가 그냥 제품 없이 사라진다).
    """
    real_names = ("내리꽃디폼", "베이직우드폼", "베이직우드버터", "말차초코크런치바",
                  "허밍크런치", "오레오흑임자크런치", "크런치팝팝", "몽글클라우드",
                  "우드득건반", "믹스폼듀", "물젤리수수깡")
    for real in real_names:
        assert not extract.is_non_product_word(real), f"합성 제품명을 지웠다: {real}"
    # 증거를 **주입해도** 살아남아야 한다 — 이 이름들은 1층 `specs` 에 실재한다(실측
    # 2026-08-11: 위 11개 중 `물젤리수수깡` 만 1층 밖이고 나머지 10개는 `specs` 안).
    # 수식 갈래(`modified_suffixes`)가 증거를 묻는 자리라, 증거가 있는데도 지우면 그게 회귀다.
    for real in real_names:
        assert not extract.is_non_product_word(real, real_names), f"증거 있는 이름을 지웠다: {real}"
    print("✓ 종류어·재료어를 품은 합성 제품명 생존 OK")


def test_two_names_moved_out_of_the_survivor_list_by_human_review():
    """⚠️ **`디폼생베`·`디폼클리어` 는 2026-08-11 에 생존 목록에서 나갔다** — 조용히 빼지 않는다.

    둘 다 사람 검수가 **종류어**로 판정한 이름이고, 실측으로 1층 `specs` 에 **없다**
    (레지스트리 전용 — 해시태그 빈도로 유도된 후보라 잡음이 섞이는 층이다):
      · `디폼클리어` — `data/market_inversion_excludes.json` 의 `_rulings` 가 근거를 남겼다.
        늪지 게시물의 꼬리 분류 태그 무더기(`#클리어디폼 #디폼크런치 #디폼클리어 #클리어슬라임`)
        에 섞여 있었고 그 글의 실제 제품은 `#꼬도독닭발` 이며, 후기 쪽은 `ㅁㅁ`(머머)라고 썼다.
      · `디폼생베` — 446조각 전수 검수의 `TYPE_PARTS_WORDS`(디폼 + 생베, 둘 다 종류/재료어).
    이 케이스가 있는 이유: 생존 목록에서 이름을 지우기만 하면 다음 사람이 '왜 빠졌지' 를 모른 채
    되돌린다. 판정이 바뀌었다는 사실 자체를 게이트로 남긴다.
    """
    for word in ("디폼생베", "디폼클리어"):
        assert extract.is_non_product_word(word), f"사람 판정이 되돌려졌다: {word}"
    print("✓ 사람 검수로 이동한 두 이름 OK")


def test_forgotten_name_fragments_are_never_products():
    """`어쩌구/어쩌고/어쩍고` 는 '이름이 기억 안 난다'는 **명시적 표지**다 → 식별자가 아니다."""
    for frag in ("버블버블 어쩌구", "쿠키 바닐라 어쩌구", "캔디어쩌구",
                 "벨벳어쩍고", "생크림 뼈 어쩌구"):
        assert extract.is_non_product_word(frag), f"조각 이름을 놓쳤다: {frag}"
    print("✓ 조각 마커(어쩌구) → 비제품 OK")


def test_jamo_only_names_are_never_products():
    """자모뿐인 이름은 제품명일 수 없다.

    KB 에 있는 마켓 초성은 `linking.split_market_prefix` 가 이미 떼어 마켓으로 승격시킨다.
    여기 남는 건 KB **밖** 자모(실측: `ㅅㄱㄷ` 2행 · `ㅇㅍㅋ`·`ㅃㅇ`·`ㅇㄹ`·`ㅅㅈㄴ` 각 1행)인데,
    그것도 마켓 표기지 제품명이 아니다 — 어느 쪽이든 제품으로 색인될 값이 아니다.
    """
    for jamo in ("ㅅㄱㄷ", "ㅇㅍㅋ", "ㅃㅇ", "ㅇㄹ", "ㅅㅈㄴ", "ㅂㅇㅍ", "ㅁㅁㄴ"):
        assert extract.is_non_product_word(jamo), f"자모 이름을 놓쳤다: {jamo}"
    # 자모가 **섞인** 완성형 이름은 건드리지 않는다 — 거긴 접두 분리의 몫이다.
    assert not extract.is_non_product_word("ㅂㅇㅍ 빨대")
    assert not extract.is_non_product_word("ㅈㄴ아몬드바나나브레드")
    print("✓ 자모뿐인 이름 → 비제품 (혼합형은 무변경) OK")


def test_word_gate_nulls_the_name_but_keeps_the_review():
    """이름만 비우고 **행은 남긴다** — 그 조각의 배송·CS 는 마켓 축에 그대로 들어가야 한다."""
    doc = {"reviews": [{"mentioned_product": "디폼", "texture": {"sentiment": "pos"}},
                       {"mentioned_product": "빠코볼"}]}
    n = extract.drop_non_product_words(doc)
    assert n == 1, n
    assert len(doc["reviews"]) == 2, "게이트가 후기 항목을 버렸다(미언급 → null, 드롭 아님)"
    assert doc["reviews"][0]["mentioned_product"] is None
    assert doc["reviews"][0]["texture"] == {"sentiment": "pos"}, "평가 내용이 사라졌다"
    assert doc["reviews"][1]["mentioned_product"] == "빠코볼"
    print("✓ 단어 게이트: 이름만 null · 후기 항목 보존 OK")


def test_word_gate_reaches_dcinside_through_the_thread_path():
    """디시(해시태그 없음)에도 걸려야 한다 — 라벨 게이트가 같은 이유로 배치 경로에 산다."""
    doc = {"reviews": [{"mentioned_product": "수수깡"}]}
    extract.repair_product_names(doc, "해시태그 없는 디시 본문 수수깡 좋더라")
    assert doc["reviews"][0]["mentioned_product"] is None, \
        "해시태그 없는 소스에서 단어 게이트가 안 돌았다"
    print("✓ 해시태그 없는 소스에도 단어 게이트 적용 OK")


# ------------------------------------------------------ 디시 어휘 게이트 확장(계획 Phase 4)
def test_bare_vocabulary_asks_no_evidence():
    """**맨몸** 종류어·부속어·메타어는 증거를 묻지 않는다 — 물으면 되살아난다.

    실측(2026-08-11): `펄러비즈`·`점섞슬`·`액괴`·`슬랑이`·`할로윈` 5개가
    `known_product_names()` 에 들어 있는데 **전부 레지스트리(해시태그 유도 후보)뿐이고
    1층 `specs` 엔 0개**다. 레지스트리는 사람이 승격한 목록이 아니다.
    `is_non_product_label` ①이 판매자가 실제로 단 `#비매품` 태그 때문에 증거를 안 묻는 것과
    정확히 같은 자리다.
    """
    noisy_evidence = {"펄러비즈", "점섞슬", "액괴", "슬랑이", "할로윈"}
    for word in ("퐁말", "파츠", "플레잉", "첫굼", "미감", "슬켓", "유슬이",
                 "프링글스", "돌슬라임", *noisy_evidence):
        assert extract.is_non_product_word(word), f"맨몸 어휘를 놓쳤다: {word}"
        assert extract.is_non_product_word(word, noisy_evidence), \
            f"증거를 물어서 맨몸 어휘가 되살아났다: {word}"
    print("✓ 맨몸 어휘: 증거 무관 OK")


def test_modified_names_need_evidence_and_fail_safe_without_it():
    """**수식된** 이름은 1층/레지스트리가 모를 때만 비제품 — 증거 미주입이면 무변경.

    `enforce_product_vocab` ③·`is_non_product_label` ③과 같은 규칙이다: 증거 없이 지우는
    쪽이 화면에 안 보이는 손실이라 더 나쁘다.
    """
    assert not extract.is_non_product_word("초코디폼"), "증거 없이 수식형을 지웠다"
    assert not extract.is_non_product_word("별디폼"), "증거 없이 수식형을 지웠다"
    assert extract.is_non_product_word("초코디폼", {"빠코볼"}), "증거가 있는데 수식형을 못 지웠다"
    assert not extract.is_non_product_word("초코디폼", {"초코디폼"}), \
        "1층/레지스트리가 아는 이름을 지웠다"
    print("✓ 수식형: 증거 요구 + 페일세이프 OK")


def test_punctuation_wrapped_jamo_is_not_a_product():
    """`ㅋㅈ(ㅁㅁ)` — 마켓 표기를 괄호로 병기한 형태(실측 ROW#1324). 알맹이가 자모뿐이다.

    ⚠️ 이건 ④(자모뿐인 이름)의 좁은 확장이지 **구조 규칙의 일반화가 아니다**. 제목 감탄사
      (`존나좋다`)나 어절(`적구 가`)은 어휘로만 잡는다 — `와이풀 그린티` 같은 진짜
      띄어쓰기 제품명이 반례라서다.
    """
    for name in ("ㅋㅈ(ㅁㅁ)", "ㅅㄱㄷ", "ㅇㅍㅋ / ㅃㅇ"):
        assert extract.is_non_product_word(name), f"자모뿐인 이름을 놓쳤다: {name}"
    for name in ("와이풀 그린티", "4pm스낵", "UFO머핀"):
        assert not extract.is_non_product_word(name), f"진짜 제품명을 지웠다: {name}"
    print("✓ 구두점 감싼 자모 → 비제품 OK")


def test_word_gate_evidence_is_never_narrowed():
    """⛔ **재료를 마켓/스레드 스코프로 좁히지 말 것** — 같은 이름에 경로마다 다른 판정이 붙는다.

    `MarketInversion` 은 **마켓 스코프로 접혀 있고**(`_own_index` 가 유일소유 판정까지 끝낸
    dict) 정규화도 다르다(`_strip().lower()` vs `_norm_tag()`). 재사용하면
    `is_non_product_label` 독스트링이 못박은 금지를 정확히 위반한다 — 마켓을 아직 모르는
    조각(=디시의 절반)에서 수식 갈래가 전부 지워진다.
    재료는 `pipeline.known_product_names()` **한 벌**이다.
    """
    import inspect
    from slime_rag import linking, pipeline
    for fn in (linking.link, pipeline.dc_attribution_target, extract.extract_thread):
        for line in inspect.getsource(fn).splitlines():
            if "is_non_product_word" not in line or line.lstrip().startswith("#"):
                continue
            for narrowed in ("inversion", "MarketInversion", "market]", "by_market",
                             "thread", "spec["):
                assert narrowed not in line, \
                    f"{fn.__name__} 가 좁혀진 재료를 어휘 게이트에 넘긴다: {line.strip()}"
    # 수집 경로가 실제로 재료를 흘리는가 — 안 흘리면 게이트가 백필에서만 돈다.
    ing = inspect.getsource(pipeline.ingest_dcinside)
    assert "known_product_names" in ing, "디시 수집이 재료를 안 만든다"
    assert "known_products=label_known" in ing, \
        "`label_known` 이 `extract_collected` 까지만 간다 — 색인 경로에 안 닿는다"
    idx = inspect.getsource(pipeline.index.index_post)
    assert "known_products=known_products" in idx, "`index_post` 가 재료를 `link_post` 로 안 넘긴다"
    print("✓ 어휘 게이트 증거 비축소 + 배관 관통 OK")


def test_the_vocabulary_file_never_holds_line_words():
    """⛔ `LINE_WORDS`(뭉치·갈배·유자·진저…)는 **제품 라인/축약 지칭**이라 여기 넣으면 안 된다.

    그건 실재 제품의 약칭이라 별칭 사전(`data/product_aliases.json`) 소관이고, 여기 섞으면
    진짜 후기의 제품명이 지워진다 — 유령 제품과 반대 방향의, 더 알아채기 어려운 실패다.
    """
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "data" / "non_product_words.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    words = {w for k in ("type_parts", "meta", "market_notation") for w in data[k]}
    line_words = {"뭉치", "케이크", "허니", "빙하", "믹스", "불량", "유자", "크머", "레몬커드",
                  "말차뭉", "비뭉", "숭덩자바", "잘자바", "막걸리", "갈배", "아생케", "유폭찜",
                  "바토디", "마크시", "배", "자몽", "썸파", "물젤리", "진저", "맥플", "베플",
                  "플레이크"}
    assert not (words & line_words), f"제품 축약 지칭이 비제품 어휘에 들어갔다: {words & line_words}"
    # 수식 판정 꼬리는 2음절 이상 — `폼`·`슬` 을 넣으면 `베이직우드폼` 이 증거 대기로 넘어간다.
    assert all(len(s.replace(" ", "")) >= 2 for s in data["modified_suffixes"]), \
        "한 음절 꼬리가 들어갔다 — 레지스트리에 없는 진짜 제품이 지워진다"
    print("✓ 어휘 파일: 라인어 미포함 · 꼬리 2음절 이상 OK")


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
    test_excluded_tag_is_not_protected_as_a_distinct_product()
    test_bare_labels_are_never_products()
    test_real_products_that_merely_contain_a_label_word_survive()
    test_numbered_seller_series_are_real_products()
    test_qualified_labels_need_evidence_to_be_dropped()
    test_the_gate_nulls_the_name_but_keeps_the_review()
    test_the_gate_runs_before_the_hashtag_gate_so_dcinside_is_covered()
    test_the_label_gate_does_not_disturb_normal_instagram_repair()
    test_a_numbered_bare_label_that_is_a_real_product_survives()
    test_the_evidence_set_must_not_be_narrowed_per_market_or_per_thread()
    test_identical_held_items_fold_as_extractor_stutter()
    test_held_items_that_differ_in_any_block_stay_apart()
    test_contentless_held_items_are_never_folded()
    test_named_fold_is_unaffected_by_the_held_rule()
    test_type_words_are_never_products()
    test_glue_and_base_material_words_are_never_products()
    test_composites_containing_a_type_or_glue_word_survive()
    test_forgotten_name_fragments_are_never_products()
    test_jamo_only_names_are_never_products()
    test_word_gate_nulls_the_name_but_keeps_the_review()
    test_word_gate_reaches_dcinside_through_the_thread_path()
    test_two_names_moved_out_of_the_survivor_list_by_human_review()
    test_bare_vocabulary_asks_no_evidence()
    test_modified_names_need_evidence_and_fail_safe_without_it()
    test_punctuation_wrapped_jamo_is_not_a_product()
    test_word_gate_evidence_is_never_narrowed()
    test_the_vocabulary_file_never_holds_line_words()
    print("\n제품명 귀속 복구 오프라인 테스트 통과 ✅")
