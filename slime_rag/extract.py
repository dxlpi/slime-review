# -*- coding: utf-8 -*-
"""
추출 러너 (Phase 2) — 비정형 후기 → 정형 JSON (2층).

`prompts/slime_rag_extraction_prompts.md` 의 2층 스펙을 옮긴 것.
핵심: structured outputs(`response_format` json_schema, strict)로 스키마를 강제한다.
      GPT-5 계열은 추론 모델이라 temperature 가 무시/제한될 수 있으므로,
      결정성은 낮은 temperature 가 아니라 '스키마 강제'로 확보한다.

원칙(스펙):
- 작성자가 명시한 것만. 미언급 블록/필드는 null. 추측 금지.
- 필드별 근거 스니펫(evidence)을 짧게.
- feel 은 FEEL_VOCAB 안에서만, '~같은' 비유는 feel_simile, 목록 밖은 feel_other.
- 점수는 작성자 명시분만 stated_rating. model_sentiment 는 모델 추정 라벨.
- mentioned_market/product 는 초성·약칭 그대로만 뽑는다(정규화는 linking 단계).
"""

from __future__ import annotations

import json
import logging
import re

from .config import settings
from .llm_ops import LLM

log = logging.getLogger("extract")

_NO_RE = re.compile(r"[?&]no=(\d+)")

# ---------------------------------------------------------------- 통제어휘
FEEL_VOCAB = ["말랑", "말캉", "쫀득", "퐁신", "폭닥", "크리미", "로션크리미",
              "얄랑", "매트", "빳빳", "텐션감있는", "흐물거리는", "쳐지는", "흐름성있는"]
TYPE_ENUM = ["폼볼", "촉감류(점토)", "디폼", "난사", "눈꽃", "지글리", "크런치",
             "빈백", "클라우드", "샤베트", "클리어", "버글리", "젤라또", "빨대", "라이스볼",
             "수수깡", "자바칩", "스티로폼", "납작블럭"]
PROJECTION = ["강함", "적당", "약함"]
PRESENCE = ["있음", "없음", "약간"]
SENTIMENT = ["pos", "neu", "neg"]
REBUY = ["있음", "없음", "미언급"]


# ---------------------------------------------------------------- 스키마 헬퍼
# structured outputs 제약: 모든 object 는 additionalProperties:false + 전체 required.
# '미언급=null' 은 nullable 타입/anyOf 로 표현한다.
def _nstr() -> dict:
    return {"type": ["string", "null"]}


def _nenum(values: list[str]) -> dict:
    return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}


def _obj(properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _nullable_obj(properties: dict, description: str | None = None) -> dict:
    node: dict = {"anyOf": [_obj(properties), {"type": "null"}]}
    if description:
        node["description"] = description
    return node


# ---------------------------------------------------------------- 2층 스키마
# 제품 단위 '평가' 속성. 한 후기가 여러 제품을 비교하면 이 객체를 제품 수만큼 만든다.
# 마켓·배송은 제품이 아니라 '후기(주문) 단위' 사실이므로 최상위로 뺀다(아래 LAYER2_SCHEMA).
_PRODUCT_PROPS: dict = {
    "mentioned_product": {"type": ["string", "null"],
                          "description": "이 항목이 가리키는 제품명. 같은 베이스명을 비교하면 구분어 포함"
                                         "(예: '한줌'이 둘이면 한글과자한줌 / 과일사탕한줌)."},
    # 항목 단위 마켓. 최상위 `market`(주문 단위)의 **예외 통로**다 — 보통 1주문=1마켓이라
    # 최상위 하나로 충분하지만, 이 갤엔 여러 마켓을 한 줄에 나열하는 글이 있다
    # (`ㅇㅍㅋ 든든장작 … ㅅㄹㄹ 약과볼`). 그런 글에서 최상위 하나만 쓰면 나머지 제품이
    # 전부 남의 마켓 후기로 집계된다 — 실측 94행이 KB 마켓을 2개 이상 언급한다.
    # ⚠️ 최상위와 같은 규칙: **표기 그대로**(정규화는 linking 단계), 일반어는 마켓이 아니다.
    "mentioned_market": {"type": ["string", "null"],
                         "description": "이 항목의 제품 앞·근처에 마켓 초성·약칭이 따로 붙은 경우만. "
                                        "표기 그대로(정규화 금지). 글 전체가 한 마켓이면 여기 말고 "
                                        "최상위 market 에만 넣는다. 없으면 null."},
    "scent": _nullable_obj({
        "perceived": _nstr(),
        "projection": _nenum(PROJECTION),
        "vs_official_comment": _nstr(),     # 작성자가 공식향과 다르다고 직접 말한 경우만
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }),
    "texture": _nullable_obj({
        "feel": {"type": "array", "items": {"type": "string", "enum": FEEL_VOCAB},
                 "description": "본문에 실제 등장한 질감 통제어휘만. 없으면 []. 추측 금지."},
        "feel_simile": _nstr(),
        "feel_other": _nstr(),              # 어휘 밖 새 표현 (예: "걀걀거림")
        "hand_stick": _nenum(PRESENCE),
        "hand_residue": _nenum(PRESENCE),
        "type_mentioned": _nstr(),          # 후기가 종류 언급 시(1층 교차검증)
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }),
    "sound": _nullable_obj({
        "mentioned": {"type": "boolean"},
        "notes": _nstr(),
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }, description="소리. 비즈/재료가 부딪는 '걀걀거림' 같은 청각 신호도 여기(질감 아님)."),
    "longevity": _nullable_obj({
        "notes": _nstr(),
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }, description="제품 수명만('빨리 죽음/오래 감'). 배송 속도·도착 여부는 여기 아님 → shipping_cs."),
    "overall": _obj({
        "stated_rating": {"type": ["number", "null"]},   # 작성자 명시 점수만
        "model_sentiment": {"type": "string", "enum": SENTIMENT},
        "rebuy_intent": {"type": "string", "enum": REBUY},
        "summary": _nstr(),
    }),
    # 전언 차단의 **결정적 게이트**(AC15). 프롬프트 지시만으로는 못 막는다 — 실측상 같은 입력에
    # 4회 호출하면 4번 다 답이 달랐다(빈 배열 ↔ 전언 제품 4개 유입). 그래서 '본인 경험임을 보여주는
    # 원문 조각'을 필수 필드로 요구하고, null 인 항목은 코드가 버린다(`drop_hearsay_reviews`).
    # 1층에서 '제품명은 반드시 캡션 해시태그여야 한다'로 유령 제품을 막은 것과 같은 수법이다.
    "firsthand_evidence": {"type": ["string", "null"],
                           "description": "작성자 '본인'이 직접 써 봤음을 보여주는 원문 조각(15자 내외). "
                                          "전언('다들 좋다고 함')·미구매('아직 안산게')·미사용이면 반드시 null."},
}

# 최상위: 후기(주문) 단위 사실(market·shipping_cs·flags) + 제품별 평가(reviews[]).
# 이 프로퍼티 묶음은 단건 스키마와 **스레드 배치 스키마가 공유**한다(정의가 갈라지면 배치 결과와
# 단건 결과가 조용히 달라진다 — AC12 동등성이 무의미해진다).
_DOC_PROPS: dict = {
    "market": {"type": ["string", "null"],
               "description": "마켓 식별자만(초성·약칭·마켓명). 제목/머리말의 마켓도 여기. "
                              "후기 전체에 하나(보통 1주문=1마켓). '자사몰/공홈/스토어' 등 일반어는 null."},
    "shipping_cs": _nullable_obj({
        "notes": _nstr(),
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }, description="배송·주문·문자·도착·교환·CS. 후기(주문) 전체 기준. 이걸로 제품 항목을 만들지 마라."),
    "reviews": {"type": "array", "items": _obj(_PRODUCT_PROPS),
                "description": "작성자 '본인'의 사용 경험/평가를 서술한 슬라임마다 한 항목. 비교글이면 제품별 분리. "
                               "제목·배송만 있고 평가가 없는 건, 그리고 전언(남이 좋다더라)은 항목으로 만들지 마라."},
    "flags": _obj({"toxic": {"type": "boolean"}}),
}

LAYER2_SCHEMA: dict = _obj(_DOC_PROPS)

# 스레드 배치(C-1) — 조각(글/댓글) 하나당 문서 하나. source_id 가 귀속의 유일한 근거다.
LAYER2_THREAD_SCHEMA: dict = _obj({
    "docs": {"type": "array",
             "items": _obj({"source_id": {"type": "string",
                                          "description": "이 문서가 대응하는 입력 조각의 [S<n>] 번호. "
                                                         "입력에 나온 값을 그대로. 새로 만들지 마라."},
                            **_DOC_PROPS}),
             "description": "입력 스레드의 조각마다 정확히 한 항목. 합치거나 빠뜨리지 마라."},
})


# ---------------------------------------------------------------- 시스템 프롬프트
LAYER2_SYSTEM = f"""\
너는 한국 슬라임 후기에서 사용자 경험을 구조화한다.
작성자가 '명시'한 내용만 추출하고, 안 나온 항목/블록은 null. 추측·과장·창작 금지.
각 필드에는 근거가 된 원문 조각을 evidence 에 짧게(15자 내외) 넣는다.

[후기 단위 vs 제품 단위]
- 최상위 market·shipping_cs 는 '후기(주문) 전체' 사실이다. 보통 1주문=1마켓.
  market: 제목/머리말의 마켓 초성·약칭도 여기. 표기 그대로(정규화 금지).
  '자사몰/공홈/스토어/자몰' 같은 일반 명사는 마켓이 아니다 → null.
- 제목줄(예: "ㅂㅉ 한줌")의 마켓은 최상위 market 에 넣고, 본문 모든 제품에 적용된다고 본다.
  → 예) "ㅂㅉ 한줌\n…한글과자한줌…과일사탕…" : market="ㅂㅉ", reviews=[한글과자한줌, 과일사탕한줌].
- 배송/주문/문자/도착/CS 는 최상위 shipping_cs 하나. 이걸로 제품 항목(reviews)을 만들지 마라.
- reviews[].mentioned_product 엔 '제품명'만. 마켓 초성을 제품명에 섞지 마라.
- 한 글이 **여러 마켓**의 제품을 나열하면(예: "ㅇㅍㅋ 든든장작 … ㅅㄹㄹ 약과볼") 각 항목의
  reviews[].mentioned_market 에 그 항목의 마켓을 적는다. 글 전체가 한 마켓이면 거긴 null 로
  두고 최상위 market 에만 넣는다(중복 기재 금지).

[추측 금지]
- texture.feel 은 본문에 통제어휘 표현이 실제로 나온 경우에만 채운다. 없으면 빈 배열 [].
  통제어휘: {", ".join(FEEL_VOCAB)}. (예: 본문에 '쫀득'이 없으면 절대 넣지 마라.)
- '~같은' 비유는 feel_simile, 통제어휘에 없는 '질감' 새 표현만 feel_other 에 원문 그대로.
- 어떤 속성이 언급 안 됐으면 그 블록 전체를 null. 빈 값으로 채운 블록을 만들지 마라.

[필드 구분 — 자주 헷갈림]
- sound(소리): 비즈/재료가 서로 부딪혀 나는 소리도 여기. 예) '걀걀거림'은 비즈 부딪는 청각 신호 → sound.
  (소리 관련 의성어를 texture.feel/feel_other 로 보내지 마라.)
- longevity(지속력): '슬라임이 빨리 죽었다 / 오래 살아있다' 등 시간 경과 후 제품 수명만.
  배송이 빠르다/늦다/아직 배송중은 지속력과 무관 → longevity 아님.
- shipping_cs(최상위): 배송/문자/도착/교환·CS 관련은 전부 여기(후기 단위).
- texture.type_mentioned: 1층 종류({", ".join(TYPE_ENUM)})를 언급한 경우만.
  비즈/글리터 같은 구성요소는 종류가 아니다 → null.
- scent.vs_official_comment: 작성자가 '공식향과 다르다'고 직접 말한 경우만.

[전언(남의 경험) 배제 — reviews 는 작성자 본인이 겪은 것만]
- 한국어는 증거성을 어미로 표시한다. 전언 표지가 붙은 평가는 '남의 경험'이므로 항목으로 만들지 마라:
  '-대 / -다는데 / -다더라 / -다고 함 / -다길래 / -대서 / -라던데', '들었는데', '후기 보니', '다들 ~한다고',
  '친구가·누가·남들이 ~했다는'.
  예) "친구가 허니푸냥이 샀는데 걀걀거림 심하다고 함" → reviews: [] (걀걀거림 항목을 만들지 마라)
  예) "다들 좋다고 하는 것 중에 아직 안산게 A B C" → A·B·C 로 항목을 만들지 마라(본인이 안 만져봤다).
- 직접 지각 표지는 본인 경험이다 — 정상 추출한다:
  '-더라 / -던데'(직접 겪음. '-다더라'와 혼동 금지), '만져보니 / 만져봤는데', '써보니', '내 기준', '나는 ~했음'.
  예) "이거베이스 좋더라" → 정상 추출. "만져봤는데 좋았음" → 정상 추출.
- '아직 안 산 / 안 만져본 / 살까 고민중 / 장바구니에 담은' 제품은 **구매 후보 목록**이지 후기가 아니다.
  나열된 제품이 여럿이어도 항목을 만들지 마라 — 그 글에서 항목이 되는 건 작성자가 실제로 만져본 제품뿐이다.
  예) "다들 좋다고 하는 것 중에 아직 안산게 A B C D 인데 살말?" → A·B·C·D 전부 항목 없음.
- 만드는 항목마다 firsthand_evidence 에 **본인이 직접 써 봤음을 보여주는 원문 조각**을 넣는다.
  근거를 못 대겠으면 그 항목은 애초에 만들지 마라(= firsthand_evidence 가 null 인 항목은 버려진다).
- ⚠️ 과교정 금지: 전언 표지가 없으면 1인칭으로 간주한다. 본인 경험 후기·본인이 두 제품을 견준 비교글은
  전언이 아니다 — 의심만으로 버리지 마라. 배제하는 건 '표지가 명시된 전언'과 '미구매 후보 목록'뿐이다.

[다제품 비교 — 제품별 분리]
- 출력은 reviews 배열이다. 작성자가 '실제 사용 경험/평가'를 서술한 제품마다 항목 1개를 만든다.
  한 제품만 다루면 1개, 두 제품을 비교하면 2개. 단순 언급·제목·배송만 있는 건 항목으로 만들지 마라.
  (예: "ㅂㅉ 한줌" 줄은 마켓+라인 머리말일 뿐 → 그 자체로 'ㅂㅉ 한줌' 항목을 만들지 마라.)
- 같은 베이스명을 공유하면(예: '한줌') 구분어를 붙여 식별한다(한글과자한줌 vs 과일사탕한줌).
- 한 제품의 속성을 다른 제품 항목에 섞지 마라.
- 양쪽 반영은 작성자가 '두 제품을 직접 견준 축'(향·재미 등)에만 적용한다.
  예) "컨셉 향은 과일사탕이 더 취향" → 과일사탕 scent.sentiment=pos, 한글과자 scent.sentiment=neg.
- 한쪽 제품에만 해당하는 단점/장점은 '그 제품 항목에만' 넣고 다른 항목엔 복제하지 마라.
  예) "(과일사탕은) 걀걀거리는게 적고 비즈양 적은거같아" → 과일사탕 sound/texture 에만. 한글과자엔 넣지 마라.
- 주어가 생략된 평가는 '직전에 새로 등장한 제품'에 귀속한다(가장 가까운 맥락).
  불확실해도 양쪽 복제는 금지 — 문맥상 한 제품에만 넣는다.
  예) "과일사탕도 샀는데 / 걀걀거리는게 적고…" → 걀걀거림은 직전 주어인 과일사탕에만.

점수는 작성자가 직접 매긴 경우만 overall.stated_rating, 아니면 null.
overall.model_sentiment 는 텍스트 기반 모델 추정 라벨이다.
flags.toxic 은 후기 전체 기준(제품별 아님). 욕설/유해 표현이 있으면 true."""


LAYER2_THREAD_SYSTEM = LAYER2_SYSTEM + """

[스레드 배치 — 조각별로 따로 출력]
- 입력은 **한 스레드**다. 조각마다 [S0] [S1] … 번호가 붙어 있다(S0=글 본문, 이후는 댓글 순서).
- docs 배열에 **입력에 나온 조각마다 정확히 한 항목**을 만들고 source_id 에 그 번호를 그대로 적는다.
  조각을 합치거나 건너뛰지 마라. 평가가 없는 조각도 항목은 만들되 reviews: [] 로 둔다.
- 위의 모든 규칙(전언 배제·제품별 분리·미언급 null)은 **조각마다 개별 적용**한다.
- **문맥은 참조하되 내용은 섞지 마라.** 어떤 조각이 제품명을 생략하고 앞 조각을 받아 말하면,
  그 제품이 무엇인지 앞 조각에서 찾아 mentioned_product 에 적는다. 하지만 앞 조각의 평가를
  그 조각으로 복사하지는 마라 — 각 항목은 그 조각이 실제로 말한 것만 담는다.
  예) [S1] "카피바라랑 푸냥이 중 뭐가 나아?" / [S2] "웅 근데 향이 좀 에바ㅠ"
      → S2 의 mentioned_product 는 앞 문맥이 가리키는 제품. S1 에는 평가 항목을 만들지 마라.

[제품 후보 — 있을 때만]
- 머리말에 `[제품 후보]` 줄이 있으면 `이름(초성·약칭)` 목록이다. 이 갤은 제품을 초성·약칭으로
  부른다(`ㅂㅇㅍ 밀버크`, `아바`) — 그 표기를 **제품으로 알아보라고** 주는 참고 자료다.
- **쓰임은 하나뿐: 본문에 실제로 나온 표기를 정규 이름으로 펴는 것.** 본문의 표기를 목록에서
  찾아 그 정규 이름을 mentioned_product 에 적는다.
- ⛔ 목록에 있다는 이유로 **본문에 없는 제품을 적지 마라.** 이 목록은 후보지 정답이 아니다.
  본문 어디에도 근거가 없으면 mentioned_product 는 null 이다(미언급 → null, 1급 규칙).
- ⛔ 어느 이름인지 애매하면(표기 하나가 여러 후보에 걸리면) 고르지 말고 null 로 둬라."""


# ---------------------------------------------------------------- 1층 스키마 (판매자 → 공식 스펙)
# 판매자(마켓 본인) 캡션에서 공식 스펙을 추출한다. 2층(후기)과 대칭 구조지만
# '주관 평가'가 아니라 '객관 스펙'(향료/풀조합/종류)만 뽑는다. 미언급은 null(§10).
_SPEC_PROPS: dict = {
    "product": {"type": ["string", "null"],
                "description": "제품명. 캡션에 제품명이 명시된 경우만. 없으면 null."},
    "scent": {"type": ["string", "null"],
              "description": "향료(공식 표기). 예: '레몬커드', '라벤더'. 미언급 null."},
    "base_combo": {"type": ["string", "null"],
                   "description": "풀조합(glue composition). 예: '투명풀+활성제'. 미언급 null."},
    # 종류(통제어휘). 목록 밖/미언급이면 null. **배열**인 이유: 토핑이 믹스된 제품은 종류가
    # 둘이다(사용자 규칙 2026-08-09 — 화이트쿠키넛=빈백+폼볼). fixture 경로(`layer1.iter_specs`)는
    # 원래부터 TYPE_ENUM 배열을 콤마결합해 넣었는데 추출 경로만 단일 enum이라 두 값을 표현할
    # 방법이 없었다. `extract_spec` 이 반환 직전에 콤마결합 문자열로 정규화하므로 소비처
    # (`_specs_from_seller_post` → `specs.slime_type` TEXT)는 그대로다.
    "slime_type": {"anyOf": [{"type": "array", "items": {"type": "string", "enum": TYPE_ENUM}},
                             {"type": "null"}]},
    "official_texture": {"type": ["string", "null"],
                         "description": "판매자가 캡션에 직접 쓴 '만졌을 때의 질감' 서술을 1~2문장으로 "
                                        "요약. 캡션의 표현·어휘를 그대로 살려 압축만 한다(재해석·미화 금지). "
                                        "문장은 '~해요'체로 끝낸다(어휘는 캡션 그대로, 종결어미만 통일). "
                                        "질감 얘기가 없으면 null."},
    "beads": {"type": "array", "items": {"type": "string"},
              "description": "제품에 포함된 비즈/토핑 구성요소 목록(오픈 어휘, 마켓별 명칭 상이: "
                             "지렁이비즈·나뭇잎비즈·퍼즐비즈·별비즈 등). 캡션 표기 그대로. 비즈 없으면 []."},
    "evidence": _nstr(),               # 근거 원문 조각(짧게)
}

LAYER1_SCHEMA: dict = _obj({
    "products": {"type": "array", "items": _obj(_SPEC_PROPS),
                 "description": "캡션이 소개한 제품마다 한 항목. 여러 제품이면 배열. "
                                "스펙이 하나도 없으면 빈 배열 []."},
})

LAYER1_SYSTEM = f"""\
너는 슬라임 '마켓(판매자) 본인'이 올린 게시물 캡션에서 공식 제품 스펙만 구조화한다.
이건 후기가 아니라 '공식 정보원'이다 — **구매자 평가**(다른 사람이 좋다/나쁘다 한 말)는 무시한다.
다만 판매자가 자기 제품을 직접 서술한 '질감 설명'은 공식 정보다 → official_texture 로 뽑는다.

[제품명 = 제품 고유 해시태그]
- product: 제품 고유 해시태그(#제품명)의 텍스트를 그대로 쓴다. 예) '#레몬커드쉘도넛' → '레몬커드쉘도넛'.
- 무시하는 태그는 '마켓 자기 이름'(머머·머머슬라임 등 판매자 핸들/상호)과 '광역 슬라임어'(슬라임·slime·
  slimereview)뿐이다. 그 외 고유 해시태그는 '샵·캔디·스토어·펜션' 같은 단어가 들어 있어도 제품명이다.
  예) '#위즈캔디샵' → 제품 '위즈캔디샵'(가게 이름이 아니라 캔디샵 컨셉 제품명). 태그가 하나뿐이고
  광역/마켓명이 아니면 그게 제품명이다 — 함부로 버리지 말 것.
- 제품 고유 해시태그가 없으면 정식 제품명이 없는 것 → 설명(스쿱 점토, 8mm디폼, 빨대 등)을 제품명으로
  지어내지 말 것.
- 비즈·토핑·파츠(지렁이비즈·별비즈·퍼즐비즈·감자칩비즈·리본블럭 등)는 제품이 아니라 구성요소다
  → 별도 product 로 만들지 말 것. 대신 해당 제품의 beads 배열에 캡션 표기 그대로 넣는다(예: '지렁이비즈').
  base_combo 에는 넣지 마라(base_combo=풀 재료만). 비즈가 없으면 beads=[].

[비매품·미출시 제외]
- '비매품' 목록글(예: '6월마켓 비매품 1.…')이나 출고/오픈/랜덤박스 등 공지글의 아이템은 아직 정식
  출시·명명되지 않은 비매품이다 → products 에 넣지 않는다(스킵).
- 판단선은 '비매' 단어 유무가 아니라 '제품 고유 해시태그' 유무다. 어떤 제품이 "지난 마켓 비매품
  베이스를 …"처럼 과거 비매품을 언급만 하고 자기 제품 해시태그를 가지면 그건 출시 제품 → 정상 추출한다.

[뽑을 것]
- scent: 향료(음식·맛 이름) 공식 표기 — 예) 딸기우유, 에그노그, 솜사탕, 레몬커드. 여러 향이면 대표 표기 그대로.
- base_combo: 풀/베이스 재료(글루·활성제·점토류)만 — 예) '아마존 쁠루모 글루올 플레인'.
  ⚠️ 향료(맛 이름)는 base_combo 에 넣지 말 것 — scent 로 분리한다. 비즈·토핑·규격(6mm·40g·비즈g)도 제외.
  캡션이 '풀조합 / 향료 규격' 처럼 ' / ' 로 나뉘면 ' / ' 앞(풀조합)만 base_combo, 뒤 향료는 scent 로 간다.
  📍 캡션 첫머리(해시태그 줄 직후, 본문 문단 전)의 짧은 나열 줄이 **정본**이다. 본문 산문의
  '베이스는 ~' 언급은 다른 제품과의 비교이거나 질감 서술이므로 base_combo 로 쓰지 마라.
  머리말 줄이 둘이면 풀조합·향 순서는 고정이 아니니 내용으로 판별한다.
  ⚠️ 그 줄의 재료는 **하나도 빠뜨리지 말고 전부** 옮긴다 — '컬글 스쿨' 을 '컬글' 로, '착풀
  아이뉴클 점토' 를 '착풀 아이뉴클' 로 줄이지 마라. 다만 괄호 주석('(크런키 베이스)')은
  재료가 아니므로 제외한다.
- slime_type: 종류 통제어휘 안에서만 고른 **배열** — {", ".join(TYPE_ENUM)}. 목록 밖이면 null.
  🧩 **토핑이 믹스된 제품은 둘 다 적는다**(사용자 규칙 2026-08-09) — 예) 마블폼볼과 마블빈백이
  같이 든 '화이트쿠키넛' → ["빈백", "폼볼"]. 하나면 원소 하나짜리 배열, 미언급이면 null.
  📏 '6mm'·'8mm'·'7-9미리' 같은 mm 규격은 **알갱이 지름**이지 종류가 아니다 — 디폼도 폼볼도
  mm 로 표기한다. 예) '7-9미리 폼볼 내장' → 폼볼, '6미리 디폼 내장' → 디폼.
  **캡션이 쓴 종류어를 그대로 따르고**, mm 만 있고 종류어가 없을 때만 '디폼'으로 기본값을 잡는다.
  (제품명·테마어에 낚이지 말 것: '곰돌디핑'·'꼬끄카롱'은 음식 테마지 종류가 아니다.)
  🧱 **풀조합에 '점토'가 있다고 촉감류가 아니다**(사용자 규칙 2026-08-09). '촉감류(점토)'는
  점토가 **주재료**일 때만 쓴다 — 여러 글루 나열 끝에 '점토'가 한 항목으로 끼어 있는 건
  대개 소량 첨가라 촉감류가 아니다('점토소량' 명시면 확실히 아니다).
  🧷 통제어휘는 두 갈래다(사용자 규칙 2026-08-09).
    · **내장물형** — 폼볼·디폼·빈백·빨대·라이스볼·수수깡·자바칩·난사·스티로폼·납작블럭. 캡션
      머리말의 '6미리 디폼 내장'·'자바칩 내장'·'납작블럭 40g내장' 줄에 적히고 beads 에도
      같은 말이 들어간다. **'납작블록'(블'록')도 같은 종류어다** — 통제어휘 표기 '납작블럭'으로
      적는다(마켓마다 표기가 갈린다: 머머·빈짱·예찬 '블럭' / 베이퍼·진통제 '블록').
      ⚠️ 네모블럭·샌드블럭·원형블럭·하트블럭 등 **다른 블럭은 통제어휘에 없다** — 종류로
      적지 말고 beads 에만 캡션 표기 그대로 넣는다.
    · **베이스 성격어** — 촉감류(점토)·클리어·크런치·버글리·지글리·샤베트·젤라또·클라우드·눈꽃.
      베이스 자체의 제형·투명도·소리다.
  **내장물이 우선이되, 성격어도 함께 성립하면 둘 다 적는다** — 성격어를 앞에 둔다.
  예) 6미리 디폼이 든 크런키 → ["크런치","디폼"] · 수수깡 든 물젤리 → ["클리어","수수깡"]
      · 마블폼볼+마블빈백 → ["빈백","폼볼"] · 내장물이 없으면 성격어만 → ["클리어"]
  머리말에 내장물이 적혀 있으면 그게 정본이고, 본문 산문의 '촉감류'라는 표현보다 **우선**한다.
  예) 빠코 계열은 풀조합이 전부 '아마존 우드 점토'로 같지만 종류는 내장물로 갈린다 —
  빠코볼(폼볼 내장)=폼볼, 빠코깡(수수깡)=수수깡, 빠코디(6미리 디폼)=디폼, 빠코폼=빈백.
- official_texture: 판매자가 쓴 **'만졌을 때 어떤가'** 서술을 1~2문장으로 압축. slime_type 이
  '무슨 종류인가'라면 이건 '만지면 어떤가'다 — 종류어만 반복하지 말고 서술을 남겨라.
  예) '기포 차기 전엔 탱글 뻐등하다가 기포가 차면 몽글해지고, 밀도가 높아 묵직한 편이에요.'
  문장은 **'~해요'체**로 끝낸다(스펙 카드에 그대로 나가는데 화면 카피가 전부 해요체다).
  ⚠️ 바꾸는 건 **종결어미뿐**이다 — 캡션이 쓴 촉감 어휘('뻐등', '도골도골', '까드득')는
     한 글자도 갈아치우지 말고 그대로 둔다. 말투 통일이 재서술의 핑계가 되면 안 된다.
  ⚠️ 캡션의 표현·어휘를 그대로 살려 **압축만** 한다 — 없던 형용사를 붙이거나 좋게 다듬지 마라.
  손붙음·부풀기·농도별 차이·무게감처럼 판매자가 명시한 촉감 정보는 여기 함께 담는다.
  컨셉·디자인·파츠·보관법·향 설명은 질감이 아니다 → 넣지 마라. 질감 언급이 없으면 null.
- beads: 제품에 **든 것**(비즈·토핑·내장물)을 캡션 표기 그대로 배열로 — 예) ['지렁이비즈', '퍼즐비즈'].
  ⚠️ **폼볼·디폼 같은 내장 알갱이도 여기 넣는다**(사용자 규칙 2026-08-06). slime_type 과 겹쳐도
  중복이 아니다: slime_type 은 '이 슬라임이 무슨 종류인가', beads 는 '안에 뭐가 들었나'다.
  구매자는 뒤쪽을 보고 고른다. 크기 표기가 붙어 있으면 붙은 채로 넣는다 — 예) '7-9미리 폼볼',
  '8미리 디폼', '6디폼 스팽글'. 없으면 [].

[원칙]
- 캡션에 '명시'된 것만. 안 나온 필드는 null. 추측·창작 금지(§10).
- 각 필드 근거를 evidence 에 짧게(15자 내외).
- 제품 고유 해시태그가 여럿이면 products 배열에 제품 수만큼. 출시 제품이 하나도 없으면 [].
- 할인/이벤트/배송 안내 등 스펙과 무관한 홍보 문구는 무시한다."""


_HASHTAG_RE = re.compile(r"#([0-9A-Za-z_가-힣]+)")


def hashtags_in(text: str) -> list[str]:
    """캡션의 해시태그 목록(순서 유지·중복 제거).

    []이면 해시태그가 하나도 없는 것 = 비매품/공지글 → 스펙으로 저장하지 않는다(결정적 게이트).
    사용자 규칙(memo layer1-product-name-hashtag-rule): '해시태그 없으면 실제 제품 아님'.

    ⚠️ 여기서는 **거르지 않는다** — 캡션에 있는 태그 전부를 순서대로 준다.
       제품명 후보는 `product_hashtags()` 를 쓸 것(마켓명·광역어 제외).
    """
    out: list[str] = []
    for tag in _HASHTAG_RE.findall(text or ""):
        if tag not in out:
            out.append(tag)
    return out


# 광역 슬라임어 — 어느 마켓 캡션에나 붙을 수 있어 제품명이 될 수 없다.
# 광역 태그 — 어떤 마켓의 제품명도 될 수 없는 커뮤니티/분류 태그. 제품명 후보에서 상시 뺀다.
# ⚠️ 여기 없는 광역 태그는 **제품이 된다.** 실측(2026-08-07): `슬라임리뷰` 가 한글판이 없어
#   `빅말차쿠키디 → 슬라임리뷰` 로 복구될 뻔했다 — 영어 `slimereview` 만 있었다.
# ⚠️ 넣는 기준은 '실제 코퍼스에서 관측됐고 제품명일 수 없는 **완전일치** 태그'다. 부분일치
#   규칙으로 넓히지 말 것 — `#위즈캔디샵` 같은 고유 제품명이 '샵' 때문에 사라진다.
# ⚠️ 개인 태그(`#두통픽`·`#소망픽`·`#꼼픽`)는 여기 못 넣는다 — 마켓/사용자마다 달라 열거가
#   불가능하다. 그건 마켓별 제품 목록(KB `products`)으로만 갈린다.
GENERIC_TAGS = frozenset({
    "슬라임", "슬라임샵", "슬라임마켓", "슬라임스타그램", "슬라임추천", "국내슬라임",
    "slime", "slimeshop", "slimereview", "slimes", "asmr", "슬라임asmr",
    # 2026-08-07 코퍼스 실측 추가 — 인스타 34개 글의 고유 태그 42종에서 관측된 광역어
    "슬라임후기", "슬라임리뷰", "슬라임영상", "슬라임계정", "슬계맞팔",
    "폼볼슬라임", "slimeasmr", "slimevideo", "floamslime", "satisfying",
})


# ---------------------------------------------------------------- 비제품 라벨
# 제품이 아니라 **판매 형식**을 가리키는 말. 추출기가 `mentioned_product` 로 들어올리면
# '비매'라는 이름의 유령 제품이 마켓마다 하나씩 생긴다(실측 2026-08-10: 11행 / 8개 이름).
#
# ⚠️ **이 목록은 부분일치로 쓰면 안 된다.** 실측 반례가 둘 다 실재한다:
#   · `나비매듭`·`말차수플레` — 진짜 제품명 안에 `비매`·`차수` 가 들어간다(과잉 차단).
#   · `연찌비매17`·`푸딩비매품`·`웨이즈1월비매` — **1층 `specs` 에 실재하는 제품**이다.
#     연찌·웨이즈는 비매품에 번호를 붙여 해시태그로 파는데, 그건 판매 형식이 아니라
#     그 마켓의 제품 식별자다(계획서가 예상 못 한 실측 — specs 64행이 이 모양이다).
# 그래서 판정은 두 갈래로 갈린다(`is_non_product_label` 참조): **맨몸 라벨**은 무조건
# 비제품이고, **수식된 라벨**은 1층/레지스트리가 모르는 이름일 때만 비제품이다.
NON_PRODUCT_LABELS = frozenset({"비매", "비매품"})       # 접미로 붙는다: 'X의 비매품'
NON_PRODUCT_LABELS_EXACT = frozenset({"차수", "랜덤박스", "랜박", "랜덤팩"})
# 지시어 접두 — '이번 비매'는 '비매'와 같은 말이다. 제품명의 일부가 될 수 없다.
_LABEL_DEICTICS = ("이번", "저번", "지난", "다음", "요번")
# 꼬리 수량 표기 — `비매5`·`비매품 1번`·`3차수`. 이것만으로는 제품 식별이 안 된다.
_LABEL_TRAILING_RE = re.compile(r"(?:\d+\s*(?:번|차|호)?|번)\s*$")
_LABEL_LEADING_RE = re.compile(r"^\d+\s*")


def _label_core(name: str) -> str:
    """라벨 판정용 축약형 — 꼬리 수량과 지시어 접두를 벗긴 몸통(공백 제거)."""
    s = _norm(name)
    for _ in range(3):                           # `비매품 1번` 처럼 두 겹인 경우
        m = _LABEL_TRAILING_RE.search(s)
        if not m or m.start() == 0:
            break
        s = s[:m.start()]
    for d in _LABEL_DEICTICS:
        if s.startswith(d) and len(s) > len(d):
            s = s[len(d):]
            break
    # 앞자리 회차 표기 — `3차수`. 뒤가 아니라 **앞**에 붙는 형태라 위 꼬리 정규식이 못 잡는다.
    # 벗긴 뒤 `NON_PRODUCT_LABELS_EXACT` 와 **완전일치**해야 라벨이므로, 숫자로 시작하는
    # 진짜 제품명(`4pm스낵` 류)이 여기서 잘려도 판정에는 닿지 않는다.
    if (m := _LABEL_LEADING_RE.match(s)) and m.end() < len(s):
        s = s[m.end():]
    return s


def is_non_product_label(name: str | None, known_products=None) -> bool:
    """이 이름이 제품이 아니라 **판매 형식 라벨**인가. 순수 함수(무LLM·무DB·무네트워크).

    세 갈래다. 갈리는 축은 **이름이 라벨 말고 무엇을 더 들고 있는가**이고, 갈래마다
    증거(`known_products`)를 얼마나 신뢰하는지가 다르다.

      ① **맨몸**(`비매`·`비매품`·`차수`·`랜덤박스`) — 이름이 라벨 그 자체다. 무조건 비제품.
         ⚠️ 여기서 `known_products` 를 보면 **안 된다**: 레지스트리에 판매자가 실제로 단
           `#비매품` 태그가 후보로 올라와 있어(실측), 증거를 물으면 맨몸 라벨이 되살아난다.
      ② **수량만 붙음**(`비매5`·`비매품 1번`·`이번비매`·`3차수`) — 라벨 + 숫자/지시어뿐이다.
         원칙적으로는 식별 정보가 없지만 **반례가 있다**: `비매품50` 은 연찌가 해시태그로 파는
         실제 제품이다(`#비매품50`). 그래서 증거가 있으면 제품으로 본다. 증거가 **없으면**
         비제품으로 본다 — 라벨+숫자는 그 자체로는 아무 마켓도 가리키지 못하기 때문이다.
      ③ **수식어가 붙음**(`베이퍼비매`·`교동 지글리 비매`) — 라벨로 **끝나되** 앞에 낱말이 있다.
         `연찌비매17`·`푸딩비매품` 이 같은 모양인데 **1층에 실재하는 제품**이라 구조만으로는
         못 가른다. 증거가 없으면 **건드리지 않는다**(페일세이프) — 증거 없이 지우는 쪽이
         화면에 안 보이는 손실이라 더 나쁘다(`enforce_product_vocab` 의 ③과 같은 규칙).

    `known_products`: 1층 `specs` + 제품 후보 레지스트리의 이름들(**마켓 무관 전량**).
      ⚠️ 마켓별로도, 스레드별로도 좁히지 말 것 — 여기서 묻는 건 '이 마켓의 제품인가'가 아니라
        '이 표기가 누군가의 제품명으로 실재하는가'다. 좁히면 마켓을 아직 모르는 조각
        (=디시의 절반)에서 ②③이 전부 지워지고, 같은 이름에 경로마다 다른 판정이 붙는다.
        재료는 `pipeline.known_product_names()` 한 벌이다.
    """
    raw = _norm(name or "")
    core = _label_core(raw)
    if not core:
        return False
    is_label_core = core in NON_PRODUCT_LABELS or core in NON_PRODUCT_LABELS_EXACT
    if is_label_core and raw == core:
        return True                              # ① 맨몸 — 증거를 묻지 않는다
    known = {_norm_tag(p) for p in (known_products or ())}
    if is_label_core:
        return _norm_tag(raw) not in known       # ② 수량 — 증거 없으면 비제품
    if not any(core.endswith(lbl) for lbl in NON_PRODUCT_LABELS):
        return False
    if not known:
        return False                             # ③ 증거 없음 → 건드리지 않는다
    return _norm_tag(raw) not in known


# ---------------------------------------------------------------- 비제품 '단어'
# 라벨(`비매`·`차수`)이 **판매 형식**을 가리킨다면, 이쪽은 **종류어·재료어·조각난 이름**이다.
# 둘 다 제품이 아니지만 막는 실패가 달라서 게이트도 카운터도 가른다(합치면 어느 게이트가
# 일했는지 사후에 못 가른다 — `llm_calls_saved` 를 가른 것과 같은 이유).
#
# ⚠️ **완전일치로만 쓴다.** 실측(2026-08-10, `specs` 제품명 1,980개): 종류어·재료어와
#   **완전히 같은** 제품명은 0개인데, 그 단어를 **품은** 제품명은 16개다(`내리꽃디폼`·
#   `베이직우드폼`·`말차초코크런치바`·`허밍크런치`…). 부분일치로 넓히면 그 16개가 통째로
#   사라지고, 그 손실은 화면에 안 보인다(유령 제품과 반대 방향의, 더 알아채기 어려운 실패).
#   `is_non_product_label` 이 `나비매듭` 때문에 부분일치를 금지한 것과 같은 자리다.

# 풀·베이스 재료어. 1층 `base_combo` 어휘에서 왔다 — 캡션의 **스펙 줄**이 제품명으로
# 들어올려진 자국이다(인스타에서 `아마존 우드 점토` 가 제품 행 8건을 만든 그 실패의 디시판).
GLUE_WORDS = frozenset({
    "글루올", "택키", "아마존", "우드", "우마존", "생베", "점토", "화이트글루", "글리",
})
# '이름이 기억 안 난다'는 **명시적 표지**. 이게 붙은 이름은 제품 식별자가 아니다.
FRAGMENT_MARKERS = ("어쩌구", "어쩌고", "어쩍고")
# 자모만으로 된 이름 — 제품명일 수 없다(KB 안 `ㅇㅊ` 이든 KB 밖 `ㅅㄱㄷ` 이든).
# ⚠️ **호출 순서가 이 규칙의 안전조건이다.** 이 함수는 KB 를 모르므로 `ㅇㅊ` 가 마켓 표기라는
#   걸 알 방법이 없다 — 그냥 비운다. 그래서 **반드시 `linking.split_market_prefix` 로 마켓을
#   떼어 낸 뒤에** 불러야 한다. 앞에서 부르면 마켓 신호가 통째로 사라지고, 그 행은 스레드
#   도장 마켓을 0.95 로 물려받는다(되돌릴 표식 없는 오귀속 — NULL 보다 나쁘다).
#   그 순서를 지키는 곳은 둘뿐이다: `linking.link`(수집 경로) · `pipeline.dc_attribution_target`
#   (적재분 복구). 그래서 `extract_thread` 는 이 게이트를 **부르지 않는다**.
_ALL_JAMO_RE = re.compile(r"^[ㄱ-ㅎ\s]+$")


def is_non_product_word(name: str | None) -> bool:
    """이 이름이 제품이 아니라 **종류어·재료어·조각난 이름**인가. 순수 함수(무LLM·무DB·무KB).

    `is_non_product_label` 의 형제다. 넷 중 하나면 True:
      ① 종류 통제어휘와 **완전일치**(`디폼`·`클리어`·`수수깡`·`빨대`·`빈백`·`크런치`…).
         사용자 규칙: 종류어는 제품명이 아니다(→ [MEMORY.md] 슬라임 속성 어휘 분류 규칙).
      ② 풀·베이스 재료어와 **완전일치**(`글루올`·`아마존`·`점토`…).
      ③ 이름에 '기억 안 남' 표지가 붙어 있다(`버블버블 어쩌구`).
      ④ 이름이 자모뿐이다(`ㅅㄱㄷ`).
    ①②는 **완전일치 전용**이다 — 위 상수 주석의 실측 근거 참조. 합성어는 건드리지 않는다.
    """
    core = _norm(name or "")
    if not core:
        return False
    if core in {_norm(t) for t in TYPE_ENUM} or core in GLUE_WORDS:
        return True
    if any(m in core for m in FRAGMENT_MARKERS):
        return True
    return bool(_ALL_JAMO_RE.match(name or ""))


def drop_non_product_words(doc: dict) -> int:
    """`mentioned_product` 가 비제품 단어면 `None` 으로 비운다. 반환: 비운 건수.

    `drop_non_product_labels` 와 **따로 센다** — 둘은 아예 다른 실패를 막는다(판매 형식어 vs
    종류어·재료어). 여기서도 **행은 버리지 않는다**: 제품 귀속만 사라지고 그 조각의 배송·CS 는
    마켓 축(ADR-0015) 집계에 그대로 남는다.
    """
    dropped = 0
    for rv in (doc.get("reviews") or []):
        if is_non_product_word(rv.get("mentioned_product")):
            rv["mentioned_product"] = None
            dropped += 1
    return dropped


def drop_non_product_labels(doc: dict, known_products=None) -> int:
    """`mentioned_product` 가 비제품 라벨이면 `None` 으로 비운다. 반환: 비운 건수.

    **후기 항목 자체는 버리지 않는다** — 1급 규칙은 '미언급 → null' 이지 '드롭'이 아니다.
    제품 귀속만 사라지고 마켓 축(배송·CS) 집계에는 그대로 남는다.

    ⚠️ 소스마다 적용 자리가 다르지만 **규칙은 `is_non_product_label` 한 벌**이다:
      인스타는 `repair_product_names` 머리(해시태그 게이트 **앞**), 디시는 `extract_thread`.
      해시태그 게이트 뒤에 두면 디시엔 아예 안 돈다 — 그래서 `비매품 1번` 이 살아남았다.
    """
    dropped = 0
    for rv in (doc.get("reviews") or []):
        if is_non_product_label(rv.get("mentioned_product"), known_products):
            rv["mentioned_product"] = None
            dropped += 1
    return dropped


def _norm_tag(t: str) -> str:
    """태그 비교용 정규화: 공백·`_`·`.` 제거 + 소문자.

    구분자를 벗기는 이유는 IG 핸들이 `slime_gina_`·`from.murmurslime`·`bom__slime` 처럼
    구분자를 쓰는데, 캡션 해시태그는 `#SlimeGina` 처럼 붙여 쓰기 때문이다. 안 벗기면
    같은 마켓 이름인데도 제외 집합에 안 걸려 유령 제품이 다시 생긴다.
    """
    return re.sub(r"[\s_.]+", "", t or "").lower()


def product_hashtags(text: str, *, exclude: "set[str] | frozenset[str] | None" = None) -> list[str]:
    """제품명 **후보** 해시태그 — 마켓 자기이름과 광역 슬라임어를 뺀 나머지.

    사용자 규칙(2026-08-06): **제품명은 해시태그 중 '하나'이지 모든 해시태그가 아니다.**
    실측 계기: @slime_gina_ 는 게시물마다 `#슬라임지나 #제품명` 두 개를 다는데, 마켓 태그까지
    제품명으로 통과시키면 **마켓마다 자기 이름의 유령 제품 행**이 하나씩 생긴다(스펙은 같은
    캡션의 진짜 제품과 동일하게 복제된다 — 조용해서 더 나쁘다).

    `exclude` 에는 그 마켓의 상호·핸들·별칭을 넣는다(KB 에서 조립). 광역어는 상시 제외다.
    비교는 공백 제거 + 소문자로 한다 — `#슬라임 지나`/`#SlimeGina` 같은 표기 흔들림 때문.

    ⚠️ 과잉 제외 금지: '샵·캔디·스토어' 가 들어가도 고유 태그면 제품명이다
       (예: `#위즈캔디샵` → 제품 '위즈캔디샵'). 여기서 거르는 건 마켓명과 광역어**뿐**이다.
    """
    ex = {_norm_tag(e) for e in (exclude or set())} | {_norm_tag(g) for g in GENERIC_TAGS}
    return [t for t in hashtags_in(text) if _norm_tag(t) not in ex]


def market_tag_exclusions(market: dict) -> set[str]:
    """KB 마켓 레코드 → `product_hashtags(exclude=...)` 에 넣을 자기이름 집합.

    상호(`market`)·표시어(`market_word`)·핸들(`handle`,`handles_alt`)·별칭(`aliases`)을 모은다.
    초성(`choseong`)은 넣지 않는다 — 제품 태그와 충돌할 만큼 짧고, 해시태그로 잘 쓰이지 않는다.
    """
    keys = ("market", "market_word", "handle")
    out = {market.get(k) for k in keys if market.get(k)}
    for k in ("handles_alt", "aliases"):
        out |= {v for v in (market.get(k) or []) if v}
    return out


def extract_spec(text: str, llm: LLM, model: str | None = None) -> dict:
    """
    판매자 캡션 한 건 → 1층 공식 스펙(dict):
    {"products": [{product, scent, base_combo, slime_type, official_texture, beads, evidence}...]}.
    market 은 추출하지 않는다 — 판매자 핸들→market_word 매핑(bias.partition)이 이미 알고 있어 주입한다.
    스키마 강제 + 파싱 실패 1회 재시도.

    ⚠️ 호출 전 게이트: product_hashtags(text) 가 []이면 비매품/공지글이므로 이 함수를 부르지 말 것
    (extract_spec 은 텍스트만 보고 설명형 이름을 지어낼 수 있어, 스킵은 호출부의 결정적 게이트가 담당).

    ⚠️ `slime_type` 은 스키마상 **배열**이지만 반환 직전에 콤마결합 문자열로 정규화한다 —
      `specs.slime_type` 이 TEXT 이고 fixture 경로(`layer1.iter_specs`)가 이미 같은 결합을 쓴다.
      정규화를 소비처로 미루면 두 경로가 서로 다른 타입을 넣어 조용히 갈린다.
    """
    doc = llm.complete(
        text,
        system=LAYER1_SYSTEM,
        schema=LAYER1_SCHEMA,
        model=model,
        label="extract.layer1",
    )
    for p in (doc or {}).get("products") or []:
        p["slime_type"] = _join_types(p.get("slime_type"))
    return doc


def _join_types(value) -> str | None:
    """TYPE_ENUM 배열 → 콤마결합 문자열. 빈 배열·빈 문자열은 null 로 접는다.

    구모델·재시도가 문자열을 그대로 줄 수 있어 두 모양을 다 받는다. 빈 배열을 ''로 두면
    `_PRODUCTHOOD_FIELDS` 의 truthiness 판정은 통과시키면서 화면엔 빈 칸이 나간다.
    """
    if isinstance(value, str):
        value = [value]
    joined = ", ".join(v.strip() for v in (value or []) if v and v.strip())
    return joined or None


def _norm(text: str) -> str:
    return "".join((text or "").split())


# 근거가 '제품명 재기입'인지 가르는 잔여 길이 하한. **1** = 제품명과 정확히 같을 때만 버린다.
# ⚠️ 2 로 올리지 말 것 — 이 갤의 평점 어휘엔 **1음절**이 있다. 실측(2026-08-10 아모스갤):
#   `잭두콩 썸`·`허밍 썸`·`미봉 썸` 등 **7행**이 잔여 1자인데 전부 진짜 보유 평가다.
#   2 로 두면 그 7행이 죽는데, 정작 같은 글의 `핑키별 쏘쏘`(2음절)는 살아남아 **한 평점
#   나열의 절반만 사라진다** — 조용하고 앞뒤가 안 맞는 손실이다.
#   계획서의 구속 정정(스레드 142738 은 `이렇개 만져봤고` 라고 밝힌 보유 평가글)이 정확히 이 자리다.
# 이 값이 겨냥하는 실측 대상은 잔여 **0자**(제품명 완전 재기입) 29행이고, 1 이면 그건 그대로 잡는다.
_EVIDENCE_MIN_RESIDUE = 1


def _evidence_is_just_the_name(evidence: str, product: str | None) -> bool:
    """근거에서 제품명을 뺀 나머지가 `_EVIDENCE_MIN_RESIDUE` 자 미만인가.

    `firsthand_evidence='바질토마토블렌디드'` 는 **제품명을 다시 적은 것**이지 본인이 써 봤다는
    근거가 아니다. 앞의 세 겹은 이걸 못 잡는다 — 제품명은 당연히 원문에 있고(②를 통과),
    전언·구매예정 표지도 없다(③④를 통과). 실측(2026-08-10 아모스갤): 근거가 제품명과 **정확히
    일치**하는 행이 30건이었다.

    ⚠️ **컷 기준은 '잔여 길이'이지 '제품명 포함'이 아니다.** 제품명+평가어(`새튀반 좋았고`,
      `카피바라 조음`)는 정상 근거이고, 실측상 제품명+4자 이내가 116행이다 — 그쪽을 자르면
      진짜 후기가 죽는다. 회수 손실은 화면에 안 보이고, 그중 부정 후기의 손실은 1급 기능인
      출처 편향을 직접 깎는다. 그래서 임계를 **1자**로 둔다(위 `_EVIDENCE_MIN_RESIDUE` 주석 —
      이 갤 평점 어휘엔 `썸` 같은 1음절이 있어서 2자면 진짜 평가 7행이 죽는다).
    """
    ev = _norm(evidence)
    if not ev:
        return True
    residue = ev.replace(_norm(product), "") if product else ev
    return len(residue) < _EVIDENCE_MIN_RESIDUE


def drop_hearsay_reviews(doc: dict, source_text: str = "") -> dict:
    """
    본인 경험 근거를 못 대는 항목 제거 — 전언 차단의 **결정적 게이트**(AC15).

    왜 코드로 막나: 프롬프트만으로는 안 잡힌다. 같은 입력(`dc-015`)을 반복 호출하면
    `[]` / `['ㅇㅉ거','ㅂ 유슬']` / `['ㅇㅉ거', 전언제품 4개]` 로 매번 달랐다(관측 누수율 ~1/14).
    가짜 후기 행 하나가 **디시 긍정 카운트를 부풀려** 1급 기능인 소스 편향을 왜곡하므로,
    비결정에 맡기지 않고 스키마 필드 + 코드 필터로 확정한다.

    세 겹으로 검사한다 — 앞의 것을 통과해도 뒤에서 걸린다:
      1) 근거가 비어 있으면 폐기.
      2) 근거가 원문에 실제로 없으면 폐기(**지어낸 인용**). evidence 는 사용자에게 보이는
         인용이기도 해서, 원문에 없는 문자열은 그 자체로 결함이다.
      3) 근거 조각 자체가 전언·미사용 표지를 담고 있으면 폐기 — "다들 좋다고 하는"을 근거로
         댔다면 그건 본인 경험의 근거가 아니다.
      4) 근거 조각이 **구매 예정 표지**를 담고 있으면 폐기 — `담았는데 우뗘??` 를 근거로 댄
         항목은 장바구니 목록이지 후기가 아니다(실측 아모스갤: 그 한 조각이 제품 6행을 냈다).
      5) 근거에서 **제품명을 뺀 나머지가 2자 미만**이면 폐기 — 제품명을 다시 적은 건 근거가
         아니다(`_evidence_is_just_the_name`, 실측 30행). 앞의 넷을 전부 통과하는 모양이라
         따로 있어야 한다.
    source_text 를 안 넘기면 2)는 건너뛴다(원문을 모르는 호출부 하위호환).

    ⚠️ 4)는 **좁게 유지한다.** 짧은 평점 나열(`잭두콩 썸`)·순위(`1믹스 2허밍`)·표지 없는
      질문은 진짜 보유 후기라 여기서 버리면 안 된다 — 회수 손실은 화면에 안 보이고,
      그중 부정 후기의 손실은 1급 기능(출처 편향)을 직접 깎는다. Q/E 순위가 이미 뒤로
      미루므로(ADR-0006/0017) 애매한 건 버리지 말고 순위에 맡긴다.
    """
    from . import relevance_rules as rules

    haystack = _norm(source_text)
    kept = []
    for r in (doc.get("reviews") or []):
        ev = (r.get("firsthand_evidence") or "").strip()
        if not ev:
            continue
        if haystack and _norm(ev) not in haystack:
            continue
        if rules.is_hearsay_span(ev):
            continue
        if rules.is_candidate_span(ev):
            continue
        if _evidence_is_just_the_name(ev, r.get("mentioned_product")):
            continue
        kept.append(r)
    doc["reviews"] = kept
    return doc


_ATTR_SLOTS = ("scent", "texture", "sound", "longevity")


def _filled_score(item: dict) -> int:
    """항목이 실제로 담고 있는 평가의 양 — 접을 때 어느 쪽을 남길지 정하는 데만 쓴다."""
    n = sum(1 for k in _ATTR_SLOTS if item.get(k))
    ov = item.get("overall") or {}
    return n * 2 + sum(1 for k in ("summary", "stated_rating") if ov.get(k))


def _held_fingerprint(item: dict) -> str:
    """보류(제품명 None) 항목의 **내용 지문** — 말더듬 판정에만 쓴다.

    제품명을 뺀 나머지 전부를 정렬된 JSON 으로 굳힌다. 키 순서가 달라도 같은 내용이면 같은
    지문이 나오게(`sort_keys`) 해야, 배치 응답의 키 순서 흔들림이 말더듬을 못 접게 만들지 않는다.
    """
    return json.dumps({k: v for k, v in item.items() if k != "mentioned_product"},
                      sort_keys=True, ensure_ascii=False)


def _fold_by_product(items: list[dict]) -> list[dict]:
    """같은 제품으로 접힌 항목을 하나로 병합 — **이중 계상 방지**.

    복구만 하고 안 접으면 유령 2행이 진짜 제품 2행이 될 뿐이다(AC3). 실측: `DLNVdrIzQdm` 은
    한 캡션에서 `아마존 우드 점토`(풀조합)와 `코코넛과자`(향)를 각각 제품으로 내보냈는데,
    둘 다 `빠코볼` 로 복구되면 한 사람의 한 의견이 빠코볼 후기 **2건**이 된다.
    ⚠️ 보류(None)는 **내용이 완전히 같을 때만** 접는다(추출기 말더듬). 내용이 다르면 서로
      다른 제품일 수 있고, 합치면 다른 의견이 한 건이 된다 — 아래 분기 주석 참조.
    """
    out: list[dict] = []
    seen: dict[str, int] = {}                    # 정규화 제품명 → out 인덱스
    held: set[str] = set()                       # 보류분 내용 지문
    for it in items:
        name = it.get("mentioned_product")
        if not name:
            # 보류(None)는 **내용이 완전히 같을 때만** 접는다 — 추출기 말더듬이 제거다.
            # 실측: 한 조각이 `아쿠아 자몽 후르츠 프쿠 썸파` 를 두고 내용이 글자 하나까지
            # 같은 항목을 3개 내보냈다. 이름이 없어 `UNIQUE(source, post_id, product)` 도
            # 못 걸러서(Postgres 는 NULL 을 서로 다른 값으로 본다) 그대로 3행이 된다.
            # ⛔ 내용이 **다르면 절대 접지 않는다.** 이름 없는 두 항목은 서로 다른 제품일 수
            #   있고, 합치면 다른 의견이 한 건으로 사라진다(원칙 2 — 과잉 병합 금지).
            # ⚠️ 지문에 `firsthand_evidence` 를 **포함한다**: 말더듬은 근거 조각까지 똑같이
            #   반복되지만, 서로 다른 문장에서 온 두 의견은 근거가 다르다. 그게 '말더듬'과
            #   '속성이 비어 있는 별개 의견'을 가르는 유일한 신호다.
            # ⚠️ DB 제약으로 풀지 말 것 — `(source, post_id) WHERE product IS NULL` 부분
            #   유니크 인덱스는 **서로 다른** 보류 제품 둘을 한 조각에서 충돌시킨다.
            # ⚠️ **내용이 없는 항목은 접지 않는다.** 속성 블록도 총평도 없는 보류 둘은
            #   말더듬의 증거가 아니라 그냥 구분할 재료가 없는 것이다 — 원래 이름이 서로
            #   달랐어도(`정체불명A`/`정체불명B`) 이 자리엔 이미 이름이 안 남아 있다.
            #   과소 집계는 과대 집계보다 알아채기 어렵다(`_fold_orders` 와 같은 판단).
            fp = _held_fingerprint(it)
            if _filled_score(it) and fp in held:
                continue
            held.add(fp)
            out.append(it)
            continue
        key = _norm_tag(name)
        if key not in seen:
            seen[key] = len(out)
            out.append(it)
        elif _filled_score(it) > _filled_score(out[seen[key]]):
            out[seen[key]] = it                  # 더 많이 찬 쪽을 남긴다
    return out


def repair_product_names(doc: dict, text: str, *, exclude=None,
                         known_products=None, known_fallback=None,
                         label_known=None) -> dict:
    """추출된 `mentioned_product` 를 **캡션 해시태그**로 검증·복구한다(순수·무LLM).

    후기 분기에는 판매자 분기와 달리 제품 게이트가 없어서, 추출기가 캡션의 **스펙 줄**을
    제품명으로 들어올린다. 실측: `아마존 우드 점토`(풀조합)에 후기 8행, `코코넛과자향`(향료)에
    2행이 달렸고, 정작 해시태그의 `빠코볼` 행은 **같은 글에서 0건**이었다.

    판정 4갈래:
      ① 제품명이 캡션 해시태그면 **그대로 둔다** — `specs` 에 없어도 상관없다.
      ② 해시태그가 아니면 산문에서 온 유령이다. 후보 태그가 **정확히 하나**면 교체.
      ③ 후보가 여럿이면 그중 **1층 제품인 게 정확히 하나**일 때만 교체(타이브레이커).
      ④ 그 외에는 `None`(보류). 지어내지도, 흡수하지도 않는다.
    마지막에 같은 제품으로 접힌 항목을 하나로 병합한다(`_fold_by_product`).

    ⛔ **되돌리지 말 것 — 초안이 틀렸던 자리.** 처음엔 ①이 없이 '캡션 태그 중 `known_products`
      에 있는 것 하나면 교체'만 있었다. 그러면 **`specs` 부재를 '제품이 아님'으로 취급**하게 되어
      태그가 둘인 글에서 진짜 제품이 흡수된다. 실측(시뮬레이션): `빠다코코볼`·`빠코폼`·
      `눈꽃크런키`·`곰바라기`·`키위스쿱`·`알감자찐감자`·`댕초밥` 이 전부 `빠코볼` 로 빨려
      들어갔다. **`specs` 부재는 미수집일 뿐이다** — 프로필 액터가 최신 ~12글만 주므로 1층은
      구조적으로 불완전하다. 판정 기준은 **해시태그 여부**이고 `known_products` 는 ③ 전용이다.

    ⚠️ 이 게이트는 **인스타 전용**이다. 해시태그가 없는 입력(디시)은 후보가 0개라 ②③이 모두
      불발하고 ①만 남는데, 그러면 모든 이름이 보류로 바뀐다 — 그래서 태그가 하나도 없으면
      **아무것도 건드리지 않고** 그대로 돌려준다(AC7).

    exclude: 그 마켓의 상호·핸들·별칭(`market_tag_exclusions`). 안 빼면 `#슬라임지나` 가 제품이 된다.
    known_products: 그 마켓의 1층 제품명 집합(`specs`). ③ 타이브레이커에만 쓴다.
    known_fallback: 같은 마켓의 **제품 후보 레지스트리**(`pipeline.load_product_registry`).
      ③이 1층에서 한 건도 못 찾았을 때만 본다(③′) — 아래 `resolve_product_name` 참조.
    """
    # 비제품 라벨은 **해시태그 게이트 앞**에서 비운다. 뒤에 두면 해시태그가 없는 소스(디시)에는
    # 아예 안 돌아서 `비매품 1번` 같은 이름이 그대로 제품 행이 된다(실측 11행).
    # ⚠️ 재료는 **`label_known` 전용 인자**다. `known_products`/`known_fallback` 로 때우지 말 것 —
    #   저 둘은 그 **마켓의** 제품 집합(③/③′ 타이브레이크용)이라, 그걸 라벨 판정에 쓰면
    #   마켓을 아직 모르는 조각에서 증거가 비어 판정이 갈린다. 여기서 묻는 건 '어느 마켓의
    #   제품인가'가 아니라 '이 표기가 누군가의 제품명으로 실재하는가'이고, 답은 마켓과 무관한
    #   전량이어야 한다(`pipeline.known_product_names`). 미주입이면 ③이 페일세이프로 떨어진다.
    drop_non_product_labels(doc, label_known)
    # 종류어·재료어도 같은 자리에서 비운다 — 이 경로가 인스타의 유일한 게이트 지점이고,
    # 아래 해시태그 게이트는 태그가 없으면 즉시 반환하므로 뒤에 두면 안 걸린다.
    drop_non_product_words(doc)

    if not product_hashtags(text, exclude=exclude):
        return doc                               # 해시태그 없는 소스(디시) → 무변경

    items = list(doc.get("reviews") or [])
    # 1패스: ①로 확정되는 이름을 먼저 모은다 — 2패스의 흡수 금지 목록(`taken`)이 된다.
    #        순서에 의존하면 같은 입력이 항목 순서만 달라도 결과가 갈린다.
    taken = [r.get("mentioned_product") for r in items
             if resolve_product_name(r.get("mentioned_product"), text, exclude=exclude,
                                     known_products=known_products,
                                     known_fallback=known_fallback)[1] == "keep"]
    repaired: list[dict] = []
    for r in items:
        pick, why = resolve_product_name(r.get("mentioned_product"), text, exclude=exclude,
                                         known_products=known_products,
                                         known_fallback=known_fallback, taken=taken)
        repaired.append(r if why == "keep" else {**r, "mentioned_product": pick})
    doc["reviews"] = _fold_by_product(repaired)
    return doc


def resolve_product_name(name: str | None, text: str, *, exclude=None,
                         known_products=None, known_fallback=None,
                         taken=None) -> tuple[str | None, str]:
    """제품명 한 개에 대한 판정 — `(목표 제품명, 사유)`. 위 4갈래의 **단일 출처**다.

    백필(`pipeline.repair_product_attribution`)과 수집 경로가 이 한 벌을 공유한다. 규칙이 두
    곳에 있으면 조용히 갈라진다 — 판매자 게이트가 `_specs_from_seller_post` 로 합쳐진 것과 같은 이유.

    taken: **같은 조각에서 ①로 이미 확정된 제품명들**. 이 이름들로는 흡수하지 않는다.
      ⛔ 이 가드가 없으면 1층에 아직 없는 **진짜 제품이 흡수된다.** 실측(`DLOb2euzM60`):
        캡션 태그는 `#빠코볼` 뿐인데 본문이 `저는 예전부터 빠코폼 파였는데, … 빠코볼도 존잼`
        이라 두 제품을 대조한다. `빠코폼` 은 태그가 아니라 ①에 안 걸리고, 1층에도 없어서
        ③이 `빠코볼` 로 바꿔 버린다 — 그러면 비교 후기 한 축이 통째로 사라진다.
        진짜 유령 글에는 애초에 진짜 제품 행이 **없다**(실측 0/8) — 있다는 것 자체가
        '이건 다른 제품'이라는 신호다.
      (계획서 시뮬레이션도 이걸 놓쳤다 — 보존 확인 목록에 `빠코폼` 이 빠져 있었다.)

    known_fallback: **2단 타이브레이크**(③′). 1층(`specs`)에서 일치가 **0건일 때만** 본다.
      왜 2단인가: `specs` 는 캡션이 두꺼운 제품만 담는다(`_PRODUCTHOOD_FIELDS` 네 칸이 전부
      null 이면 제품성 미달로 드롭). 반면 레지스트리는 판매자 피드 전량의 **해시태그**라
      실측 408행 대 약 2,200후보다 — ③이 못 가리는 상황의 대부분은 '둘 다 1층에 없음'이지
      '둘 다 1층에 있음'이 아니다.
      ⛔ **합집합으로 만들지 말 것.** 한 집합으로 합치면 1층에서 정확히 하나이던 판정이
        레지스트리 쪽 후보가 끼어들어 `hold_ambiguous` 로 **퇴화**할 수 있다. 2단은
        1층 판정을 절대 못 뒤집는다 — 없던 판정만 더한다(단조).
      ⚠️ 레지스트리는 사람이 승격한 목록이 아니라 **유도된 후보**라 잡음이 있다(실측: 늪지의
        `액괴`·`워터글루`·`jigglyslime` — 광역어·재료어인데 마켓/종류 후보 임계값 아래라
        products 에 남았다). 그래서 1층보다 **뒤**에 두고, 두 개 이상 걸리면 보류한다.

    사유 문자열: `keep`(①) · `sole_tag`(②) · `l1_tiebreak`(③) · `registry_tiebreak`(③′) ·
      `hold_*`(④) · `no_tags`.
    """
    tags = product_hashtags(text, exclude=exclude)
    if not tags:
        return (name or None), "no_tags"         # 해시태그 없는 소스 → 무변경(AC7)
    clean = (name or "").strip()
    if clean and _norm_tag(clean) in {_norm_tag(t) for t in tags}:
        return clean, "keep"                     # ① specs 에 없어도 유지
    claimed = {_norm_tag(t) for t in (taken or ())}
    free = [t for t in tags if _norm_tag(t) not in claimed]
    if not free:
        # ⚠️ 먼저: 그 이름이 **애초에 제외 대상**(마켓 상호·핸들·별칭·광역어)이면 '다른 제품'이
        #   아니라 그냥 노이즈다. 아래 보존 규칙은 1층에 아직 없는 **진짜 제품**을 지키려고
        #   있는 건데, 이 검사가 없으면 마켓 태그까지 같이 지켜 준다.
        #   실측(2026-08-09): 캡션 `#슬라임지나 #빠코볼` 에서 같은 글의 다른 행이 `빠코볼` 을
        #   이미 claim 하자 `슬라임지나` 가 `keep_distinct` 로 살아남아 **마켓 이름이 제품 행**이
        #   됐다(`꼼픽` 도 같은 경로). ①이 이미 걸렀다고 믿을 수 없는 이유는, ①은 '캡션
        #   해시태그인가'만 보는데 제외된 태그는 애초에 그 목록에 없기 때문이다.
        ex = {_norm_tag(e) for e in (exclude or ())} | {_norm_tag(g) for g in GENERIC_TAGS}
        if clean and _norm_tag(clean) in ex:
            return None, "hold_excluded_name"
        # 같은 글이 후보 제품을 **이미 갖고 있다** → 이 이름은 그 제품이 아니다.
        # ⚠️ 그렇다고 '제품이 아니다'는 아니다 — 여기서 None 으로 비우면 **진짜 제품이 사라진다.**
        #   실측: `DLOb2euzM60` 의 `빠코폼` 은 캡션 본문이 `예전부터 빠코폼 파였는데 … 빠코볼도
        #   존잼` 이라 명백한 별개 제품인데, None 처리했더니 맞는 이름을 지우는 결과가 됐다.
        #   이 분기는 **판단 근거가 없는** 자리이므로 추출값을 건드리지 않는 게 최소 개입이다.
        #   진짜 유령(`슬린이시절` 등)이 남는 건 이 게이트가 아니라 마켓별 제품 목록의 몫이다.
        return (clean or None), "keep_distinct"
    if len(free) == 1:
        return free[0], "sole_tag"               # ②
    known = {_norm_tag(p) for p in (known_products or ())}
    in_l1 = [t for t in free if _norm_tag(t) in known]
    if len(in_l1) == 1:
        return in_l1[0], "l1_tiebreak"           # ③
    if not in_l1:
        # ③′ 1층이 **한 건도** 못 짚었을 때만 레지스트리를 본다. `in_l1` 이 둘 이상이면
        #    상위 집합인 레지스트리도 둘 이상이라 어차피 불발이고, 여기서 걸러 두면
        #    '1층 판정을 못 뒤집는다'가 코드 모양으로 남는다.
        in_reg = [t for t in free if _norm_tag(t) in {_norm_tag(p) for p in (known_fallback or ())}]
        if len(in_reg) == 1:
            return in_reg[0], "registry_tiebreak"
    # ④ 보류 — 지어내지도 흡수하지도 않는다(AC5)
    return None, ("hold_no_l1_match" if not in_l1 else "hold_ambiguous")


def extract_review(text: str, llm: LLM, model: str | None = None) -> dict:
    """
    후기 텍스트 한 건 → 2층 JSON(dict):
      {"market": ..., "shipping_cs": ..., "reviews": [제품별 평가...], "flags": {...}}.
    market·shipping_cs 는 후기(주문) 단위, reviews 는 제품 단위. 비교글이면 reviews 가 제품 수만큼.
    스키마 강제 + 파싱 실패 1회 재시도.
    """
    return drop_hearsay_reviews(llm.complete(
        text,
        system=LAYER2_SYSTEM,
        schema=LAYER2_SCHEMA,
        model=model,
        label="extract.layer2",
    ), text)


def _empty_doc() -> dict:
    """빈 문서 — **팩토리로 만든다.** 모듈 상수로 두면 얕은 복사본들이 같은 `reviews` 리스트를
    공유해, 한 조각의 결과가 다른 조각으로 새는 종류의 버그가 조용히 생긴다."""
    return {"market": None, "shipping_cs": None, "reviews": [], "flags": {"toxic": False}}

# 한 호출에 넣을 조각(글+댓글) 최대 개수. 크게 잡을수록 호출은 줄지만 항목별 귀속 정확도가
# 떨어진다(§5 위험표). 실측 근거(스레드 경로, 2026-08-04, evals/results/cost_profile_thread.json):
# LAYER2_THREAD_SYSTEM 고정 프롬프트 4,511 토큰(n=1 최소 본문, gpt-5.4-mini) 기준, n=12 에서
# 고정 비중 90.6%·cached_tokens=4,864(고정 프롬프트 거의 전부가 캐시됨)로, 웜캐시 비용/조각
# $0.000162 vs 본문만의 이론적 하한 $0.000154 — 비율 1.054 ≤ 1.10 캐시 하드스탑 기준 →
# 16/20/24 확장 트라이얼 없이 12 유지. Settings.max_thread_sources(config.py, env
# MAX_THREAD_SOURCES)가 단일 출처이며, 이 값은 그 기본값을 그대로 반영한다.
# AC12 동등성 테스트가 이 값에서 통과하는 것을 확인한 뒤에만 올릴 것.
MAX_THREAD_SOURCES = settings.max_thread_sources


# ---------------------------------------------------------------- 제품 어휘(초성·약칭)
# 왜 프롬프트에 넣나: **linking 은 모델이 이미 뽑은 것만 정규화한다.** 댓글이 `ㅇㅇㅈ 아바 좋더라`
# 라고 하면 모델이 먼저 '아바'를 제품으로 **인식**해야 `mentioned_product` 에 뭔가가 들어가고,
# 그래야 linking 이 정규화할 대상이 생긴다. 인식 자체가 안 되면 사후 정규화로는 못 되살린다
# (사용자 지적 2026-08-09). 디시 신규 877조각 중 **44%(390건)** 가 초성 토큰을 포함한다.
#
# ⚠️ 이건 1급 규칙('미언급 → null, 지어내기 금지')과 정면으로 닿는 자리다. 후보 목록을 통째로
#   보여 주면 모델이 애매한 문장을 그럴듯한 제품명에 **스냅**시킬 수 있다. 그래서 두 겹으로 막는다:
#   ① 목록은 **그 스레드 본문에 실제로 등장한 표면형만** 남긴다(아래 `vocab_candidates`) —
#      즉 목록에 오르는 이름은 전부 텍스트에 근거가 있다. 없는 이름은 애초에 안 보인다.
#   ② 그래도 규칙은 프롬프트, **강제는 코드**다(이 저장소의 일관된 규칙 — `drop_hearsay_reviews`
#      와 `_fold_orders` 가 같은 자리다). `enforce_product_vocab` 이 사후에 다시 검사한다.
PRODUCT_VOCAB_MAX = 24  # 프롬프트에 싣는 후보 상한(침묵 절단 금지 — 넘치면 로그로 드러낸다)


def build_product_vocab(names, aliases: dict | None = None) -> dict[str, list[str]]:
    """`{정규 제품명: [표면형…]}` — 표면형 = 이름 자체 + **사람이 시드한 약칭**.

    ⛔ **제품 초성을 생성하지 마라.** `linking._kb_surface_forms` 가 같은 이유로 이미 그렇게
      한다("제품 태그와 충돌할 만큼 짧고"). 그 규칙을 모르고 한 번 넣어 봤다가 실측으로 확인했다
      (2026-08-09, 디시 171스레드): 생성 초성 매칭 14건 중 **9건이 오탐**이었다 —
      `ㅋㅋㅋㅋㅋㅇ`(쿠키컵코코아)는 웃음 `ㅋㅋㅋㅋㅋㅋㅋㅋㅇㅋ` 안에서, `ㅂㅋㅋㅋ`(바콕쿠키)는
      욕설+웃음 `ㅅㅂㅋㅋㅋ` 안에서, `ㅅㄹㅇ`(슬랑이)는 **다른 마켓·제품의 초성**
      `ㅅㄹㅇㅂㄴ`·`ㅅㄹㅇㅈㄴ` 안에서 걸렸다(6건). 제품명은 6~9음절이라 초성이 길어질 것 같지만,
      실제로 이 갤이 쓰는 약칭은 초성이 아니라 **음절 클리핑**(`허니푸냥이`→`푸냥이`)이다.
      깨끗하게 맞은 4건은 전부 생성분이 아니라 `data/product_aliases.json` 의 사람 시드였다.
      초성이 유효한 건 **마켓명**(`베이퍼`→`ㅂㅇㅍ`)뿐이고 그건 개체연결이 이미 한다.

    즉 약칭 재료는 **사람만 만들 수 있다.** 그래도 시드는 안전하고 점진적이다: 틀린 약칭은
    어떤 스레드 본문에도 안 걸려 그냥 무시되고, 맞는 약칭은 즉시 인식된다(재추출 불필요).
    """
    vocab: dict[str, list[str]] = {}
    for n in names:
        n = (n or "").strip()
        if n:
            vocab.setdefault(n, [n])
    for _market, table in (aliases or {}).items():
        for short, canon in (table or {}).items():
            if canon in vocab and short:
                vocab[canon] = sorted(set(vocab[canon]) | {short})
    return vocab


def vocab_candidates(vocab: dict[str, list[str]], text: str) -> dict[str, list[str]]:
    """`vocab` 중 **이 텍스트에 표면형이 실제로 등장한** 항목만. 프롬프트 주입 재료.

    이 필터가 반-지어내기 장치의 절반이다(나머지 절반은 `enforce_product_vocab`).
    부수 효과로 토큰도 크게 준다 — 마켓당 160여 개를 매 호출 싣는 대신 보통 한 줌만 남는다.
    """
    if not vocab or not text:
        return {}
    hits = {canon: forms for canon, forms in vocab.items()
            if any(f and f in text for f in forms)}
    return hits


def _vocab_line(cands: dict[str, list[str]]) -> str:
    """후보를 `이름(표면형·표면형)` 꼴 한 줄로. 상한 초과분은 **세어서 드러낸다**."""
    items = sorted(cands.items())
    shown, extra = items[:PRODUCT_VOCAB_MAX], len(items) - PRODUCT_VOCAB_MAX
    parts = []
    for canon, forms in shown:
        alt = [f for f in forms if f != canon]
        parts.append(f"{canon}({'·'.join(alt)})" if alt else canon)
    line = ", ".join(parts)
    if extra > 0:
        line += f" 외 {extra}개"
    return line


def enforce_product_vocab(doc: dict, text: str, cands: dict[str, list[str]]) -> int:
    """`mentioned_product` 에 **근거가 있는지** 코드로 검사하고, 없으면 `None` 으로 보류한다.

    `cands` 는 이미 `vocab_candidates` 로 **그 스레드 본문에 등장한 것만** 남긴 목록이다 —
    따라서 `name in cands` 자체가 '스레드에 근거 있음'이다. 반환값은 보류시킨 건수(관측용).

    통과 조건(하나라도 만족):
      ① 이름이 **이 조각** 본문에 그대로 있다 — 가장 흔한 정상 경로.
      ② 이름이 **스레드 후보**에 있다 — 제품명을 생략하고 앞 조각을 받아 말한 댓글(AC13).
         ⛔ 근거 판정을 조각 단위로 좁히면 **바로 이 기능이 죽는다**: 문맥으로 귀속된 댓글은
           자기 텍스트에 이름이 없어서 전부 null 이 된다. 배치 추출의 존재 이유 절반을
           사후 검사가 되돌리는 꼴이라, 근거 스코프는 스레드여야 한다.
      ③ 어휘 자체가 비었다(미주입 경로) — 검사하지 않는다(하위호환).
    어느 것도 아니면 = 스레드 어디에도 그 표기가 없다 = **지어냈다** → 보류.

    ⛔ 규칙을 프롬프트에만 맡기지 말 것: 같은 입력 4회에 4번 다른 답이 나온 전례가 있다
      (`drop_hearsay_reviews` 가 생긴 이유). 여기도 같은 실패 모드다.
    ⚠️ 과잉 보류도 회귀 대상이다 — 1층에 없는 **진짜 제품**을 지우는 방향이고, 그 손실은
      화면에 안 보인다(유령 제품과 반대 방향의, 더 알아채기 어려운 실패). 그래서 ①이 먼저다:
      본문에 그대로 있으면 어휘에 없어도 남긴다.
    """
    if not cands:
        return 0
    held = 0
    for rv in (doc.get("reviews") or []):
        name = (rv.get("mentioned_product") or "").strip()
        if not name or name in text or name in cands:
            continue
        rv["mentioned_product"] = None
        held += 1
    return held


def build_thread_prompt(title: str | None, texts: list[str],
                        products: dict[str, list[str]] | None = None) -> str:
    """조각들에 [S<n>] 번호를 붙인 스레드 프롬프트. 번호가 귀속의 유일한 근거다.

    `products` 가 있으면 **본문에 실제로 등장한** 제품 후보를 머리말에 싣는다(초성·약칭 인식용).
    """
    head = f"[제목] {title}\n" if title else ""
    vocab = f"[제품 후보] {_vocab_line(products)}\n" if products else ""
    body = "\n".join(f"[S{i}] {t}" for i, t in enumerate(texts))
    return head + vocab + body


# 스레드 배치의 출력 상한. 기본 4,096 으로는 **모자란다** — 실측(2026-08-09 유료 런):
# 34앵커 중 2앵커가 `JSONDecodeError: Unterminated string`(char 10,803 / 12,383)으로 통째로 죽었다.
# ⚠️ GPT-5 계열에서 `max_completion_tokens` 는 **추론 토큰까지 포함**한다. 즉 4,096 중 상당 부분이
#   본문이 나오기도 전에 소진된다. 파싱 재시도 1회는 같은 지점에서 똑같이 잘리므로 무의미하다
#   (결정성 재시도는 '다른 답'을 위한 게 아니다).
# 왜 지금 터졌나: ADR-0017 이전엔 게이트가 스레드당 9조각까지만 통과시켜 출력이 상한 안에 들었다.
# M-only 로 배치가 조밀해지면서 처음 넘쳤다 — 긴 스레드 청크 문제와 같은 뿌리다.
THREAD_MAX_TOKENS = 16384


def extract_thread(title: str | None, texts: list[str], llm: LLM,
                   model: str | None = None,
                   products: dict[str, list[str]] | None = None,
                   label_known=None) -> list[dict]:
    """
    스레드 조각들 → 조각별 문서 리스트(입력 순서 정렬, 길이 보장).
    응답에 빠진 조각은 빈 문서로 메운다 — 조용히 짧은 리스트를 돌려주면 호출부에서 귀속이 밀린다.

    `products`: 이 스레드 본문에 표면형이 등장한 제품 어휘. 프롬프트 머리말로 들어가고,
      반환 직전 `enforce_product_vocab` 이 **같은 어휘로 코드 검사**한다(규칙은 프롬프트,
      강제는 코드).
    """
    if not texts:
        return []
    out = llm.complete(
        build_thread_prompt(title, texts, products),
        system=LAYER2_THREAD_SYSTEM,
        schema=LAYER2_THREAD_SCHEMA,
        model=model,
        max_tokens=THREAD_MAX_TOKENS,
        label="extract.layer2.thread",
    )
    by_id: dict[str, dict] = {}
    for doc in (out.get("docs") or []):
        by_id.setdefault(str(doc.get("source_id", "")).strip(), doc)
    docs = []
    held = 0
    labeled = 0
    for i in range(len(texts)):
        doc = dict(by_id.get(f"S{i}") or by_id.get(str(i)) or _empty_doc())
        doc.pop("source_id", None)               # 귀속은 리스트 위치로 끝났다
        doc = drop_hearsay_reviews(doc, texts[i])
        # 비제품 라벨(`비매품 1번`·`이번차수`)을 먼저 비운다. 어휘 검사보다 **앞**인 이유는
        # 그런 이름이 대개 본문에 그대로 있어서(①) 어휘 검사를 그냥 통과하기 때문이다 —
        # 근거는 있는데 제품이 아닌 경우라, 두 검사가 서로를 대신하지 못한다.
        # ⚠️ 재료는 `label_known`(마켓 무관 전량)이지 `products` 가 **아니다**. `products` 는
        #   이 **스레드 본문에 등장한** 후보만 남은 집합이라, `푸딩 비매품` 처럼 표기가 살짝
        #   달라 후보에 못 든 진짜 제품이 '증거 없음'이 아니라 '증거 있는데 불일치'로 읽혀
        #   지워진다(집합이 비지 않아 페일세이프가 안 걸린다). 백필은 전량을 보므로 같은
        #   이름에 경로마다 다른 판정이 붙는다 — 규칙이 갈리는 전형적인 모양이다.
        labeled += drop_non_product_labels(doc, label_known)
        # ⛔ 여기서 `drop_non_product_words` 를 부르지 말 것 — **되돌린 자리다**(2026-08-10).
        #   이 층은 KB 를 모르므로 마켓 접두를 뗄 수 없는데, 종류어·자모 게이트를 먼저 걸면
        #   `ㅇㅊ` 같은 맨몸 마켓 표기가 '자모뿐인 이름'으로 먼저 비워져 **마켓 신호가 사라진다**
        #   — 그 행은 스레드 도장 마켓을 0.95(직접 매칭)로 물려받아, 되돌릴 표식도 없는
        #   오귀속이 된다. 반대 방향으로도 샌다: `ㅂㅇㅍ 빨대` 의 나머지 `빨대`(종류어)가
        #   게이트를 이미 지나쳐 제품으로 남는다. 실측으로 둘 다 재현됐다.
        #   분리와 단어 게이트는 KB 를 아는 `linking.link` 에서 **그 순서로 함께** 돈다.
        held += enforce_product_vocab(doc, texts[i], products or {})
        docs.append(doc)
    if held:
        # 침묵 금지 — 어휘 주입이 지어내기를 얼마나 막았는지가 그 기능의 유일한 실측치다.
        log.info("제품 어휘 검사: 근거 없는 제품명 %d건 보류(후보 %d개)", held, len(products or {}))
    if labeled:
        # 어휘 검사 보류와 **따로** 센다 — 둘은 아예 다른 실패를 막는다(지어내기 vs 판매 형식어).
        # 합치면 어느 게이트가 일했는지 사후에 못 가른다(`llm_calls_saved` 를 가른 것과 같은 이유).
        log.info("비제품 라벨 게이트: 제품명 %d건 비움", labeled)
    return docs


def thread_key(raw) -> str:
    """이 조각이 속한 스레드의 글번호. 못 읽으면 `''`(고아 그룹).

    **글과 댓글의 규칙이 다르다** — 댓글은 `meta["parent_no"]` 를 갖지만 **글의 meta 엔
    스레드 번호가 없다**(`_parse_post` 가 싣는 건 nick·ip·조회·댓글수·추천뿐). 그래서 글은
    URL 의 `no=` 에서 파싱한다. `source_links.build_source_ref` 도 같은 이유로 같은 규칙을 쓴다.

    ⚠️ 이 함수가 그 규칙의 **단일 출처**다. 호출부가 `meta["thread_no"]` 하나로 통일하고 싶다는
      유혹에 빠지지 말 것 — 그 키는 댓글에만 있어서, 글에 대해선 조용히 `None` 이 되고
      `None in {..., None}` 같은 와일드카드 매칭까지 만들어 낸다(2026-08-07 실제 결함).
    """
    if raw.meta.get("type") == "comment":
        return raw.meta.get("parent_no") or ""
    m = _NO_RE.search(raw.url or "")
    return m.group(1) if m else ""


def group_threads(raws: list) -> dict[str, dict]:
    """수집 조각들을 스레드 단위로 묶는다(`thread_key` 기준). 매칭 실패분은 `''` 고아 그룹.

    `extract_collected`(실제 추출)와 `count_thread_batches`(비용 추정), 그리고 파이프라인의
    문맥 판정이 **이 한 벌을 공유한다** — 그룹핑이 두 곳에 있으면 절감량 보고가 실제 호출 수와
    조용히 어긋난다.
    """
    threads: dict[str, dict] = {}
    for p in (r for r in raws if r.meta.get("type") == "post"):
        threads.setdefault(thread_key(p), {"post": None, "comments": []})["post"] = p
    for c in (r for r in raws if r.meta.get("type") == "comment"):
        threads.setdefault(thread_key(c), {"post": None, "comments": []})["comments"].append(c)
    return threads


def count_thread_batches(raws: list, batch_size: int = MAX_THREAD_SOURCES) -> int:
    """이 조각 목록을 추출하면 LLM 을 **몇 번** 부르는지. 순수 함수(무LLM·무네트워크).

    디시 경로는 조각당 1콜이 아니라 **스레드 배치당 1콜**이라, 기보유 컷의 절감량을
    '거른 조각 수'로 보고하면 실제보다 부풀려진다. 컷 전후로 이 값을 재서 차이를 보고한다.
    """
    return sum(
        -(-len(([t["post"]] if t["post"] else []) + t["comments"]) // batch_size)   # ceil
        for t in group_threads(raws).values())


def extract_collected(raws: list, llm: LLM, model: str | None = None,
                      batch_size: int = MAX_THREAD_SOURCES,
                      product_vocab: dict[str, list[str]] | None = None,
                      label_known=None, counts: dict | None = None) -> list[tuple]:
    """
    수집된 RawReview(글 + 댓글)를 **스레드 단위 배치**로 추출한다(계획 C-1).
    이 갤은 후기가 댓글에 많아(sources.py 주석) 댓글도 1급 후기로 취급한다.

    왜 배치인가: **배치 도입 전의 per-comment 경로**는 호출당 입력 토큰의 99.4%가 고정
    프롬프트였다(실측, `evals/cost_profile.py` 기본 모드 — 배치가 대체한 경로의 역사적 근거).
    댓글 1건당 1회 호출은 그 2,900자짜리 지시문을 댓글 수만큼 재전송한다는 뜻이다.
    현행 스레드 경로의 배치 크기 근거는 위 `MAX_THREAD_SOURCES` 주석(캐시 하드스탑 실측)이다.

    부수 이득(AC13): 같은 호출 안에 형제 댓글이 있으므로 **제품명을 생략한 댓글의 귀속**이 가능해진다.
    per-comment 경로에서는 원리적으로 불가능했던 케이스다.

    market 상속은 유지하되 **채우기 전용**이고 **출처는 글 본문 하나**다 — 댓글 단독 추출은
    'ㅂ슬라임' 등으로 흔들려 개체연결을 막으므로 마켓을 말하지 않은 조각엔 글의 값을
    물려주지만, ①자기 마켓을 뽑은 조각은 그대로 두고(덮어쓰면 비교 스레드에서 남의 마켓
    후기가 된다) ②**댓글의 마켓은 스레드로 승격되지 않는다**(아래 실측 주석 둘).

    counts: 주면 `counts["market_inherited"]` 에 상속이 걸린 조각 수를 쓴다(관측용, 선택).
      반환값을 안 바꾸는 out-파라미터인 건 호출부 무변경을 지키기 위해서다 — 이 함수의
      반환은 `index_post` 입력으로 바로 흘러가는 자리라 튜플을 늘리면 소비처가 전부 깨진다.

    반환: [(raw, doc), ...] — index.index_post 입력으로 그대로 사용(호출부 무변경).
    """
    threads = group_threads(raws)

    out: list[tuple] = []
    failed = 0                       # 실패한 배치 수 — 무음 유실 금지(아래에서 로그로 드러낸다)
    inherited = 0                    # 글 마켓을 물려받은 조각 수 → counts["market_inherited"]
    for _, thread in threads.items():
        post, cmts = thread["post"], thread["comments"]
        title = (post.raw_title if post else None) or \
                (cmts[0].meta.get("parent_title") if cmts else None)
        members = ([post] if post else []) + cmts
        # 제품 후보는 **스레드 전체 본문**으로 한 번 좁힌다 — 조각마다 좁히면 제품명을 생략한
        # 댓글(바로 이 기능의 대상)이 자기 텍스트엔 근거가 없어 후보를 못 받는다. 형제 문맥이
        # 배치의 존재 이유인 것과 같은 논리다. 사후 코드 검사는 조각 단위로 따로 돈다.
        thread_text = "\n".join((r.text or "") for r in members)
        cands = vocab_candidates(product_vocab or {}, thread_text)

        docs: list[dict] = []
        for start in range(0, len(members), batch_size):
            chunk_members = members[start:start + batch_size]
            texts = [r.text for r in chunk_members]
            # 13조각 넘는 스레드는 청크가 갈리는데, **글 본문은 members[0] 하나뿐**이라
            # 둘째 청크부터는 댓글만 남는다. 그러면 ① 프롬프트가 'S0=글 본문'이라고 말하는데
            # 실제 S0 가 댓글이고 ② 제품명을 생략한 댓글의 귀속(AC13)이 원리적으로 불가능해진다
            # — 배치 추출의 존재 이유 절반이 그 문맥이다. 그래서 이어지는 청크마다 글 본문을
            # **문맥으로만** 앞에 넣고 그 결과 문서는 버린다(색인은 청크 원소에만 대응).
            # ⚠️ ADR-0017(M 만 드롭) 전에는 게이트가 스레드당 최대 9조각까지만 통과시켜 이
            #   분기가 **한 번도 안 돌았다**. 실측: 같은 저장소에서 M-only 는 10스레드가 12를
            #   넘고 54조각이 글 본문 없이 판정될 뻔했다(최대 스레드 25조각).
            ctx_post = post if (start and post is not None) else None
            if ctx_post is not None:
                texts = [ctx_post.text] + texts
            try:
                chunk_docs = extract_thread(title, texts, llm, model, products=cands,
                                            label_known=label_known)
            except Exception as e:
                # 배치 하나가 죽어도 **그 배치만** 잃는다. 예전엔 예외가 그대로 올라가
                # 그 앵커의 남은 스레드가 통째로 날아갔다(실측: 2앵커 74조각).
                # 빈 문서로 자리를 채워 **정렬을 지킨다** — 짧은 리스트를 돌려주면 zip 이
                # 조용히 잘라 뒤쪽 조각의 귀속이 통째로 밀린다(패딩과 같은 이유).
                # 값은 이미 나갔지만 행은 안 남으므로 **다음 런이 공짜로 재시도**한다
                # (원문이 디스크에 있다 — 그게 raw-first 의 값어치다).
                failed += 1
                log.error("스레드 배치 추출 실패 — 이 배치만 건너뜀(%d조각): %s", len(texts), e)
                chunk_docs = [_empty_doc() for _ in texts]
            docs.extend(chunk_docs[1:] if ctx_post is not None else chunk_docs)

        # 스레드 market 의 권위는 **글 본문뿐**이다. 댓글 폴백은 없다.
        # ⛔ **되돌리지 말 것 — `next(d.get("market") for d in docs …)` 를 다시 넣지 마라.**
        #   댓글의 마켓은 **그 댓글 작성자의 것**이지 스레드의 것이 아니다. 예전 폴백은 글이
        #   마켓을 안 밝히면 아무 댓글의 마켓이나 집어 스레드 전체에 물려줬다. 실측
        #   (2026-08-10, 아모스갤 813행): `빈짱` 21행 중 **19행이 한 스레드**에서 나왔고,
        #   그 출처는 댓글 하나의 `ㅂㅉ` 였다. 마켓이 붙은 스레드 95/95(100%)가 단일 마켓인데,
        #   원문 대조 결과 그 갤의 지배적 형태는 **한 글에 여러 마켓이 나열되는 추천/첨삭 글**이다
        #   — 100% 는 물리적으로 나올 수 없는 값이라 그 자체가 폴백의 증거였다.
        #   틀린 마켓은 NULL 보다 나쁘다(남의 마켓 후기로 집계된다 = 1급 기능인 출처 편향 왜곡).
        #   항목별 마켓이 필요하면 폴백이 아니라 `reviews[].mentioned_market` 이 자리다.
        market = docs[0].get("market") if post and docs else None
        for raw, doc in zip(members, docs):
            # **채우기만 한다 — 덮어쓰지 않는다.** 조각이 자기 마켓을 뽑았으면 그게 그 조각의
            # 사실이고, 스레드 값은 마켓을 말하지 않은 형제를 위한 폴백일 뿐이다.
            # 실측(2026-08-10, 아모스갤 813행): 덮어쓰기 때문에 **36행이 자기 본문과 모순**됐다.
            # 스레드 200743 은 본문에 마켓이 7개 등장하는 비교 스레드인데, 글에서 잡힌 `빈짱`
            # 하나가 19행 전부에 찍혔다 — 다른 마켓 후기가 빈짱 후기로 집계된다는 뜻이고,
            # 그건 1급 기능인 출처 편향의 왜곡이다(틀린 마켓은 NULL 보다 나쁘다).
            if market and not doc.get("market"):
                doc["market"] = market
                inherited += 1
            out.append((raw, doc))
    if inherited and counts is not None:
        # 무음 금지 — 상속이 **몇 건 걸렸는지**가 없으면 마켓 커버리지가 움직였을 때
        # '원문이 말한 마켓'과 '글에서 물려받은 마켓'을 사후에 못 가른다. 절감 카운터를
        # `llm_calls_saved` / `llm_calls_saved_by_dedup` 으로 가른 것과 같은 이유다.
        log.info("스레드 글 마켓 상속: %d조각", inherited)
    if counts is not None:
        counts["market_inherited"] = inherited
    if failed:
        log.error("스레드 배치 %d개 추출 실패 — 그 조각들은 행이 안 남아 다음 런이 재시도한다", failed)
    return out


if __name__ == "__main__":
    import logging
    from .sources import DCInsideSource, collect_all, expand_queries
    from .llm_ops import summary

    logging.basicConfig(level=logging.WARNING)

    dc = DCInsideSource(gallery_id="amos", comment_pages=1)
    queries = expand_queries("허니푸냥이", aliases=["푸냥이"], market_word="봄")
    print("검색어 확장:", queries)
    raws = collect_all([dc], keywords=queries, per_source_limit=40)
    if not raws:
        print("수집 결과 없음 — 키워드를 바꿔 시도")
        raise SystemExit

    llm = LLM()
    pairs = extract_collected(raws, llm)          # 글 + 댓글 모두 동일 추출
    n_post = sum(1 for r, _ in pairs if r.meta.get("type") == "post")
    n_cmt = len(pairs) - n_post
    print(f"추출 대상: 글 {n_post} + 댓글 {n_cmt}\n" + "=" * 60)
    for raw, doc in pairs:
        kind = raw.meta.get("type")
        prods = [r.get("mentioned_product") for r in doc.get("reviews", [])]
        print(f"[{kind:7}] market={doc.get('market')!r:>6} 제품={prods}  {raw.url}")
    print("=" * 60, "\n관측성:", summary())
