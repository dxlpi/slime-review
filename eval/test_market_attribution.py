# -*- coding: utf-8 -*-
"""
디시 2층 **귀속** 게이트 — 마켓 스프레이 · 초성 제품명 · 근거 품질.
계획 `.omc/plans/2026-08-10-dcinside-attribution-repair.md` AC1~AC9 (전부 오프라인·무과금).

원문 172스레드(948조각)를 재파싱해 대조한 결과 네 가지 귀속 결함이 실측됐다. 이 파일이
막는 건 그중 코드로 강제 가능한 셋이다.

① **마켓이 스레드 전체에 뿌려졌다.** `extract_collected` 가 조각 자기 마켓을 **덮어쓰고**,
   권위 없는 **댓글**의 마켓을 스레드 전체로 전파했다. 실측: 마켓이 붙은 스레드 95/95(100%)가
   단일 마켓이고, `빈짱` 21행 중 19행이 **한 스레드**에서 나왔다(한 댓글의 `ㅂㅉ`).
   → AC1(덮어쓰기 금지) · AC2(댓글 폴백 제거). **상속 자체는 유지**한다 — 댓글 단독 추출은
   'ㅂ슬라임' 등으로 흔들려 개체연결을 막고, 구매 후기 스레드에선 글의 마켓이 맞다.
   바뀌는 건 **권위의 출처와 덮어쓰기 여부**뿐이다.

② **마켓 초성이 제품명 자리에 들어갔다.** 프롬프트는 금지만 하고 코드 강제가 없었다.
   실측: 순수 초성 제품명 26행/15종, `초성+제품명` 12행/10종.
   → AC3~AC5(`split_market_prefix`) · AC6(충돌 보류 우회 금지) · AC9(항목 마켓 우선).
   접두는 버릴 잡음이 아니라 **그 항목의 마켓 신호**다 — 그래서 떼면서 힌트로 회수한다.

④ **첨삭/장바구니 글이 중립 후기 행이 됐다.** `drop_hearsay_reviews` 3중 검사를
   **제품명 자체**가 근거로 통과했다(`firsthand_evidence` == 제품명 정확 일치 30행).
   → AC7(폐기) · AC8(**과잉 폐기 회귀**).

⚠️ ③(입도 중복)은 여기 없다 — **자동 병합하지 않기로** 한 항목이라 게이트할 코드가 없다.
   같은 조각의 `빠코볼`/`미니빠코볼` 은 진짜 다른 제품이고, 포함관계만으로 접으면 유령 제품
   복구 때 `빠코폼` 을 지웠던 실패를 반대 방향으로 반복한다. 후보만 뽑아 사람 시드로 넘긴다
   (`pipeline.product_containment_candidates`).

실행:  python -m eval.test_market_attribution   (repo 루트에서 · LLM·DB·네트워크 미접촉)
"""
from __future__ import annotations

import re

from slime_rag import extract as X
from slime_rag import linking
from slime_rag.sources.base import RawReview

_POST_URL = "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=888"
_S_RE = re.compile(r"^\[S(\d+)\]\s?(.*)$", re.M)


# ---------------------------------------------------------------- 픽스처
def _kb() -> linking.KB:
    """실 KB(14마켓). 접두 인정 대상이 **명부에 실재하는 마켓뿐**이라는 게 규칙의 핵심이라,
    가짜 명부로 대체하면 검증하려던 성질이 사라진다."""
    return linking.load_kb()


def _conflict_kb() -> linking.KB:
    """초성이 겹치는 2마켓만 담은 명부 — AC6 전용.

    실 KB 에는 `ㅁㅁ`(머머) 단일 매칭뿐이라 충돌 케이스가 없다. 충돌을 **만들어서라도**
    검증해야 하는 이유: 접두 분리가 새 경로라, 기존 `link()` 의 보류 규칙을 우회하지
    않는다는 보장이 여기서만 나온다.
    """
    return linking.KB({"markets": [
        {"market": "머머슬라임", "market_word": "머머", "handle": "a", "handles_alt": [],
         "aliases": [], "choseong": "ㅁㅁ", "choseong_aliases": [], "products": []},
        {"market": "미미슬라임", "market_word": "미미", "handle": "b", "handles_alt": [],
         "aliases": [], "choseong": "ㅁㅁ", "choseong_aliases": [], "products": []},
    ]})


class MarketLLM:
    """네트워크 경계 대체 — 조각별로 **지정한 market** 을 돌려준다.

    `[S<n>]` 순서로 `markets[n]` 을 싣고, 제품명·근거는 본문에서 만든다. 근거를 본문에서
    떼어 오는 건 필수다 — `drop_hearsay_reviews` 2번째 겹이 '원문에 없는 인용'을 버리므로,
    지어낸 근거를 쓰면 항목이 통째로 사라져 마켓 검증 자체가 성립하지 않는다.
    """

    def __init__(self, markets: list[str | None]):
        self.markets = markets
        self.calls: list[str] = []

    def complete(self, prompt, *, system=None, schema=None, model=None,
                 max_tokens=4096, effort=None, label=""):
        self.calls.append(prompt)
        docs = []
        for sid, body in _S_RE.findall(prompt):
            docs.append({"source_id": f"S{sid}", "market": self.markets[int(sid)],
                         "shipping_cs": None,
                         "reviews": [{"mentioned_product": (body.split() or [""])[0],
                                      "firsthand_evidence": body[:15]}],
                         "flags": {"toxic": False}})
        return {"docs": docs}


def _thread(texts: list[str]) -> list[RawReview]:
    """[글, 댓글, 댓글…] 텍스트 → RawReview 목록. meta 는 수집기가 싣는 키만 넣는다.

    ⚠️ 글의 meta 에 `thread_no` 를 넣지 않는다 — 수집기가 안 싣기 때문이다.
      픽스처가 없는 키를 넣으면 `thread_key` 회귀가 통과해 버린다(2026-08-07 실제 사고).
    """
    post = RawReview(text=texts[0], url=_POST_URL, platform="dcinside",
                     raw_title=texts[0][:20], meta={"type": "post"})
    comments = [
        RawReview(text=t, url=f"{_POST_URL}#cmt", platform="dcinside",
                  meta={"type": "comment", "parent_no": "888", "parent_title": texts[0][:20]})
        for t in texts[1:]
    ]
    return [post] + comments


# ---------------------------------------------------------------- AC3~AC5 접두 분리
def test_ac3_choseong_prefix_is_split():
    """AC3 — `ㅇㅉ 쿠키슈크림번` → (`ㅇㅉ`, `쿠키슈크림번`)."""
    assert linking.split_market_prefix("ㅇㅉ 쿠키슈크림번", _kb()) == ("ㅇㅉ", "쿠키슈크림번")
    # 실측에 나온 다른 모양들도 같은 규칙으로 갈린다.
    kb = _kb()
    assert linking.split_market_prefix("ㅁㅁㄴ 새튀반", kb) == ("ㅁㅁㄴ", "새튀반")
    assert linking.split_market_prefix("봄 누텔라 바이트", kb) == ("봄", "누텔라 바이트")
    # 공백 없이 붙은 자모 접두 — 호환 자모는 완성형 제품명의 일부가 될 수 없어 경계가 명확하다.
    assert linking.split_market_prefix("ㅈㄴ아몬드바나나브레드", kb) == ("ㅈㄴ", "아몬드바나나브레드")
    print("✓ AC3 마켓 초성 접두 분리 OK")


def test_ac4_whole_name_market_holds_product():
    """AC4 — 전체가 마켓이면 제품명은 **보류**(None). 지어내지 않는다."""
    assert linking.split_market_prefix("ㅂㅇㅍ", _kb()) == ("ㅂㅇㅍ", None)
    print("✓ AC4 전체가 마켓 → 제품명 보류 OK")


def test_ac5_unknown_prefix_is_never_stripped():
    """AC5 — KB 에 없는 접두는 **안 뗀다**. 임의 초성 스트립이 이 함수의 금지사항이다.

    `요아곰 밀키크림파르페` 를 가르면 `요아곰` 이 마켓인 척 되고 제품명은 반토막이 난다.
    """
    kb = _kb()
    assert linking.split_market_prefix("요아곰 밀키크림파르페", kb)[1] == "요아곰 밀키크림파르페"
    assert linking.split_market_prefix("요아곰 밀키크림파르페", kb)[0] is None
    # 접두가 없는 평범한 제품명은 무변경.
    assert linking.split_market_prefix("빠코볼", kb) == (None, "빠코볼")
    # 빈 입력은 양쪽 None — 호출부가 `mentioned_product` 를 그대로 넘길 수 있어야 한다.
    assert linking.split_market_prefix(None, kb) == (None, None)
    assert linking.split_market_prefix("   ", kb) == (None, None)
    print("✓ AC5 KB 부재 접두 무변경 + 빈 입력 안전 OK")


# ---------------------------------------------------------------- AC6 충돌 보류 우회 금지
def test_ac6_prefix_hint_does_not_bypass_abstain():
    """AC6 — 접두 힌트가 나와도 초성 충돌이면 `link()` 가 보류해 market 은 None.

    새 경로가 기존 확신도 규칙을 **우회하지 않는다**는 게 요점이다. 접두 분리는 후보만
    만들고, 확정은 여전히 `link()` 한 곳에서 한다.
    """
    kb = _conflict_kb()
    hint, product = linking.split_market_prefix("ㅁㅁ 새튀반", kb)
    assert (hint, product) == ("ㅁㅁ", "새튀반"), f"힌트 분리 실패: {(hint, product)}"

    doc = {"market": None, "reviews": [{"mentioned_product": "ㅁㅁ 새튀반",
                                        "firsthand_evidence": "새튀반 조음"}]}
    lk = linking.link_post(doc, kb=kb)[0]
    assert lk.market is None, f"충돌인데 market 이 확정됐다: {lk.market}"
    assert lk.abstained is True
    assert len(lk.candidates) == 2, f"후보 2개가 보고돼야: {lk.candidates}"
    assert lk.product == "새튀반", f"접두는 떼되 제품명은 남아야: {lk.product!r}"
    print("✓ AC6 초성 충돌 시 힌트는 나오되 보류 유지 OK")


# ---------------------------------------------------------------- AC9 항목 마켓 우선
def test_ac9_item_market_beats_doc_market():
    """AC9 — `reviews[].mentioned_market` 이 `doc['market']` 보다 우선한다.

    한 댓글이 여러 마켓을 나열하는 추천/첨삭 스레드가 실측상 지배적인데, 문서 단위 한 칸으로는
    **원리적으로 표현 불가**하다(`ㅇㅍㅋ 든든장작 ㅋㅈ 밀카버 ㅍㅍㄹ 애플크림머핀`).
    항목 칸은 문서 칸을 **덮어쓰는 게 아니라 좁힌다** — 비면 문서 값으로 떨어진다.
    """
    kb = _kb()
    doc = {"market": "ㅂㅉ", "reviews": [
        {"mentioned_market": "ㅇㅉ", "mentioned_product": "쿠키슈크림번",
         "firsthand_evidence": "쿠키슈크림번 조음"},
        {"mentioned_market": None, "mentioned_product": "한줌",
         "firsthand_evidence": "한줌 좋았음"},
    ]}
    a, b = linking.link_post(doc, kb=kb)
    assert a.market == "연찌", f"항목 마켓이 문서 마켓을 이겨야: {a.market}"
    assert b.market == "빈짱", f"항목 마켓이 비면 문서 마켓으로: {b.market}"
    print("✓ AC9 항목 마켓 > 문서 마켓, 비면 폴백 OK")


def test_prefix_hint_fills_only_when_item_market_is_empty():
    """접두 힌트는 **판정을 더하기만** 한다(단조) — 항목 마켓이 있으면 건드리지 않는다.

    `derive_product_registry` 2단 타이브레이크와 같은 규칙이다. 힌트가 기존 판정을 덮으면
    있던 판정이 사라지는 방향이라 화면엔 원인이 안 보인다.
    """
    kb = _kb()
    doc = {"market": None, "reviews": [
        {"mentioned_market": "ㅂㅉ", "mentioned_product": "ㅇㅉ 쿠키슈크림번",
         "firsthand_evidence": "쿠키슈크림번 조음"}]}
    lk = linking.link_post(doc, kb=kb)[0]
    assert lk.market == "빈짱", f"명시된 항목 마켓이 접두 힌트에 밀렸다: {lk.market}"
    assert lk.product == "쿠키슈크림번", f"접두는 그래도 떼야: {lk.product!r}"
    print("✓ 접두 힌트는 항목 마켓이 빈 자리에만 들어간다 OK")


def test_whole_name_market_yields_no_product_row_name():
    """제품명이 통째로 마켓이면 `product` 는 None 으로 나간다 — `ㅂㅇㅍ` 행이 제품이 되면 안 된다."""
    doc = {"market": None, "reviews": [{"mentioned_product": "ㅂㅇㅍ",
                                        "firsthand_evidence": "ㅂㅇㅍ 조음"}]}
    lk = linking.link_post(doc, kb=_kb())[0]
    assert lk.product is None, f"마켓 이름이 제품명으로 남았다: {lk.product!r}"
    assert lk.market == "베이퍼", "그 이름은 마켓 신호로 회수돼야"
    print("✓ 전체가 마켓인 제품명 → product 보류 + 마켓 회수 OK")


# ---------------------------------------------------------------- AC1·AC2 마켓 상속
def test_ac1_piece_market_is_not_overwritten():
    """AC1 — 조각이 자기 마켓을 뽑았으면 스레드 마켓이 **덮지 않는다**.

    실측 결함: `ㅍㅅㅌㄹ` 을 제대로 뽑은 댓글이 스레드의 `빈짱` 으로 지워졌다.
    """
    texts = ["ㅂㅉ 한줌 후기임 진짜 좋았음", "ㅍㅅㅌㄹ 빠코볼 만져봤는데 조음"]
    llm = MarketLLM(["ㅂㅉ", "ㅍㅅㅌㄹ"])
    pairs = X.extract_collected(_thread(texts), llm)
    markets = [doc.get("market") for _, doc in pairs]
    assert markets == ["ㅂㅉ", "ㅍㅅㅌㄹ"], f"조각 자기 마켓이 덮였다: {markets}"
    print("✓ AC1 조각 자기 마켓 보존(덮어쓰기 금지) OK")


def test_ac2_comment_market_never_spreads():
    """AC2 — 글에 마켓이 없으면 **어떤 댓글의 마켓도** 다른 조각으로 전파되지 않는다.

    스레드 마켓의 권위는 **글 본문뿐**이다. 댓글의 마켓은 그 댓글 작성자의 것이지 스레드의
    것이 아니다 — `빈짱` 19행이 정확히 이 폴백에서 나왔다.
    """
    texts = ["질문임 뭐 살까 고민중인데 추천 좀", "ㅂㅉ 한줌 만져봤는데 조음", "빠코볼 진짜 좋았음"]
    llm = MarketLLM([None, "ㅂㅉ", None])
    pairs = X.extract_collected(_thread(texts), llm)
    markets = [doc.get("market") for _, doc in pairs]
    assert markets == [None, "ㅂㅉ", None], f"댓글 마켓이 전파됐다: {markets}"
    print("✓ AC2 댓글 마켓 폴백 제거(전파 금지) OK")


def test_post_market_still_fills_empty_pieces():
    """**상속 자체는 유지**한다 — 글이 마켓을 가지면 비어 있는 조각을 채운다.

    이걸 같이 고정하지 않으면 AC1·AC2 를 만족시키는 가장 쉬운 방법이 '상속을 통째로 제거'가
    되는데, 그러면 댓글 단독 추출의 흔들림(`ㅂ슬라임`)이 개체연결을 다시 막는다.
    """
    texts = ["ㅂㅉ 한줌 후기임 진짜 좋았음", "빠코볼 만져봤는데 조음"]
    llm = MarketLLM(["ㅂㅉ", None])
    pairs = X.extract_collected(_thread(texts), llm)
    assert [doc.get("market") for _, doc in pairs] == ["ㅂㅉ", "ㅂㅉ"]
    print("✓ 글 마켓 → 빈 조각 상속 유지 OK")


def test_inheritance_count_is_reported():
    """상속이 실제로 몇 건 걸렸는지 **드러낸다**(무음 금지).

    이 값이 없으면 규칙을 바꿨을 때 커버리지 변화의 출처를 사후에 못 가른다 — 이 저장소가
    `llm_calls_saved` 와 `llm_calls_saved_by_dedup` 을 가른 것과 같은 이유다.
    """
    texts = ["ㅂㅉ 한줌 후기임 진짜 좋았음", "빠코볼 만져봤는데 조음", "새튀반 조음 개좋았음"]
    counts: dict = {}
    X.extract_collected(_thread(texts), MarketLLM(["ㅂㅉ", None, None]), counts=counts)
    assert counts.get("market_inherited") == 2, f"상속 건수 보고 누락/오차: {counts}"

    counts2: dict = {}
    X.extract_collected(_thread(texts), MarketLLM([None, None, None]), counts=counts2)
    assert counts2.get("market_inherited") == 0, f"상속이 없으면 0 이어야: {counts2}"
    print("✓ market_inherited 카운터 노출 OK")


# ---------------------------------------------------------------- AC7·AC8 근거 품질
def test_ac7_evidence_that_is_just_the_product_name_is_dropped():
    """AC7 — 근거가 제품명을 다시 적은 것뿐이면 폐기.

    `firsthand_evidence='바질토마토블렌디드'` 는 본인 경험의 근거가 아니라 제품명 반복이다.
    실측 30행이 이 모양으로 3중 검사를 통과했다 — 제품명은 당연히 원문에 있고 전언 표지도
    없어서, 앞의 세 겹 어디에도 안 걸린다.
    """
    doc = {"reviews": [{"mentioned_product": "바질토마토블렌디드",
                        "firsthand_evidence": "바질토마토블렌디드"}]}
    out = X.drop_hearsay_reviews(doc, "바질토마토블렌디드 살까 고민중")
    assert out["reviews"] == [], f"제품명 반복 근거가 살아남았다: {out['reviews']}"
    print("✓ AC7 제품명 반복 근거 폐기 OK")


def test_ac8_product_name_plus_evaluation_survives():
    """AC8 **과잉 폐기 회귀** — 제품명+평가어는 정상 근거다.

    컷 기준은 '제품명 제외 **잔여 2자 미만**'이지 '제품명 포함'이 아니다. 실측상 제품명 정확
    일치는 30행이지만 제품명+4자 이내는 116행 — **후자를 자르면 진짜 후기가 죽는다.**
    """
    cases = [("새튀반", "새튀반 좋았고"), ("카피바라", "카피바라 조음"),
             ("빠코볼", "빠코볼 개좋았음")]
    for product, ev in cases:
        doc = {"reviews": [{"mentioned_product": product, "firsthand_evidence": ev}]}
        out = X.drop_hearsay_reviews(doc, f"{ev} 진짜 만족함")
        assert len(out["reviews"]) == 1, f"정상 근거가 폐기됐다: {product} / {ev}"
    print("✓ AC8 제품명+평가어 생존(과잉 폐기 방지선) OK")


def test_purchase_intent_evidence_is_dropped():
    """구매 예정 표지가 근거면 폐기 — 첨삭·장바구니 글은 후기가 아니다.

    ⚠️ 어휘는 `relevance_rules.LEXICON` 에 산다. `extract.py` 에 하드코딩하면 슬랭이 바뀔 때
      고칠 자리가 둘로 갈린다(이 저장소의 규칙: 어휘는 데이터, 강제는 코드).
    """
    from slime_rag import relevance_rules as rules
    assert "purchase_intent" in rules.LEXICON, "구매 예정 어휘가 LEXICON 에 없다"

    for ev, text in [("빠코볼 장바구니에 담았", "빠코볼 장바구니에 담았는데 살말?"),
                     ("한줌 살까 고민중", "한줌 살까 고민중임"),
                     ("첨삭 좀 해줘 빠코볼", "첨삭 좀 해줘 빠코볼 어떰")]:
        doc = {"reviews": [{"mentioned_product": "빠코볼", "firsthand_evidence": ev}]}
        out = X.drop_hearsay_reviews(doc, text)
        assert out["reviews"] == [], f"구매 예정 근거가 살아남았다: {ev!r}"
    print("✓ 구매 예정 표지 근거 폐기 + 어휘 위치 OK")


def test_existing_three_layers_still_hold():
    """기존 3중 검사 회귀 — 4번째 겹을 붙이면서 앞의 셋이 느슨해지지 않았는가."""
    # ① 빈 근거
    assert X.drop_hearsay_reviews(
        {"reviews": [{"mentioned_product": "빠코볼", "firsthand_evidence": None}]},
        "빠코볼 좋았음")["reviews"] == []
    # ② 원문에 없는 인용(지어낸 근거)
    assert X.drop_hearsay_reviews(
        {"reviews": [{"mentioned_product": "빠코볼", "firsthand_evidence": "쫀득하고 좋았음"}]},
        "빠코볼 샀음")["reviews"] == []
    # ③ 근거 자체가 전언
    assert X.drop_hearsay_reviews(
        {"reviews": [{"mentioned_product": "빠코볼", "firsthand_evidence": "다들 좋다고 함"}]},
        "빠코볼 다들 좋다고 함")["reviews"] == []
    print("✓ 기존 3중 검사(빈 근거·지어낸 인용·전언) 유지 OK")


if __name__ == "__main__":
    test_ac3_choseong_prefix_is_split()
    test_ac4_whole_name_market_holds_product()
    test_ac5_unknown_prefix_is_never_stripped()
    test_ac6_prefix_hint_does_not_bypass_abstain()
    test_ac9_item_market_beats_doc_market()
    test_prefix_hint_fills_only_when_item_market_is_empty()
    test_whole_name_market_yields_no_product_row_name()
    test_ac1_piece_market_is_not_overwritten()
    test_ac2_comment_market_never_spreads()
    test_post_market_still_fills_empty_pieces()
    test_inheritance_count_is_reported()
    test_ac7_evidence_that_is_just_the_product_name_is_dropped()
    test_ac8_product_name_plus_evaluation_survives()
    test_purchase_intent_evidence_is_dropped()
    test_existing_three_layers_still_hold()
    print("\n모든 마켓 귀속 테스트 통과 (LLM·DB·네트워크 미접촉)")
