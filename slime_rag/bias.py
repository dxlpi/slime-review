# -*- coding: utf-8 -*-
"""
후기 편향 태깅 — 홍보성(promo) 감지 + 판매자(seller) 라우팅.

배경: 인스타 해시태그 후기(2층 긍정편향 소스)에는 두 종류의 '편향된' 글이 섞인다.
  1) 홍보성 후기 — 대가/무상 제공("서포터즈", "무상으로 제공받았습니다", "협찬" 등).
     실사용 후기와 정서가 다르므로 드롭이 아니라 '분리 버킷'으로 태깅(§2·§10 라벨링 원칙).
  2) 판매자 게시물 — ownerUsername 이 KB 마켓 핸들(예: from.murmurslime)과 일치하는 글.
     후기가 아니라 '공식 정보원'이므로 후기 요약에서 빼고, 향료/풀조합/종류는 1층 스펙으로 라우팅.

설계(계획 §2 D1~D4):
- relevance.py(후기 vs 비후기)와는 다른 축이라 별도 모듈. bias 는 '후기 중 편향' + '판매자 라우팅'.
- 홍보성 감지 = 결정적 키워드/구문 seed(감사가능·무비용·오프라인 테스트) + 선택적 LLM 폴백 훅.
  ⚠️ '광고' 단독은 오탐('광고 아님') → 구문 단위('유료광고','#광고')로만 매칭.
- 판매자 감지 = KB 핸들 역인덱스(handle + handles_alt) vs owner_username(소문자 정규화). 결정적.
- 우선순위: 판매자 > 홍보성. 마켓 본인이 협찬 문구를 써도 그건 판매자 글(1층 라우팅 우선).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from .config import ROOT, settings

log = logging.getLogger("bias")

# 홍보성 판정 타입: 텍스트 → (홍보성인가, 유형/마커). LLM/키워드 구현이 공유하는 계약.
PromoDetector = Callable[[str], "tuple[bool, Optional[str]]"]


# ---------------------------------------------------------------- 홍보성 마커 seed
# 구문 단위로만(오탐 방지). data/promo_markers.json 이 있으면 그걸로 대체/확장한다.
PROMO_SEED: list[str] = [
    "서포터즈",
    "무상으로 제공", "무상 제공", "무료로 제공",
    "제공받아", "제공받았",
    "협찬",
    "유료광고", "유료 광고",
    "단순선물", "단순 선물",
    "체험단",
    "원고료",
    "대가를 받",
    "#광고", "#협찬",
    "ppl",
]


def load_promo_markers(path=None) -> list[str]:
    """data/promo_markers.json(선택) → 마커 리스트. 없으면 PROMO_SEED.

    파일 구조: {"markers": ["서포터즈", ...]} — '_' 접두 키는 메타(무시).
    운영에서 seed 를 코드 수정 없이 튜닝하기 위한 훅(_TOXIC_SEED 패턴과 동일).
    """
    p = path or (ROOT / "data" / "promo_markers.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return list(PROMO_SEED)
    except Exception as e:                       # 깨진 파일이면 seed 로 폴백(회복력)
        log.warning("promo_markers 로드 실패(%s): %s → seed 사용", p, e)
        return list(PROMO_SEED)
    markers = [m for m in (data.get("markers") or []) if m and not str(m).startswith("_")]
    return markers or list(PROMO_SEED)


def detect_promo(text: str, classify_fn: Optional[Callable[[str], bool]] = None,
                 markers: Optional[list[str]] = None) -> tuple[bool, Optional[str]]:
    """캡션이 홍보성(대가/무상 제공)인가. 매칭된 첫 마커를 함께 반환.

    반환: (True, "서포터즈") | (False, None).
    구문 단위 매칭이라 '광고 아님' 같은 부정문에 오탐하지 않는다('광고' 단독을 seed 에 두지 않음).
    classify_fn 을 주면 seed 미매칭 시 LLM 폴백(정밀도 필요 시). 폴백은 마커명을 알 수 없어 'llm' 표기.
    """
    t = text or ""
    tl = t.lower()                               # 'ppl'/'PPL' 등 대소문자 무관 매칭용
    for m in (markers if markers is not None else PROMO_SEED):
        needle = m.lower()
        if needle in tl:
            return True, m
    if classify_fn is not None and classify_fn(t):
        return True, "llm"
    return False, None


# ---------------------------------------------------------------- LLM 의미 기반 홍보성 판정
# 키워드 seed 의 두 한계를 LLM 이해로 넘는다:
#   (1) recall — 서포터/체험단/무상제공은 표현이 무한. '서포터즈'만 잡으면 '서포터 게시물입니다'를 놓친다.
#   (2) precision — 단어가 있다고 홍보성이 아니다. "서포터분들은 얄랑하다 했지만 나는 매트했다"는
#       남(서포터)을 '인용'해 자기 실사용과 대조할 뿐 → 작성자는 제공받지 않음(genuine).
# 그래서 핵심 질문을 '단어 유무'가 아니라 '작성자 본인이 대가·무상으로 받아 올린 글인가'로 좁힌다.
PROMO_LLM_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["is_sponsored", "kind", "evidence"],
    "properties": {
        "is_sponsored": {"type": "boolean",
                         "description": "이 글을 쓴 '작성자 본인'이 제품을 대가·무상으로 제공받아 올린 글이면 true. "
                                        "남(서포터/체험단 등)을 언급·인용만 하면 false."},
        "kind": {"anyOf": [{"type": "string",
                            "enum": ["supporter", "reviewer", "sponsored",
                                     "free_product", "paid_ad", "collab"]},
                           {"type": "null"}],
                 "description": "홍보성 유형(서포터/체험단/협찬/홍보목적 무상제공/유료광고·PPL/협업). "
                                "구매 사은품·서비스·할인은 홍보성이 아니다 → 홍보성 아니면 null."},
        "evidence": {"type": ["string", "null"],
                     "description": "판단 근거가 된 원문 조각(짧게). 없으면 null."},
    },
}

PROMO_LLM_SYSTEM = """\
너는 인스타 슬라임 후기 캡션이 '홍보성'인지 판정한다.
'홍보성'을 좁게 정의한다: 작성자가 제품을 '홍보/게시 대가로' 받아 올린 글 —
즉 서포터·체험단·협찬·유료광고처럼 게시 의무나 PR 관계가 있어 후기가 편향될 수 있는 글이다.
핵심 판단선은 '무언가 공짜로 받았나'가 아니라 '구매했나 vs 홍보 대가로 받았나'다.
(슬라임업계 도메인: '할인·서비스·비매품'은 대개 구매 맥락이지 홍보 대가가 아니다 — 아래 참고.)

[true — 홍보 대가로 받음(후기 편향 가능)]
- 서포터/서포터즈, 체험단, 협찬, "촬영/홍보 목적으로 무상 제공받았", 유료광고, PPL, 광고성 게시물.
  표현이 달라도 '게시를 전제로 제공받았다'는 의미면 true.
  예) "서포터 게시물입니다", "서포터즈 게시물입니다", "영상 촬영 목적을 위해 무상으로 제공받았습니다",
      "체험단으로 받은", "협찬받아".
  예) "#레몬커드쉘도넛 #머머슬라임 서포터 … 비매로 제공되었던 베이스…" → '서포터' 맥락 → true(supporter).

[false — 구매했거나, 홍보 대가가 아닌 경우]
- 할인/세일/추가 할인으로 '샀다' → 할인은 여전히 구매다. 홍보성 아님.
  예) "추가 할인이라 안 살 이유 없어서 산 건데" → false.
- '비매(비매품)'를 단지 언급하거나 구매 사은품으로 받은 경우 → 그 자체는 홍보 대가가 아니다.
  ('비매'는 '비매품'을 뜻할 뿐. 서포터·협찬 맥락이 함께 없으면 false.)
  예) "저번 차수 비매때부터 갈망이었는데 … 산 건데" → false(구매).
- '서비스'(일정 금액 이상 구매 시 끼워주는 무료 슬라임, 예: 3개 사면 1개 더) → 구매 보너스다.
  게시 의무가 없고 구매자 누구나 받는 것 → 홍보 대가 아님 → false.
- 내돈내산/직접 구매/샀다 → false.
- 남(서포터·체험단·다른 후기)을 '언급·인용'만 하는 것 → 작성자가 받은 게 아님 → false.
  예) "서포터분들은 얄랑하다 했지만 제가 만졌을 땐 매트했어요" → false.

[불확실할 때]
- 작성자가 '홍보 대가로 받았다'는 근거가 명확하지 않으면 false(실사용으로 보수적 처리).
- 특히 '샀다/구매/할인/서비스/비매' 언급이 있으면, 명시적 홍보 제공(서포터/체험단/협찬/무상 제공) 문구가
  함께 있지 않는 한 false.

evidence 에는 판단 근거가 된 원문 조각을 짧게 남긴다. kind 는 홍보성일 때만 유형, 아니면 null."""


def promo_verdict(text: str, llm, model: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """캡션 한 건 → (홍보성인가, 유형). LLM 이 '작성자가 제공받았는지'를 의미로 판정.

    llm 은 llm_ops.LLM(또는 .complete(prompt, system, schema, model, label) 시그니처를 가진 것).
    structured outputs(strict)로 강제하므로 파싱은 llm_ops 가 보장한다.
    """
    r = llm.complete(text or "", system=PROMO_LLM_SYSTEM, schema=PROMO_LLM_SCHEMA,
                     model=model or settings.model_extract, label="bias.promo")
    is_promo = bool(r.get("is_sponsored"))
    marker = (r.get("kind") or "sponsored") if is_promo else None
    return is_promo, marker


def make_llm_promo_detector(llm, model: Optional[str] = None) -> PromoDetector:
    """LLM 의미 판정을 PromoDetector(text→(bool,marker))로 감싼다. classify/partition 에 주입.

    LLM 호출이 실패하면 예외를 던지지 않고 키워드 seed 로 폴백한다(수집 회복력 — 한 건의
    판정 실패가 인제스트 전체를 죽이지 않게). 폴백도 실패하면 genuine(보수적).
    """
    def detect(text: str) -> tuple[bool, Optional[str]]:
        try:
            return promo_verdict(text, llm, model)
        except Exception as e:
            log.warning("LLM 홍보성 판정 실패 → 키워드 폴백: %s", e)
            return detect_promo(text)
    return detect


# ---------------------------------------------------------------- 홍보성 게이트(값싼 recall 전용)
# 캐스케이드(계획 promo-gate-llm-cascade): 값싼 결정적 게이트가 '홍보 의심' 캡션만 통과시키고(recall),
# 통과분만 LLM verdict 로 정밀 판정(precision)한다. 명백한 실사용은 게이트에서 즉시 genuine 단락 →
# LLM 호출을 5~10× 줄인다. 게이트는 false positive 를 허용한다(precision 은 LLM 몫).
#
# GATE_LEXICON 은 PROMO_SEED 의 '상위집합'(D2): seed 는 정밀 지향(좁음)이라 verdict 없을 때의 fallback,
# 게이트는 recall 지향(넓음)이라 애매어(증정/나눔/이벤트/체험/제공/광고…)까지 포함해 LLM 에 판정 기회를 준다.
# ⛔ 순수 구매어(비매·서비스·할인·세일)는 게이트에서 제외(D7): 단독으로 홍보를 시사하지 않고(비매품·구매
#    사은품·할인은 전부 구매 맥락) 실제 홍보글은 서포터/협찬/무상 제공 등 별도 신호를 반드시 동반해 게이트에
#    남으므로, 빼도 recall 손실 0이며 순수 구매글이 즉시 genuine 으로 단락된다(메모 promo-vs-purchase-domain-rules).
GATE_LEXICON: list[str] = [
    *PROMO_SEED,                                 # seed ⊆ gate (superset 규약 D2)
    "서포터",                                     # '서포터즈' 외 '서포터 게시물입니다' 등 표현
    "증정", "나눔", "무료", "이벤트", "당첨",
    "원고", "대가", "체험", "제공", "광고",         # 게이트는 넓게 — '광고 아님'도 통과(LLM 이 genuine 판정)
    "sponsored", "gifted", "#ad",
]


def load_gate_terms(path=None) -> list[str]:
    """data/promo_gate_terms.json(선택) → 게이트 어휘 리스트. 없으면 GATE_LEXICON.

    파일 구조: {"terms": ["서포터", ...]} — '_' 접두 키는 메타(무시).
    코드 수정 없이 recall 어휘를 튜닝하기 위한 훅(load_promo_markers 패턴과 동일).
    """
    p = path or (ROOT / "data" / "promo_gate_terms.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return list(GATE_LEXICON)
    except Exception as e:                       # 깨진 파일이면 코드 기본으로 폴백(회복력)
        log.warning("promo_gate_terms 로드 실패(%s): %s → GATE_LEXICON 사용", p, e)
        return list(GATE_LEXICON)
    terms = [t for t in (data.get("terms") or []) if t and not str(t).startswith("_")]
    return terms or list(GATE_LEXICON)


def promo_gate(text: str, terms: Optional[list[str]] = None) -> bool:
    """캡션이 '홍보 의심'인가(→ LLM 판정 대상). 어휘 1개라도 등장하면 True. 결정적·무비용.

    소문자 부분문자열 비교(recall 전용). 순수 실사용·초성단독·빈문자열은 False → LLM 미호출(단락).
    게이트는 false positive 를 허용한다(precision 은 promo_verdict 가 책임진다).
    """
    tl = (text or "").lower()
    if not tl.strip():
        return False
    for t in (terms if terms is not None else GATE_LEXICON):
        if t.lower() in tl:
            return True
    return False


def make_gated_llm_promo_detector(llm, model: Optional[str] = None,
                                  terms: Optional[list[str]] = None,
                                  on_label: Optional[Callable[[str, bool, Optional[str]], None]] = None,
                                  ) -> PromoDetector:
    """게이트(recall)→LLM(verdict) 캐스케이드를 PromoDetector 계약으로 감싼다(인터페이스 불변 D3).

    - 게이트 미통과 = 즉시 (False, None) — LLM 미호출(비용/지연 0, D4).
    - 게이트 통과분만 make_llm_promo_detector(try/except 폴백 재사용)로 정밀 판정.
    - on_label(선택, D6): 게이트 통과분(text/verdict/marker)을 라벨 싱크로 흘려 ML distillation 준비.
    partition/ingest_hashtag/CLI 는 detector 를 갈아끼우기만 하면 되고 로직 변화가 없다.
    """
    gate_terms = terms if terms is not None else load_gate_terms()
    llm_detect = make_llm_promo_detector(llm, model)   # LLM 실패 회복력(키워드 폴백) 재사용

    def detect(text: str) -> tuple[bool, Optional[str]]:
        if not promo_gate(text, gate_terms):
            return False, None                   # genuine — 게이트 단락, LLM 미호출
        is_promo, marker = llm_detect(text)
        if on_label is not None:
            on_label(text, is_promo, marker)
        return is_promo, marker
    return detect


# ---------------------------------------------------------------- 판매자 역인덱스
def _markets_of(kb) -> list[dict]:
    """KB 객체(.markets) 또는 원시 dict({"markets":[...]}) 양쪽 지원."""
    if hasattr(kb, "markets"):
        return kb.markets
    return (kb or {}).get("markets", [])


def seller_index(kb) -> dict[str, str]:
    """{owner_username_lower: market_word}. handle + handles_alt 를 모두 등록.

    owner_username 소문자 정규화로 비교하므로 여기서도 소문자로 색인한다.
    """
    idx: dict[str, str] = {}
    for m in _markets_of(kb):
        mw = m.get("market_word") or m.get("market")
        for h in [m.get("handle"), *(m.get("handles_alt") or [])]:
            if h:
                idx[h.strip().lower()] = mw
    return idx


# ---------------------------------------------------------------- 분류
@dataclass
class BiasVerdict:
    category: Optional[str]        # 'seller' | 'promo' | None(=실사용 후기)
    marker: Optional[str] = None  # promo 매칭 마커 / seller 는 None
    market: Optional[str] = None  # seller 일 때 KB market_word


def classify(raw, sellers: dict[str, str], *,
             promo_detector: Optional[PromoDetector] = None,
             classify_fn: Optional[Callable[[str], bool]] = None,
             markers: Optional[list[str]] = None) -> BiasVerdict:
    """RawReview 한 건 → 편향 판정. 판매자 먼저(우선순위 D4), 아니면 홍보성 검사.

    promo_detector 를 주면 그게 홍보성 판정을 '전담'한다(LLM 의미 이해가 1순위 결정자).
    안 주면 키워드 seed(detect_promo) 로 폴백 — 오프라인·무비용 경로.
    ⚠️ 키워드를 먼저 돌려 promo 를 확정하면 안 된다: "서포터분들은…내가 만졌을 땐" 같은 인용문을
       오탐하기 때문. 그래서 detector 가 있으면 키워드를 건너뛰고 detector 에 전권을 준다.
    """
    owner = (raw.meta.get("owner_username") or "").strip().lower()
    if owner and owner in sellers:               # 판매자 우선 — 협찬 문구가 있어도 판매자 글
        return BiasVerdict(category="seller", market=sellers[owner])
    if promo_detector is not None:
        is_promo, marker = promo_detector(raw.text)
    else:
        is_promo, marker = detect_promo(raw.text, classify_fn=classify_fn, markers=markers)
    if is_promo:
        return BiasVerdict(category="promo", marker=marker)
    return BiasVerdict(category=None)


def partition(raws, kb, *, promo_detector: Optional[PromoDetector] = None,
              classify_fn: Optional[Callable[[str], bool]] = None,
              markers: Optional[list[str]] = None) -> tuple[list, list]:
    """수집된 RawReview 들을 (seller_posts, user_reviews) 로 가른다.

    - seller_posts: 판매자 글. meta["seller_market"] 에 KB market_word 를 실어 1층 라우팅에 넘긴다.
    - user_reviews: 실사용 후기(판매자 아님). meta["review_class"] = 'promo'|'genuine',
      홍보성이면 meta["promo_marker"] 에 유형 마커.
    promo_detector(예: make_llm_promo_detector(llm)) 를 주면 홍보성 판정을 LLM 의미 이해로 한다.
    partition 은 KB 를 알기에 판매자 라우팅의 authoritative 지점이다(sources._to_review 의
    caption-self 라벨은 KB-무관·키워드 임시값 — 여기서 최종 확정).
    """
    sellers = seller_index(kb)
    seller_posts: list = []
    user_reviews: list = []
    for raw in raws:
        v = classify(raw, sellers, promo_detector=promo_detector,
                     classify_fn=classify_fn, markers=markers)
        if v.category == "seller":
            raw.meta["seller_market"] = v.market
            seller_posts.append(raw)
        else:
            raw.meta["review_class"] = "promo" if v.category == "promo" else "genuine"
            raw.meta["promo_marker"] = v.marker
            user_reviews.append(raw)
    return seller_posts, user_reviews


if __name__ == "__main__":
    # 셀프테스트(무비용) — seed 매칭·게이트·판매자 라우팅·partition.
    from .sources import RawReview
    from .linking import load_kb

    class _SelfStub:                                 # LLM 대역: 호출돼도 비용 0
        def __init__(self, resp): self.resp = resp
        def complete(self, prompt, **kw): return self.resp

    assert detect_promo("이 제품은 서포터즈 게시물입니다") == (True, "서포터즈")
    assert detect_promo("영상 촬영 목적을 위해 무상으로 제공받았습니다")[0] is True
    assert detect_promo("이 슬라임 광고 아님 내돈내산") == (False, None)
    print("✓ detect_promo seed OK")

    # 게이트: recall(홍보 의심 통과) / 순수 구매어·실사용 단락 / seed ⊆ gate 규약
    assert set(PROMO_SEED) <= set(GATE_LEXICON)              # superset(D2)
    assert not ({"비매", "서비스", "할인", "세일"} & set(GATE_LEXICON))  # 순수 구매어 제외(D7)
    assert promo_gate("서포터 게시물입니다") is True          # '서포터즈' 아니어도 통과(recall)
    assert promo_gate("서포터분들은 얄랑하다 했지만 매트했어요") is True  # 인용문도 통과(precision 은 LLM)
    assert promo_gate("비매때부터 갈망이었는데 산 건데") is False  # 순수 구매 → 단락
    assert promo_gate("말랑하고 향 좋아요 재구매각") is False   # 순수 실사용 → 단락
    gated = make_gated_llm_promo_detector(
        _SelfStub({"is_sponsored": False, "kind": None, "evidence": None}),
        terms=GATE_LEXICON)
    assert gated("말랑 향 좋아요") == (False, None)           # 단락 → LLM 미호출
    print("✓ promo_gate 캐스케이드 OK")

    kb = load_kb()
    sellers = seller_index(kb)
    assert sellers.get("from.murmurslime") == "머머"
    assert sellers.get("murmurslime") == "머머"        # handles_alt
    print("✓ seller_index OK", {k: sellers[k] for k in list(sellers)[:3]})

    raws = [
        RawReview(text="머머 신상 입고! 향료 라벤더", url="u1", platform="instagram",
                  meta={"owner_username": "from.murmurslime"}),
        RawReview(text="서포터즈로 받은 슬라임인데 말랑좋아요", url="u2", platform="instagram",
                  meta={"owner_username": "random_user"}),
        RawReview(text="내돈내산 후기 향 좋고 쫀득", url="u3", platform="instagram",
                  meta={"owner_username": "another_user"}),
    ]
    sp, ur = partition(raws, kb)
    assert len(sp) == 1 and sp[0].meta["seller_market"] == "머머"
    assert len(ur) == 2
    classes = {r.url: r.meta["review_class"] for r in ur}
    assert classes == {"u2": "promo", "u3": "genuine"}, classes
    print("✓ partition OK — 판매자1(머머), 홍보성1, 실사용1")
    print("모든 bias 셀프테스트 통과 ✅")
