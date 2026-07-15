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

from .llm_ops import LLM

_NO_RE = re.compile(r"[?&]no=(\d+)")

# ---------------------------------------------------------------- 통제어휘
FEEL_VOCAB = ["말랑", "말캉", "쫀득", "퐁신", "폭닥", "크리미", "로션크리미",
              "얄랑", "매트", "빳빳", "텐션감있는", "흐물거리는", "쳐지는", "흐름성있는"]
TYPE_ENUM = ["폼볼", "촉감류(점토)", "디폼", "난사", "눈꽃", "지글리", "크런치",
             "빈백", "클라우드", "샤베트", "클리어", "버글리", "젤라또"]
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
    "value": _nullable_obj({
        "krw": {"type": ["integer", "null"]},
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }),
    "overall": _obj({
        "stated_rating": {"type": ["number", "null"]},   # 작성자 명시 점수만
        "model_sentiment": {"type": "string", "enum": SENTIMENT},
        "rebuy_intent": {"type": "string", "enum": REBUY},
        "summary": _nstr(),
    }),
}

# 최상위: 후기(주문) 단위 사실(market·shipping_cs·flags) + 제품별 평가(reviews[]).
LAYER2_SCHEMA: dict = _obj({
    "market": {"type": ["string", "null"],
               "description": "마켓 식별자만(초성·약칭·마켓명). 제목/머리말의 마켓도 여기. "
                              "후기 전체에 하나(보통 1주문=1마켓). '자사몰/공홈/스토어' 등 일반어는 null."},
    "shipping_cs": _nullable_obj({
        "notes": _nstr(),
        "sentiment": {"type": "string", "enum": SENTIMENT},
        "evidence": _nstr(),
    }, description="배송·주문·문자·도착·교환·CS. 후기(주문) 전체 기준. 이걸로 제품 항목을 만들지 마라."),
    "reviews": {"type": "array", "items": _obj(_PRODUCT_PROPS),
                "description": "실제 '사용 경험/평가'를 서술한 슬라임마다 한 항목. 비교글이면 제품별 분리. "
                               "제목·배송만 있고 평가가 없는 건 항목으로 만들지 마라."},
    "flags": _obj({"toxic": {"type": "boolean"}}),
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
이건 후기가 아니라 '공식 정보원'이다 — 주관 평가/감상은 무시하고 객관 스펙만 뽑는다.

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
  📏 '6mm'·'8mm' 같은 mm 규격은 디폼(폼) 알갱이 지름을 뜻한다 → 기본 slime_type='디폼'.
  예) '6mm 40g', '8mm 25g', '6mm디폼' → 디폼. (제품명·테마어에 낚이지 말 것: '곰돌디핑'·'꼬끄카롱'은
  음식 테마지 종류가 아니다. 단 캡션이 다른 종류를 '명시'하면 그걸 우선한다.)
- beads: 제품에 든 비즈/토핑 구성요소를 캡션 표기 그대로 배열로 — 예) ['지렁이비즈', '퍼즐비즈']. 없으면 [].

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
    (광역/마켓 태그 필터는 현재 데이터에서 불필요해 생략 — 비매품 글이 그런 태그를 달기 시작하면 재도입.)
    """
    out: list[str] = []
    for tag in _HASHTAG_RE.findall(text or ""):
        if tag not in out:
            out.append(tag)
    return out


def extract_spec(text: str, llm: LLM, model: str | None = None) -> dict:
    """
    판매자 캡션 한 건 → 1층 공식 스펙(dict): {"products": [{product, scent, base_combo, slime_type, evidence}...]}.
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


def extract_review(text: str, llm: LLM, model: str | None = None) -> dict:
    """
    후기 텍스트 한 건 → 2층 JSON(dict):
      {"market": ..., "shipping_cs": ..., "reviews": [제품별 평가...], "flags": {...}}.
    market·shipping_cs 는 후기(주문) 단위, reviews 는 제품 단위. 비교글이면 reviews 가 제품 수만큼.
    스키마 강제 + 파싱 실패 1회 재시도.
    """
    return llm.complete(
        text,
        system=LAYER2_SYSTEM,
        schema=LAYER2_SCHEMA,
        model=model,
        label="extract.layer2",
    )


def extract_collected(raws: list, llm: LLM, model: str | None = None) -> list[tuple]:
    """
    수집된 RawReview(글 + 댓글)를 모두 '동일한 extract_review'로 추출한다.
    이 갤은 후기가 댓글에 많아(sources.py 주석) 댓글도 1급 후기로 취급한다.

    댓글은 보통 마켓을 명시하지 않으므로, 글과 같은 '제목+본문' 형태가 되도록
    부모 글 제목(meta['parent_title'])을 머리말로 붙여 추출한다
    (LAYER2_SYSTEM 이 '제목/머리말의 마켓'을 market 으로 읽음).
    그래도 market 이 비면 같은 스레드(부모 글)의 추출 market 을 상속한다.

    반환: [(raw, doc), ...] — index.index_post 입력으로 그대로 사용.
    """
    posts = [r for r in raws if r.meta.get("type") == "post"]
    comments = [r for r in raws if r.meta.get("type") == "comment"]

    out: list[tuple] = []
    thread_market: dict[str, str] = {}          # parent_no → 글에서 추출한 market

    for p in posts:                              # 글 먼저 — 스레드 market 확보
        doc = extract_review(p.text, llm, model)
        m = _NO_RE.search(p.url)
        if m and doc.get("market"):
            thread_market[m.group(1)] = doc["market"]
        out.append((p, doc))

    for c in comments:                           # 댓글 — 글과 동일 추출 + market 상속
        head = c.meta.get("parent_title")
        text = f"{head}\n{c.text}" if head else c.text
        doc = extract_review(text, llm, model)
        # 스레드(부모 글) market 이 권위: 댓글 자체 추출은 'ㅂ슬라임' 등으로 흔들려 개체연결을
        # 막으므로, 부모 글에서 깨끗이 추출된 market 이 있으면 그것으로 덮어쓴다.
        parent_mk = thread_market.get(c.meta.get("parent_no"))
        if parent_mk:
            doc["market"] = parent_mk
        out.append((c, doc))

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
