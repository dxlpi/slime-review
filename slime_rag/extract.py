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

import re

from .config import settings
from .llm_ops import LLM

_NO_RE = re.compile(r"[?&]no=(\d+)")

# ---------------------------------------------------------------- 통제어휘
FEEL_VOCAB = ["말랑", "말캉", "쫀득", "퐁신", "폭닥", "크리미", "로션크리미",
              "얄랑", "매트", "빳빳", "텐션감있는", "흐물거리는", "쳐지는", "흐름성있는"]
TYPE_ENUM = ["폼볼", "촉감류(점토)", "디폼", "난사", "눈꽃", "지글리", "크런치",
             "빈백", "클라우드", "샤베트", "클리어", "버글리", "젤라또", "빨대", "라이스볼"]
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
      → S2 의 mentioned_product 는 앞 문맥이 가리키는 제품. S1 에는 평가 항목을 만들지 마라."""


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
    "slime_type": _nenum(TYPE_ENUM),   # 종류(통제어휘). 목록 밖/미언급이면 null.
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
- slime_type: 종류 통제어휘 안에서만 — {", ".join(TYPE_ENUM)}. 목록 밖이면 null.
  📏 '6mm'·'8mm'·'7-9미리' 같은 mm 규격은 **알갱이 지름**이지 종류가 아니다 — 디폼도 폼볼도
  mm 로 표기한다. 예) '7-9미리 폼볼 내장' → 폼볼, '6미리 디폼 내장' → 디폼.
  **캡션이 쓴 종류어를 그대로 따르고**, mm 만 있고 종류어가 없을 때만 '디폼'으로 기본값을 잡는다.
  (제품명·테마어에 낚이지 말 것: '곰돌디핑'·'꼬끄카롱'은 음식 테마지 종류가 아니다.)
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
    """
    return llm.complete(
        text,
        system=LAYER1_SYSTEM,
        schema=LAYER1_SCHEMA,
        model=model,
        label="extract.layer1",
    )


def _norm(text: str) -> str:
    return "".join((text or "").split())


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
    source_text 를 안 넘기면 2)는 건너뛴다(원문을 모르는 호출부 하위호환).
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
        kept.append(r)
    doc["reviews"] = kept
    return doc


_ATTR_SLOTS = ("scent", "texture", "sound", "longevity")


def _filled_score(item: dict) -> int:
    """항목이 실제로 담고 있는 평가의 양 — 접을 때 어느 쪽을 남길지 정하는 데만 쓴다."""
    n = sum(1 for k in _ATTR_SLOTS if item.get(k))
    ov = item.get("overall") or {}
    return n * 2 + sum(1 for k in ("summary", "stated_rating") if ov.get(k))


def _fold_by_product(items: list[dict]) -> list[dict]:
    """같은 제품으로 접힌 항목을 하나로 병합 — **이중 계상 방지**.

    복구만 하고 안 접으면 유령 2행이 진짜 제품 2행이 될 뿐이다(AC3). 실측: `DLNVdrIzQdm` 은
    한 캡션에서 `아마존 우드 점토`(풀조합)와 `코코넛과자`(향)를 각각 제품으로 내보냈는데,
    둘 다 `빠코볼` 로 복구되면 한 사람의 한 의견이 빠코볼 후기 **2건**이 된다.
    ⚠️ 보류(None)는 접지 않는다 — 서로 다른 제품일 수 있고, 합치면 다른 의견이 한 건이 된다.
    """
    out: list[dict] = []
    seen: dict[str, int] = {}                    # 정규화 제품명 → out 인덱스
    for it in items:
        name = it.get("mentioned_product")
        if not name:
            out.append(it)                       # 보류분은 그대로 둔다
            continue
        key = _norm_tag(name)
        if key not in seen:
            seen[key] = len(out)
            out.append(it)
        elif _filled_score(it) > _filled_score(out[seen[key]]):
            out[seen[key]] = it                  # 더 많이 찬 쪽을 남긴다
    return out


def repair_product_names(doc: dict, text: str, *, exclude=None,
                         known_products=None) -> dict:
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
    known_products: 그 마켓의 1층 제품명 집합. ③ 타이브레이커에만 쓴다.
    """
    if not product_hashtags(text, exclude=exclude):
        return doc                               # 해시태그 없는 소스(디시) → 무변경

    items = list(doc.get("reviews") or [])
    # 1패스: ①로 확정되는 이름을 먼저 모은다 — 2패스의 흡수 금지 목록(`taken`)이 된다.
    #        순서에 의존하면 같은 입력이 항목 순서만 달라도 결과가 갈린다.
    taken = [r.get("mentioned_product") for r in items
             if resolve_product_name(r.get("mentioned_product"), text, exclude=exclude,
                                     known_products=known_products)[1] == "keep"]
    repaired: list[dict] = []
    for r in items:
        pick, why = resolve_product_name(r.get("mentioned_product"), text, exclude=exclude,
                                         known_products=known_products, taken=taken)
        repaired.append(r if why == "keep" else {**r, "mentioned_product": pick})
    doc["reviews"] = _fold_by_product(repaired)
    return doc


def resolve_product_name(name: str | None, text: str, *, exclude=None,
                         known_products=None, taken=None) -> tuple[str | None, str]:
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

    사유 문자열: `keep`(①) · `sole_tag`(②) · `l1_tiebreak`(③) · `hold_*`(④) · `no_tags`.
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


def build_thread_prompt(title: str | None, texts: list[str]) -> str:
    """조각들에 [S<n>] 번호를 붙인 스레드 프롬프트. 번호가 귀속의 유일한 근거다."""
    head = f"[제목] {title}\n" if title else ""
    body = "\n".join(f"[S{i}] {t}" for i, t in enumerate(texts))
    return head + body


def extract_thread(title: str | None, texts: list[str], llm: LLM,
                   model: str | None = None) -> list[dict]:
    """
    스레드 조각들 → 조각별 문서 리스트(입력 순서 정렬, 길이 보장).
    응답에 빠진 조각은 빈 문서로 메운다 — 조용히 짧은 리스트를 돌려주면 호출부에서 귀속이 밀린다.
    """
    if not texts:
        return []
    out = llm.complete(
        build_thread_prompt(title, texts),
        system=LAYER2_THREAD_SYSTEM,
        schema=LAYER2_THREAD_SCHEMA,
        model=model,
        label="extract.layer2.thread",
    )
    by_id: dict[str, dict] = {}
    for doc in (out.get("docs") or []):
        by_id.setdefault(str(doc.get("source_id", "")).strip(), doc)
    docs = []
    for i in range(len(texts)):
        doc = dict(by_id.get(f"S{i}") or by_id.get(str(i)) or _empty_doc())
        doc.pop("source_id", None)               # 귀속은 리스트 위치로 끝났다
        docs.append(drop_hearsay_reviews(doc, texts[i]))
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
                      batch_size: int = MAX_THREAD_SOURCES) -> list[tuple]:
    """
    수집된 RawReview(글 + 댓글)를 **스레드 단위 배치**로 추출한다(계획 C-1).
    이 갤은 후기가 댓글에 많아(sources.py 주석) 댓글도 1급 후기로 취급한다.

    왜 배치인가: **배치 도입 전의 per-comment 경로**는 호출당 입력 토큰의 99.4%가 고정
    프롬프트였다(실측, `evals/cost_profile.py` 기본 모드 — 배치가 대체한 경로의 역사적 근거).
    댓글 1건당 1회 호출은 그 2,900자짜리 지시문을 댓글 수만큼 재전송한다는 뜻이다.
    현행 스레드 경로의 배치 크기 근거는 위 `MAX_THREAD_SOURCES` 주석(캐시 하드스탑 실측)이다.

    부수 이득(AC13): 같은 호출 안에 형제 댓글이 있으므로 **제품명을 생략한 댓글의 귀속**이 가능해진다.
    per-comment 경로에서는 원리적으로 불가능했던 케이스다.

    market 상속은 유지한다 — 댓글 단독 추출은 'ㅂ슬라임' 등으로 흔들려 개체연결을 막는다.

    반환: [(raw, doc), ...] — index.index_post 입력으로 그대로 사용(호출부 무변경).
    """
    threads = group_threads(raws)

    out: list[tuple] = []
    for _, thread in threads.items():
        post, cmts = thread["post"], thread["comments"]
        title = (post.raw_title if post else None) or \
                (cmts[0].meta.get("parent_title") if cmts else None)
        members = ([post] if post else []) + cmts

        docs: list[dict] = []
        for start in range(0, len(members), batch_size):
            chunk_members = members[start:start + batch_size]
            docs.extend(extract_thread(title, [r.text for r in chunk_members], llm, model))

        # 스레드 market 은 글에서 뽑은 것이 권위. 없으면 댓글 중 처음 잡힌 것.
        market = docs[0].get("market") if post and docs else None
        if not market:
            market = next((d.get("market") for d in docs if d.get("market")), None)
        for raw, doc in zip(members, docs):
            if market:
                doc["market"] = market
            out.append((raw, doc))
    return out


if __name__ == "__main__":
    import json
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
