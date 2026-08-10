# -*- coding: utf-8 -*-
"""제품명에 섞여 들어온 **마켓 접두** 분리 게이트(`linking.split_market_prefix` → `link`).

추출 프롬프트는 '제품명에 마켓 초성을 섞지 마라'고 **말만** 하고 강제가 없었다.
실측(2026-08-10, 아모스갤 813행): 순수 마켓 표기가 제품명인 행 21건 + `마켓+제품명` 11건.
그 이름으로는 `specs` 조인도, 같은 제품의 다른 조각과의 집계도 영영 안 된다.

이 파일이 지키는 문장 넷이고, 넷 다 반대 방향으로 당긴다:
  · 접두는 뗀다. 그리고 버리지 않고 **그 항목의 마켓 신호**로 쓴다.
  · 원문이 마켓을 말했으면 접두가 그걸 덮지 않는다(근거가 약한 쪽이 이기면 안 된다).
  · **완성형 음절은 초성으로 환원하지 않는다** — `포도`가 푸딩이 되면 진짜 제품명이 사라진다.
  · 접두가 충돌하면 마켓은 보류다. 갈린 증거는 제품명으로도, 역인덱스로도 못 편다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_market_prefix`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag import linking                       # noqa: E402


def _kb() -> linking.KB:
    """가짜 KB — 디스크 KB 를 읽지 않는다(게이트가 데이터 변경에 흔들리면 안 된다).

    `ㅁㅁ` 를 공유하는 두 마켓을 일부러 넣는다: 접두 충돌 → 보류 경로가 여기 핵심 회귀다.
    `봄`(초성 `ㅂ`)은 **완성형 오탐**을 만들 수 있는 자리다 — `배` 의 초성이 `ㅂ` 라,
    환원을 허용하면 `배` 가 봄의 제품이 된다.
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
SPEC_PAIRS = [("지나", "빠코볼")]
INV = linking.build_market_inversion(SPEC_PAIRS, {}, excludes=[])


def _split(name):
    return linking.split_market_prefix(name, KB)


def _link(mentioned_market, product, **kw):
    return linking.link(mentioned_market, product, kb=KB, **kw)


# ------------------------------------------------------------------ 순수 분리 규칙
def test_jamo_prefix_splits_with_and_without_space():
    """`ㅈㄴ 아몬드바나나브레드` 도 `ㅈㄴ아몬드바나나브레드` 도 같게 갈린다.

    호환 자모는 완성형 제품명의 일부가 될 수 없어 붙여 써도 경계가 모호하지 않다.
    """
    assert _split("ㅈㄴ 아몬드바나나브레드") == ("ㅈㄴ", "아몬드바나나브레드")
    assert _split("ㅈㄴ아몬드바나나브레드") == ("ㅈㄴ", "아몬드바나나브레드")
    print("✓ 자모 접두 분리(공백 유무 무관) OK")


def test_bare_market_token_leaves_no_product():
    """이름 전체가 마켓 표기면 제품은 **보류**다 — 행은 남고 이름만 빈다."""
    assert _split("ㅈㄴ") == ("ㅈㄴ", None)
    assert _split("늪지") == ("늪지", None)           # 표면형도 마찬가지
    print("✓ 맨몸 마켓 표기 → 제품 보류 OK")


def test_surface_prefix_needs_a_space():
    """음절 접두는 **공백이 있을 때만** 본다 — 임의로 가르면 진짜 제품명이 반토막 난다."""
    assert _split("늪지 디폼클리어") == ("늪지", "디폼클리어")
    assert _split("봄날의배달부") == (None, "봄날의배달부")
    print("✓ 음절 접두는 공백 경계에서만 OK")


def test_complete_syllables_are_never_reduced_to_choseong():
    """⛔ **완성형 음절을 초성으로 환원하지 않는다.** 이 파일에서 가장 비싼 회귀다.

    실측(2026-08-10, 아모스갤): 환원을 허용하던 구현이 `포도`를 푸딩(`ㅍㄷ`)으로,
    `배`를 봄(`ㅂ`)으로, `육쩐 젤쿠칩` 의 `육쩐` 을 연찌(`ㅇㅉ`)로 잡아 **멀쩡한 제품명을
    마켓 접두로 잘라 냈다**. 손실이 화면에 안 보인다(‘제품 없는 후기’로만 보인다).
    """
    assert _split("배") == (None, "배"), "완성형 `배` 가 봄(ㅂ)으로 환원됐다"
    assert _split("육쩐 젤쿠칩") == (None, "육쩐 젤쿠칩"), "완성형 접두가 초성으로 환원됐다"
    assert _split("나중") == (None, "나중"), "완성형 `나중` 이 늪지(ㄴㅈ)로 환원됐다"
    print("✓ 완성형 음절 초성 환원 금지(진짜 제품명 보존) OK")


def test_unknown_prefix_is_left_alone():
    """KB 에 없는 접두는 안 뗀다 — 모르는 건 건드리지 않는 게 최소 개입이다."""
    assert _split("요아곰 밀키크림파르페") == (None, "요아곰 밀키크림파르페")
    assert _split("ㅇㅍㅋ 든든장작") == (None, "ㅇㅍㅋ 든든장작")   # KB 부재 자모
    print("✓ KB 부재 접두 무변경 OK")


# ------------------------------------------------------------------ link() 배선
def test_prefix_becomes_the_market_when_none_was_stated():
    """접두는 버릴 잡음이 아니라 **그 항목의 마켓 신호**다 — 떼면서 승격한다."""
    r = _link(None, "ㅈㄴ 아몬드바나나브레드")
    assert r.market == "지나", r
    assert r.product == "아몬드바나나브레드", r
    assert r.abstained is False
    print("✓ 접두 → 마켓 승격 OK")


def test_prefix_market_carries_its_own_confidence():
    """전용 확신도가 **롤백의 유일한 열쇠**다 — 직접 매칭과 같은 값이면 되돌릴 수 없다.

    `reason` 은 DB 에 안 남고 `market_confidence` 만 남는다. `ㅈㄴ` 처럼 마켓 초성과 부사
    ('존나')가 겹치는 표기가 실재하므로 되돌릴 길은 반드시 있어야 한다.
    """
    cho = _link(None, "ㅈㄴ 아몬드바나나브레드")
    sur = _link(None, "늪지 디폼클리어")
    assert cho.market_confidence == linking.PREFIX_CONF_CHOSEONG, cho
    assert sur.market_confidence == linking.PREFIX_CONF_SURFACE, sur
    # 직접 매칭과 **다른 값**이어야 사후에 갈라낼 수 있다.
    direct = _link("늪지", "디폼클리어")
    assert direct.market_confidence not in linking.PREFIX_CONFS, direct
    print("✓ 접두 마켓 전용 확신도(직접 매칭과 구분) OK")


def test_stated_market_beats_the_prefix():
    """원문이 마켓을 말했으면 접두가 덮지 않는다 — 제품명은 그래도 갈린다."""
    r = _link("봄", "ㅈㄴ 아몬드바나나브레드")
    assert r.market == "봄", f"접두가 말한 마켓을 덮었다: {r}"
    assert r.product == "아몬드바나나브레드", r
    print("✓ 명시 마켓 우선(접두는 제품명만 정리) OK")


def test_colliding_prefix_holds_the_market_but_still_splits():
    """충돌 접두(`ㅁㅁ`)는 마켓 보류 — 그래도 제품명에서는 뗀다.

    갈린 증거는 하나로 안 모인다. 하지만 `ㅁㅁ 한줌` 을 통째로 제품명으로 두면 어느
    마켓의 `한줌` 과도 조인이 안 되므로, 떼는 것과 채우는 것은 별개 판단이다.
    """
    r = _link(None, "ㅁㅁ 한줌")
    assert r.product == "한줌", r
    assert r.market is None and r.abstained is True, r
    assert "충돌" in r.reason, r.reason
    print("✓ 충돌 접두: 마켓 보류 + 제품명 분리 OK")


def test_colliding_prefix_does_not_fall_through_to_the_inversion():
    """⛔ 충돌로 보류된 행에 역인덱스를 태우지 말 것.

    `mentioned_market` 이 충돌했을 때 안 건드리는 것과 **같은 이유**다 — 갈린 증거를
    제품명 소유관계로 조용히 뒤집는 게 된다. 접두도 '원문이 마켓을 말했다'에 포함된다.
    """
    r = _link(None, "ㅁㅁ 빠코볼", inversion=INV)
    assert r.product == "빠코볼", r
    assert r.market is None, f"충돌 접두 행을 역인덱스가 채웠다: {r}"
    # 접두가 아예 없으면 역인덱스는 정상 동작한다(기능을 죽인 게 아니라는 대조군).
    ok = _link(None, "빠코볼", inversion=INV)
    assert ok.market == "지나" and ok.market_confidence == linking.INVERSION_CONF_SPEC, ok
    print("✓ 충돌 접두 → 역인덱스 미발동(대조군은 정상) OK")


def test_bare_market_name_survives_as_a_row_with_no_product():
    """맨몸 마켓 표기는 제품이 None 이 되고 **행은 남는다**(미언급 → null, 드롭 아님)."""
    r = _link(None, "ㅈㄴ")
    assert r.product is None, r
    assert r.market == "지나", r
    print("✓ 맨몸 마켓 표기: 제품 None · 마켓 승격 OK")


def test_aliases_are_looked_up_after_the_prefix_is_stripped():
    """약칭 사전은 **뗀 뒤의 이름**으로 찾는다 — 안 그러면 접두 붙은 행에만 안 먹는다."""
    r = _link(None, "ㅈㄴ 몽땅", aliases={"몽땅": "사과몽땅"})
    assert r.product == "사과몽땅", r
    print("✓ 접두 제거 후 약칭 정규화 OK")


def test_clean_names_are_untouched():
    """접두가 없는 이름은 한 글자도 안 바뀐다."""
    r = _link("늪지", "빠코볼")
    assert r.product == "빠코볼" and r.market == "늪지", r
    assert r.market_confidence not in linking.PREFIX_CONFS, r
    print("✓ 무접두 이름 무변경 OK")


# ------------------------------------------------------------------ 항목 단위 마켓
def _link_post(doc):
    return linking.link_post(doc, kb=KB)


def test_item_market_beats_the_document_market():
    """항목이 자기 마켓을 말했으면 **그게 이긴다** — 글 단위 값은 폴백일 뿐이다.

    실측 근거: 아모스갤 94행이 KB 마켓을 2개 이상 언급하는 조각에 달려 있다. 그런 글에서
    글 단위 값 하나를 전 항목에 쓰면 나머지 제품이 통째로 남의 마켓 후기가 된다.
    ⚠️ 글 단위 값은 **스레드 상속**으로 채워졌을 수 있다(형제 조각에서 온 값). 그래서
      항목 자신의 증거보다 약하다.
    """
    doc = {"market": "봄", "reviews": [
        {"mentioned_product": "든든장작", "mentioned_market": "늪지"},
        {"mentioned_product": "약과볼"},                       # 미언급 → 글 값 폴백
    ]}
    got = [(r.market, r.product) for r in _link_post(doc)]
    assert got == [("늪지", "든든장작"), ("봄", "약과볼")], got
    print("✓ 항목 마켓 우선 · 미언급은 글 값 폴백 OK")


def test_item_market_is_additive_for_instagram():
    """인스타 캡션은 이 칸을 안 채운다 — 전부 null 이면 예전과 **똑같이** 동작해야 한다."""
    doc = {"market": "지나", "reviews": [
        {"mentioned_product": "빠코볼", "mentioned_market": None},
        {"mentioned_product": "빠코폼"},                       # 칸 자체가 없는 옛 문서
    ]}
    got = [(r.market, r.product) for r in _link_post(doc)]
    assert got == [("지나", "빠코볼"), ("지나", "빠코폼")], got
    print("✓ 항목 마켓 미기재 시 기존 동작 보존(인스타 가산적) OK")


def test_document_market_never_suppresses_the_prefix_hint():
    """글 단위 값이 있어도 **제품명 접두**가 그보다 앞선다 — 둘 다 항목 밖 vs 항목 안이다.

    ⛔ 이 순서를 뒤집으면 스레드 상속이 항목 자신의 표기를 이겨 1.1 에서 고친 사고가
      개체연결 쪽으로 그대로 되살아난다.
    """
    doc = {"market": "봄", "reviews": [{"mentioned_product": "ㅈㄴ 아몬드바나나브레드"}]}
    r = _link_post(doc)[0]
    assert (r.market, r.product) == ("지나", "아몬드바나나브레드"), r
    assert r.market_confidence == linking.PREFIX_CONF_CHOSEONG, r
    print("✓ 제품명 접두 > 글 단위 값 OK")


# ------------------------------------------------------------------ 추출→개체연결 합성
# ⛔ 이 절이 이 파일에서 가장 비싼 자리다. 접두 분리와 비제품 단어 게이트는 **개별적으로는
#   둘 다 맞는데 합성 순서가 틀리면 조용히 반대로 동작한다.** 아래 두 케이스가 실제로 그렇게
#   깨져 있었다(2026-08-10): 단어 게이트가 추출 층에서 먼저 돌아 `ㅇㅊ` 를 '자모뿐인 이름'
#   으로 비웠고, 그 행은 스레드 도장 마켓을 0.95 로 물려받았다 — 되돌릴 표식 없는 오귀속.
#   `link`/`split_market_prefix` 를 따로따로 보는 위 테스트들은 **전부 통과한 채로** 그랬다.
class _StubLLM:
    """조각마다 지정된 제품명을 돌려주는 가짜 LLM — 네트워크 경계 대체."""

    def __init__(self, products, market=None):
        self.products, self.market = products, market

    def complete(self, prompt, **_kw):
        return {"docs": [{"source_id": "S0", "market": self.market, "shipping_cs": None,
                          "flags": {"toxic": False},
                          "reviews": [{"mentioned_product": p,
                                       "firsthand_evidence": "만져봤는데"}
                                      for p in self.products]}]}


def _extract_then_link(products, thread_market=None):
    """수집 경로 그대로: `extract_collected` → `link_post`. 반환 `[(market, product, conf)…]`."""
    from slime_rag import extract as X
    from slime_rag.sources.base import RawReview

    text = " ".join(products) + " 만져봤는데"
    raw = RawReview(text=text, platform="dcinside", raw_title="t", meta={"type": "post"},
                    url="https://gall.dcinside.com/mgallery/board/view/?id=amos&no=1")
    doc = X.extract_collected([raw], _StubLLM(products, thread_market))[0][1]
    return [(r.market, r.product, r.market_confidence)
            for r in linking.link_post(doc, kb=KB)]


def test_extract_to_link_composition_recovers_the_market_from_a_bare_token():
    """`ㅈㄴ` 하나만 제품명으로 온 조각은 **마켓 지나 + 제품 보류**가 되어야 한다.

    ⛔ 회귀 내용: 추출 층이 비제품 단어 게이트를 먼저 걸면 `ㅈㄴ` 가 '자모뿐인 이름'으로
      비워지고, 그 뒤 `link` 는 볼 게 없어 **스레드 도장 마켓**을 그대로 물려준다.
      틀린 마켓이 직접 매칭 확신도(0.95)로 남아 되돌릴 표식조차 없다 — NULL 보다 나쁘다.
    """
    got = _extract_then_link(["ㅈㄴ"], thread_market="늪지")
    assert got == [("지나", None, linking.PREFIX_CONF_CHOSEONG)], got
    print("✓ 합성: 맨몸 마켓 표기 → 마켓 복구(스레드 도장 미상속) OK")


def test_extract_to_link_composition_gates_the_split_remainder():
    """`ㅈㄴ 클리어` 는 마켓 지나 + **종류어라 제품 보류**가 되어야 한다.

    반대 방향 회귀: 단어 게이트가 접두 분리보다 **앞**에 돌면 `ㅈㄴ 클리어` 전체는 종류어가
    아니라 통과하고, 뗀 뒤의 `클리어` 는 게이트를 이미 지나쳐 **제품으로 남는다**.
    """
    got = _extract_then_link(["ㅈㄴ 클리어"], thread_market="늪지")
    assert got == [("지나", None, linking.PREFIX_CONF_CHOSEONG)], got
    print("✓ 합성: 접두 제거 후 나머지에도 단어 게이트 적용 OK")


def test_extract_to_link_composition_keeps_real_products():
    """⚠️ 과잉 차단 회귀 — 진짜 제품명은 스레드 도장 마켓과 함께 그대로 남는다."""
    got = _extract_then_link(["빠코볼"], thread_market="늪지")
    assert got == [("늪지", "빠코볼", 0.95)], got
    print("✓ 합성: 진짜 제품명 보존(마켓은 글 값) OK")


# ------------------------------------------------------------------ 약칭 적용 범위
def test_aliases_are_never_applied_globally():
    """⛔ 약칭 사전은 **마켓별 스코프**다 — 전역 적용은 알려진 과잉 병합 경로다.

    같은 약칭이 마켓마다 다른 제품을 가리킬 수 있다. 쿨라임 사고(쿨라임=지나 별칭)를
    전역으로 쓰면 다른 마켓의 동명 제품까지 지나 제품으로 접힌다.

    강제 방법 둘:
      ① 정본 파일은 **마켓 키 dict** 다(평평한 `{약칭: 정규명}` 이 아니다).
      ② `link` 는 그 파일을 **직접 읽지 않는다** — 마켓을 아는 호출부가 좁혀서 주입한다.
         그래서 마켓이 보류된 조각에는 약칭이 **적용되지 않는 게 정상**이다. 해결책은
         전역 적용이 아니라 마켓 커버리지를 올리는 것(Phase 1).
    """
    table = linking.load_product_aliases()
    for market, sub in table.items():
        assert isinstance(sub, dict), f"{market} 항목이 마켓별 표가 아니다: {type(sub)}"

    # 주석에 이름이 **언급**되는 건 정상이므로(바로 위 금지 주석이 그렇다) 문자열 검색이
    # 아니라 AST 로 **호출**을 찾는다.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(linking.link).lstrip())
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "load_product_aliases" not in called, \
        "link 이 약칭 사전을 직접 읽는다 — 마켓 스코프가 함수 안에서 무너진다"

    # 마켓이 보류돼도 제품명은 그대로 남는다(약칭 미적용 ≠ 제품 소실).
    r = _link(None, "몽땅")
    assert r.market is None and r.product == "몽땅", r
    # 마켓을 아는 호출부가 좁혀서 주입하면 정상 적용된다.
    r2 = _link("늪지", "몽땅", aliases={"몽땅": "사과몽땅"})
    assert r2.product == "사과몽땅", r2
    print("✓ 약칭은 마켓 스코프 · 전역 적용 없음 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
