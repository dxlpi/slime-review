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

    컷 기준은 '제품명을 빼면 **아무것도 안 남는다**'(`_EVIDENCE_MIN_RESIDUE = 1`)이지
    '제품명 포함'이 아니다. 실측상 제품명 정확 일치는 30행이지만 제품명+4자 이내는 116행 —
    **후자를 자르면 진짜 후기가 죽는다.**
    ⚠️ 임계를 2로 올리면 1음절 평점(`잭두콩 썸`)의 잔여가 1자라 8행이 죽는다 —
      그 회귀는 `eval/test_extract_hearsay.py` 가 지킨다.
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


def test_inherited_market_carries_its_own_confidence():
    """상속으로 채워진 마켓은 **전용 확신도**를 단다 — 행 단위 롤백의 유일한 열쇠.

    `counts["market_inherited"]` 는 런 집계라 '몇 건'만 알려 주고 **행을 못 짚는다.**
    상속분이 직접 매칭과 같은 0.95/0.85 로 들어가면, 나중에 오귀속이 드러나도
    `WHERE market_confidence = …` 로 골라낼 수 없다 — 이 저장소의 다른 채움 경로
    (PREFIX·INVERSION·BACKFILL)가 전부 전용 값을 갖는 이유와 같다.
    """
    kb = _kb()
    own = linking.link_post({"market": "ㅂㅉ", "reviews": [
        {"mentioned_product": "한줌", "firsthand_evidence": "한줌 조음"}]}, kb=kb)[0]
    # 제목이 그 마켓을 선언한 스레드여야 상속이 걸린다(§6.3 · AC12) — 아래 AC12 케이스가
    # 제목 없는 쪽을 따로 고정한다.
    inh = linking.link_post({"market": "ㅂㅉ", "_market_inherited": True,
                             "_thread_title": "ㅂㅉ 후기 몰아쓰기", "reviews": [
        {"mentioned_product": "한줌", "firsthand_evidence": "한줌 조음"}]}, kb=kb)[0]

    assert own.market == inh.market == "빈짱", (own.market, inh.market)
    assert inh.market_confidence == linking.INHERIT_CONF, \
        f"상속분이 전용 확신도를 안 달았다: {inh.market_confidence}"
    assert own.market_confidence != inh.market_confidence, \
        "조각 자신의 마켓과 상속분이 같은 값 — 사후에 못 가른다"
    assert linking.REASON_INHERIT in inh.reason, inh.reason

    # 항목이 자기 마켓을 말했으면 상속 표식이 있어도 그건 상속이 아니다.
    said = linking.link_post({"market": "ㅂㅉ", "_market_inherited": True,
                              "_thread_title": "ㅂㅉ 후기 몰아쓰기", "reviews": [
        {"mentioned_market": "ㅇㅉ", "mentioned_product": "한줌",
         "firsthand_evidence": "한줌 조음"}]}, kb=kb)[0]
    assert said.market == "연찌" and said.market_confidence != linking.INHERIT_CONF, \
        f"항목이 말한 마켓에 상속 표식이 잘못 붙었다: {said.market} {said.market_confidence}"
    print("✓ 상속 마켓 전용 확신도(행 단위 롤백 키) OK")


def test_fill_path_confidences_never_collide():
    """채움 경로별 확신도가 **서로 겹치지 않는다** — 겹치면 층별 롤백이 불가능해진다."""
    from slime_rag import pipeline

    vals = {"prefix_surface": linking.PREFIX_CONF_SURFACE,
            "prefix_choseong": linking.PREFIX_CONF_CHOSEONG,
            "inversion_spec": linking.INVERSION_CONF_SPEC,
            "inversion_registry": linking.INVERSION_CONF_REGISTRY,
            "inherit": linking.INHERIT_CONF,
            "piece_surface": linking.REPAIR_CONF_PIECE_SURFACE,
            "piece_choseong": linking.REPAIR_CONF_PIECE_CHOSEONG,
            "revert_sentinel": pipeline.REVERT_SENTINEL_CONF,
            **{f"backfill_{k}": v for k, v in pipeline.BACKFILL_CONFS.items()}}
    # 직접 매칭 두 값(표면형 0.95 · 초성 0.85)과도 겹치면 안 된다.
    vals["direct_surface"], vals["direct_choseong"] = 0.95, 0.85
    assert len(set(vals.values())) == len(vals), f"확신도 충돌: {vals}"
    assert len(vals) == 12, f"확신도 눈금 개수가 바뀌었다({len(vals)}) — 의도인지 확인할 것"
    assert linking.INHERIT_CONF > 0.6, "보류 임계 아래면 '채웠는데 보류'인 모순 행이 남는다"
    print(f"✓ 채움 경로 확신도 {len(vals)}개 전부 고유 OK")


# ------------------------------------------------------- AC10~AC13 귀속 우선순위(계획 Phase 3)
def _inv(pairs):
    return linking.build_market_inversion(pairs, {}, excludes=[])


def test_ac10_layer1_ownership_beats_thread_inheritance():
    """**AC10** — 항목이 마켓을 말하지 않고 상속만 있을 때, 1층 유일소유가 상속을 **이긴다**.

    감사 D2 18행이 정확히 이 순서 때문에 생겼다: `요구르팅` 의 1층 소유는 지나인데
    늪지 스레드 상속이 덮었다. 예전 코드는 `surface` 에 `fallback_market` 이 섞여 있어서
    상속이 있으면 역인덱스가 **아예 안 돌았다**.
    """
    kb = _kb()
    doc = {"market": "늪지", "_market_inherited": True, "_thread_title": "ㄴㅈ 후기",
           "reviews": [{"mentioned_product": "요구르팅"}]}
    r = linking.link_post(doc, kb=kb, inversion=_inv([("지나", "요구르팅")]))[0]
    assert r.market == "지나", f"상속이 1층 유일소유를 이겼다: {r.market} ({r.reason})"
    assert r.market_confidence == linking.INVERSION_CONF_SPEC
    # 1층이 그 이름을 모르면 상속이 그대로 산다(과잉수정 가드).
    r2 = linking.link_post(doc, kb=kb, inversion=_inv([("지나", "다른것")]))[0]
    assert (r2.market, r2.market_confidence) == ("늪지", linking.INHERIT_CONF), r2
    print("✓ AC10 1층 유일소유 > 스레드 상속 OK")


def test_ac11_an_abstained_mention_still_blocks_the_inversion():
    """**AC11** — `mentioned_market` 충돌로 보류된 행은 역인덱스가 **안 돈다**(불변).

    갈린 증거를 제품명 소유관계로 덮으면 개체연결의 보류 판정을 조용히 뒤집는 것이 된다.
    """
    kb = _conflict_kb()
    r = linking.link("ㅁㅁ", "요구르팅", kb=kb, inversion=_inv([("머머", "요구르팅")]))
    assert r.market is None, f"보류가 역인덱스로 뒤집혔다: {r.market}"
    print("✓ AC11 보류 행은 역인덱스 미발동 OK")


def test_ac12_inheritance_needs_the_title_to_declare_the_market():
    """**AC12** — 제목에 마켓이 없는 스레드에서는 본문 마켓이 **상속되지 않는다**.

    실측(스레드 200743 `인생슬 적구 가`): 한 댓글의 `ㅂㅉ 달토끼` 하나가 그 글 20행 전부를
    빈짱으로 만들었다. 실제로는 `ㅂㅇㅍ`·`ㅍㅅㅌㄹ`·`ㅅㄹㄹ`·`ㅇㅉ`·`ㅇㅇㅈ`·`ㅇㅊ` 가 섞인
    '내 인생슬 나열' 글이고 1층 소유는 베이퍼·웨이즈·봄·연찌로 갈린다.
    ⚠️ 조용히 버리지 않는다 — `_inherit_blocked_by_title` 표식이 남는다(무음 금지).
    """
    kb = _kb()
    doc = {"market": "빈짱", "_market_inherited": True, "_thread_title": "인생슬 적구 가",
           "reviews": [{"mentioned_product": "달토끼"}]}
    r = linking.link_post(doc, kb=kb)[0]
    assert r.market is None, f"제목이 선언 안 한 마켓이 상속됐다: {r.market}"
    assert doc.get("_inherit_blocked_by_title") == "빈짱", doc
    print("✓ AC12 제목 미선언 → 상속 차단 OK")


def test_ac13_a_declaring_title_still_lets_inheritance_through():
    """**AC13** — 제목이 마켓을 선언한 스레드에서는 상속이 **여전히** 걸린다.

    AC12 를 만족시키는 가장 쉬운 방법이 '상속 통째 제거'인데, 그러면 마켓을 안 밝힌 댓글의
    귀속을 통째로 잃는다(`test_post_market_still_fills_empty_pieces` 와 같은 방향의 가드).
    제목 표기는 초성(`ㅂㅉ`)이어도 되고 표면형(`빈짱`)이어도 된다 — 갤 관행이 초성이다.
    """
    kb = _kb()
    for title in ("ㅂㅉ 후기 몰아쓰기", "빈짱 후기 몰아쓰기"):
        doc = {"market": "빈짱", "_market_inherited": True, "_thread_title": title,
               "reviews": [{"mentioned_product": "달토끼"}]}
        r = linking.link_post(doc, kb=kb)[0]
        assert r.market == "빈짱", f"제목이 선언했는데 상속이 끊겼다({title}): {r.reason}"
        assert r.market_confidence == linking.INHERIT_CONF
        assert "_inherit_blocked_by_title" not in doc
    print("✓ AC13 제목 선언 스레드는 상속 유지 OK")


def test_layer1_narrows_a_multi_market_piece_but_never_overrides_it():
    """조각이 마켓을 **여럿** 지목해도 1층 소유가 그중 하나면 갈린 게 아니라 **좁혀진** 것이다.

    실측(감사 D1 ROW#421·422): `ㅅㅈㄴ 헝잭버거 , ㅂㅇㅍ 건체리크럼블` 한 댓글이 두 행 모두
    `늪지` 로 찍혀 있었다. 조각은 두 마켓을 말하지만 **제품명이 어느 쪽인지 말해 준다** —
    `헝잭버거` 는 지나, `건체리크럼블` 은 베이퍼다. 이걸 충돌로 흘려보내면 감사가 지목한
    바로 그 오귀속이 그대로 남는다.

    ⛔ 1층이 **후보 밖** 마켓을 지목하면 그건 진짜 충돌이다 — 두 증거가 어긋나므로
      조용히 1층을 이기게 하지 않는다.
    """
    from slime_rag import pipeline
    kb = _kb()
    inv = _inv([("지나", "헝잭버거"), ("베이퍼", "건체리크럼블"), ("모모네", "로지가든")])
    text = "나도 맨날 찾아다니는데 ㅅㅈㄴ 헝잭버거 , ㅂㅇㅍ 건체리크럼블 좋았음"
    for product, want in (("헝잭버거", "지나"), ("건체리크럼블", "베이퍼")):
        mk, why, conf = pipeline.dc_market_target(product, "늪지", kb=kb, inversion=inv,
                                                  text=text, title="")
        assert (mk, why) == (want, "spec_narrows_multi"), (product, mk, why)
        assert conf == pipeline.BACKFILL_CONF_SPEC
    # 1층이 후보 밖을 지목하면 충돌로 남는다(조용한 승격 금지).
    mk, why, _c = pipeline.dc_market_target("로지가든", "늪지", kb=kb, inversion=inv,
                                            text=text, title="")
    assert (mk, why) == (None, "conflict_multi_market"), (mk, why)
    print("✓ 1층이 다중 후보를 좁힘 · 후보 밖은 충돌 유지 OK")


def test_a_single_market_piece_never_silently_overrides_layer1_ownership():
    """조각이 마켓을 **하나만** 말해도 1층 소유가 다르면 충돌이다 — 위 ⛔의 **대칭**.

    위 케이스(다중 후보)엔 ⛔ 가 있는데 **단일 후보 분기엔 없었다**. 그래서 조각 스캔이
    1층을 조용히 이겼다 — 재감사 실측(2026-08-11) 새 결함 4행이 전부 이 구멍이다:
    `나 ㅂㅇㅍ 빨대는 잘 모르겠더라 … 빠코볼은 재미없었으면` 한 댓글이 `빠코볼`(1층 지나)
    행을 **베이퍼**로 찍었다. 조각이 마켓을 하나만 말한다는 건 그 조각의 **화제**가 하나란
    뜻이지, 거기 언급된 **제품이 그 마켓 것**이라는 뜻이 아니다 — 이 갤의 지배적 형태가
    한 글에서 여러 마켓을 비교하는 글이라(상속 폴백 제거와 같은 실측) 정상 형태다.

    ⚠️ 확신도로는 사후에 못 고른다: 되돌림이 0.90/0.85 → 0.83 이라 값만 보면 정상 채움과
      구별되지 않는다. 그래서 **쓰기 전에** 막고 사람 목록으로 내보낸다.
    ⚠️ 1층이 **모르는** 이름이면 막지 않는다 — 그러면 조각 스캔이 통째로 죽는다
      (실측: 같은 런의 올바른 채움 4행이 1층 미등재 제품이었다).
    """
    from slime_rag import pipeline
    kb = _kb()
    inv = _inv([("지나", "빠코볼")])
    text = "나 ㅂㅇㅍ 빨대는 잘 모르겠더라 농도 잡아도 ㅜ 빠코볼은 재미없었으면"
    mk, why, conf = pipeline.dc_market_target("빠코볼", "지나", kb=kb, inversion=inv,
                                              text=text, title="")
    assert (mk, why, conf) == (None, "conflict_spec_vs_piece", None), (mk, why, conf)
    # 1층이 모르는 이름이면 조각 스캔이 그대로 산다(과잉 차단 회귀).
    mk2, why2, _c = pipeline.dc_market_target("빨대슬", None, kb=kb, inversion=inv,
                                              text=text, title="")
    assert (mk2, why2) == ("베이퍼", "piece_scan"), (mk2, why2)
    # 1층과 조각이 **같은** 마켓을 말하면 충돌이 아니다.
    mk3, why3, _c3 = pipeline.dc_market_target(
        "빠코볼", None, kb=kb, inversion=_inv([("베이퍼", "빠코볼")]), text=text, title="")
    assert (mk3, why3) == ("베이퍼", "piece_scan"), (mk3, why3)
    print("✓ 단일 조각 스캔이 1층 소유를 조용히 못 이김 · 과잉 차단 없음 OK")


def test_piece_body_scan_fills_only_on_a_single_market():
    """조각 본문이 **정확히 하나**의 마켓을 지목할 때만 채운다 — 비교글에선 아무것도 안 한다.

    이게 되돌린 행 스코프 가드와 다른 점의 기계적 근거다(ADR-0018 대안 (d) 기각 사유):
    옛 가드를 깨뜨린 입력에서 이 경로는 **막지 않고 그냥 아무것도 만들지 않는다**.
    """
    kb = _kb()
    doc = {"market": None, "reviews": [{"mentioned_product": "달토끼"}]}
    one = linking.markets_in_text("ㅂㅉ 에서 산 거임", kb)
    r = linking.link_post(doc, kb=kb, text_markets=one)[0]
    assert r.market == "빈짱" and r.market_confidence == linking.REPAIR_CONF_PIECE_CHOSEONG, r
    many = linking.markets_in_text("빠코볼, ㅂㅇㅍ 빨대 이런거였음. ㅁㅁㄴ 도 좋았고", kb)
    assert len(many.unique) > 1, many.unique
    r2 = linking.link_post({"market": None, "reviews": [{"mentioned_product": "달토끼"}]},
                           kb=kb, text_markets=many)[0]
    assert r2.market is None, f"비교글에서 마켓을 만들어 냈다: {r2.market}"
    print("✓ 조각 본문 스캔: 단일일 때만 채움 OK")


# ---------------------------------------------------------------- ③ 포함관계 후보(대역 conn)
class _FakeConn:
    """`pipeline.connect()` 대역 — `product_containment_candidates` 는 SELECT 하나뿐이다."""

    def __init__(self, rows):
        self._rows = rows
        self.sql: list[str] = []
        self.committed = False

    def execute(self, sql, *_a, **_k):
        self.sql.append(sql)
        return self

    def fetchall(self):
        return self._rows

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_containment_candidates_are_reported_not_merged():
    """③ 잔여 포함관계는 **후보로만** 나온다 — 자동 병합·개명 없음.

    같은 조각의 `빠코볼`/`미니빠코볼` 은 진짜 다른 제품이라(202004 실측) 포함관계만으로
    접으면 유령 제품 복구 때 `빠코폼` 을 지웠던 실패를 반대 방향으로 반복한다.
    """
    from slime_rag import pipeline

    rows = [
        ("p1", "지나", "빠코볼"), ("p1", "지나", "미니빠코볼"),   # 한 조각 안의 포함관계
        ("p2", "예찬", "밀키크림파르페"), ("p2", None, "요아곰 밀키크림파르페"),
        ("p3", "봄", "빠코볼"), ("p4", "빈짱", "미니빠코볼"),      # 서로 다른 조각 → 쌍 아님
    ]
    conn = _FakeConn(rows)
    rep = pipeline.product_containment_candidates(conn=conn)

    assert all(s.strip().upper().startswith("SELECT") for s in conn.sql), \
        f"읽기 전용이어야 하는데 SELECT 아닌 쿼리가 있다: {conn.sql}"
    assert conn.committed is False, "커밋이 나갔다 — 읽기 전용 계약 위반"

    got = {(e["outer"], e["inner"]) for e in rep["pairs"]}
    assert ("미니빠코볼", "빠코볼") in got, f"같은 조각의 포함관계를 못 잡았다: {got}"
    assert ("요아곰 밀키크림파르페", "밀키크림파르페") in got, got
    # 방향은 하나뿐 — 짧은 쪽이 inner 다.
    assert ("빠코볼", "미니빠코볼") not in got, "역방향까지 보고하면 사람이 정규형을 못 고른다"
    # 조각이 다르면 쌍이 아니다(p3·p4 는 한 조각에 같이 있지 않다).
    assert rep["n_pairs"] == 2, f"조각 경계를 안 지켰다: {rep['pairs']}"
    # 마켓은 **그 쌍의 이름들이 실제로 단 것**만.
    pair = next(e for e in rep["pairs"] if e["inner"] == "밀키크림파르페")
    assert pair["markets"] == ["예찬"], f"쌍과 무관한 마켓이 섞였다: {pair['markets']}"
    print("✓ ③ 포함관계: 후보만 · 읽기 전용 · 한 방향 · 조각 스코프 OK")


def test_containment_output_carries_no_source_text():
    """산출물에 원문 본문이 없다 — 이름·건수·마켓뿐이라 커밋 가능하다(ADR-0013)."""
    from slime_rag import pipeline

    rep = pipeline.product_containment_candidates(
        conn=_FakeConn([("p1", "지나", "빠코볼"), ("p1", "지나", "미니빠코볼")]))
    assert set(rep["pairs"][0]) == {"outer", "inner", "pieces", "markets"}, rep["pairs"][0]
    print("✓ ③ 산출물에 원문 본문 미포함(ADR-0013) OK")


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
    test_inherited_market_carries_its_own_confidence()
    test_fill_path_confidences_never_collide()
    test_ac10_layer1_ownership_beats_thread_inheritance()
    test_ac11_an_abstained_mention_still_blocks_the_inversion()
    test_ac12_inheritance_needs_the_title_to_declare_the_market()
    test_ac13_a_declaring_title_still_lets_inheritance_through()
    test_layer1_narrows_a_multi_market_piece_but_never_overrides_it()
    test_a_single_market_piece_never_silently_overrides_layer1_ownership()
    test_piece_body_scan_fills_only_on_a_single_market()
    test_containment_candidates_are_reported_not_merged()
    test_containment_output_carries_no_source_text()
    print("\n모든 마켓 귀속 테스트 통과 (LLM·DB·네트워크 미접촉)")
