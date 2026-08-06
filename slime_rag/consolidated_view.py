# -*- coding: utf-8 -*-
"""
슬라임 RAG — 종합 뷰 + 소스 편향 집계

입력: 추출+개체연결이 끝난 2층 리뷰 레코드 리스트(추출 프롬프트 스키마).
      각 레코드는 최소:
        r["source"]["platform"]        # 'instagram' | 'dcinside'
        r["product_ref"]               # {"market":..., "product":...}
        r["overall"]["model_sentiment"]# 'pos'|'neu'|'neg'
        r["scent"]["perceived"], r["scent"]["sentiment"], ...
        r["texture"]..., r["shipping_cs"]..., 등 (없으면 None)
      shipping_cs 는 원래 후기(주문) 단위 필드(ADR-0005)지만, index.index_post 가 제품별
      팬아웃 행마다 복제해 넣어 준다 — 여기선 다른 속성과 동일하게 행 단위로 읽는다.

핵심 원칙: 소스를 순진하게 평균내지 않는다.
  - 소스별로 따로 집계하고 '갭'을 시그널로(net·건수). 보정(점수 깎기) 대신 소스별 투명화.
  - '긍정/부정 쏠림' 같은 편향 라벨은 노출하지 않는다(사용자 결정 2026-07-15).
  - 서포터(홍보성) 후기는 실사용과 분리하되, 소수라도 6기준(질감·향·소리·지속력·고객 응대·배송)/장단점 실내용을 요약해 포함.
"""

from __future__ import annotations
import json
from collections import Counter, defaultdict
from typing import Optional, Callable

from . import source_links

SENT_SCORE = {"pos": 1.0, "neu": 0.0, "neg": -1.0}

# 종합 요약에서 다룰 속성 필드(필드별 정서가 있는 것들)
ATTR_FIELDS = ["scent", "texture", "sound", "longevity", "shipping_cs"]


def _platform(r: dict) -> str:
    return (r.get("source") or {}).get("platform", "unknown")

def _overall_sent(r: dict) -> Optional[str]:
    return (r.get("overall") or {}).get("model_sentiment")


def per_source_sentiment(reviews: list[dict]) -> dict:
    """플랫폼별 정서 분포 + 순점수(net) + 건수."""
    by = defaultdict(lambda: {"pos": 0, "neu": 0, "neg": 0, "n": 0})
    for r in reviews:
        s = _overall_sent(r)
        if s in SENT_SCORE:
            by[_platform(r)][s] += 1
            by[_platform(r)]["n"] += 1
    out = {}
    for plat, c in by.items():
        n = c["n"] or 1
        net = (c["pos"] - c["neg"]) / n           # -1 ~ +1
        out[plat] = {**c, "net": round(net, 3)}
    return out


def sentiment_gap(by_source: dict) -> Optional[dict]:
    """인스타 net - 디시 net. 둘 다 있어야 의미."""
    ig, dc = by_source.get("instagram"), by_source.get("dcinside")
    if not ig or not dc:
        return None
    # gap 수치만 보고한다. 임계값 기반 자동 해석(괴리/수렴 문구)은 제거 —
    # 표본수 미반영·임의 상수(0.5)라 근거 약함. 통계적 방법은 데이터 확장 후 재도입 검토.
    gap = round(ig["net"] - dc["net"], 3)
    return {"instagram_net": ig["net"], "dcinside_net": dc["net"], "gap": gap}


def top_points(reviews: list[dict], polarity: str, per_platform: bool = True, top_k: int = 3):
    """소스별로 자주 나온 호평/지적 속성. polarity='pos'(호평)|'neg'(지적)."""
    buckets = defaultdict(Counter)
    for r in reviews:
        plat = _platform(r) if per_platform else "all"
        for f in ATTR_FIELDS:
            blk = r.get(f)
            if isinstance(blk, dict) and blk.get("sentiment") == polarity:
                buckets[plat][f] += 1
    return {plat: cnt.most_common(top_k) for plat, cnt in buckets.items()}


def scent_divergence(official_scent: Optional[str], reviews: list[dict]) -> Optional[dict]:
    """공식향 대비 '다른 향을 느꼈다'는 후기 비율 + 자주 나온 체감향."""
    if not official_scent:
        return None
    total, diverged = 0, 0
    perceived = Counter()
    for r in reviews:
        sc = r.get("scent") or {}
        p = sc.get("perceived")
        if not p:
            continue
        total += 1
        perceived[p] += 1
        # 공식향 토큰이 체감향에 안 들어가면 불일치로 카운트(단순 휴리스틱)
        if not any(tok and tok in p for tok in re.split(r"[\s/]+", official_scent)):
            diverged += 1
    if total == 0:
        return None
    return {"official": official_scent,
            "diverged_ratio": round(diverged / total, 3),
            "top_perceived": perceived.most_common(5),
            "n": total}


import re  # (scent_divergence에서 사용)


# ---------------------------------------------------------------- 종합 뷰 빌더
# 평가 기준(요약 섹션) 단일 출처 — 백엔드 스키마와 UI 표가 이 리스트 하나를 공유한다.
# 순서는 화면 표의 행 순서다. 제품 축 넷(질감·향·소리·지속력)은 ADR-0008 이 규정한 축과
# 같고, 주문 축(shipping_cs)은 표시 단계에서 고객 응대 / 배송 둘로 갈린다(ADR-0011).
CRITERIA: list[dict] = [
    {"key": "texture",   "ko": "질감",      "en": "Texture"},
    {"key": "scent",     "ko": "향",        "en": "Scent"},
    {"key": "sound",     "ko": "소리",      "en": "Sound"},
    {"key": "longevity", "ko": "지속력",    "en": "Longevity"},
    {"key": "cs",        "ko": "고객 응대", "en": "Customer service"},
    {"key": "shipping",  "ko": "배송",      "en": "Shipping"},
]
CRITERIA_KEYS = [c["key"] for c in CRITERIA]

# 리뷰 요약(소스별/통합 공통) 스키마 — 6기준 각각 미언급이면 null(빈칸), 장단점은 모든 측면 배열.
# strict structured outputs 용: 전 필드 required + additionalProperties=False.
SOURCE_REVIEW_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [*CRITERIA_KEYS, "pros", "cons"],
    "properties": {
        "texture": {"type": ["string", "null"],
                    "description": "질감 관련 후기 요약(1~2문장). 질감 언급이 없으면 null."},
        "scent":   {"type": ["string", "null"],
                    "description": "향 관련 후기 요약(1~2문장). 향 언급이 하나도 없으면 null(지어내기 금지)."},
        "sound":   {"type": ["string", "null"],
                    "description": "소리 관련 후기 요약(1~2문장) — 걀걀거림·꾸덕소리·기포음 등. "
                                   "언급이 없으면 null. 소리는 질감이 아니다(별개 축)."},
        "longevity": {"type": ["string", "null"],
                      "description": "지속력 관련 후기 요약(1~2문장) — 제품 수명·굳음·묽어짐·'빨리 죽음'. "
                                     "언급이 없으면 null. 배송과 무관한 제품 속성이다."},
        "cs":      {"type": ["string", "null"],
                    "description": "고객 응대 관련 후기 요약(1~2문장) — 문의 답변·교환·환불·사과·판매자 태도. "
                                   "언급이 없으면 null. 물류 사실(속도·포장·파손)은 shipping 이다."},
        "shipping": {"type": ["string", "null"],
                     "description": "배송 관련 후기 요약(1~2문장) — 발송·도착 속도·포장 상태·파손·누락. "
                                    "언급이 없으면 null. 사람의 응대는 cs, 제품 수명은 longevity 다."},
        # 장단점은 개조식('배송 지연')으로 새기 쉬워 말투 규칙을 필드에도 박아 둔다.
        "pros":    {"type": "array", "items": {"type": "string"},
                    "description": "장점(6기준 + 가격 등 모든 측면)을 '~해요'체 짧은 문장으로. 없으면 []."},
        "cons":    {"type": "array", "items": {"type": "string"},
                    "description": "단점(모든 측면)을 '~해요'체 짧은 문장으로. 없으면 []."},
    },
}

# 출력 말투 — 화면 카피(`web/`)가 전부 '~해요'체라('이 영역에서는 두 출처의 리뷰가 섞이지
# 않아요') 요약만 '~다'체로 나오면 한 화면에서 톤이 튄다. 세 요약 프롬프트가 이 블록을 공유한다
# (6기준 리스트와 같은 이유 — 말투를 바꿀 자리가 한 곳이어야 한다).
# ⚠️ 말투만 바꾸는 것이지 평가를 누그러뜨리는 게 아니다. 부정 후기를 순화하면 소스 편향이
#    지워진다 — 그건 이 프로젝트의 1급 기능을 깨는 것이다.
TONE = """\
[말투]
- 모든 문장을 '~해요'체로 끝낸다 (예: '손에 잘 안 붙는대요', '배송이 늦었다는 말이 많아요').
  '~다 / ~함 / ~임' 이나 명사로 끊는 개조식 종결을 쓰지 마라. pros/cons 항목도 같은 말투로,
  짧은 한 문장으로 쓴다 (예: '배송이 늦어요', 'X' '배송 지연').
- 존댓말이되 과공은 금지 — '~하십니다', '~해드려요' 같은 응대 투는 쓰지 않는다.
- 이모지·느낌표는 쓰지 않는다. 후기의 비속어·감탄사는 뜻만 옮긴다.
- ⚠️ 부정 평가를 부드럽게 누그러뜨리지 마라. '별로였다'는 '아쉬웠대요'가 아니라
  '별로였다는 말이 많아요'다 — 말투만 바꾸고 평가의 세기는 원문 그대로 둔다."""

# 한 재료 필드가 두 섹션으로 갈리는 곳(주문 축) — 프롬프트가 이 분기를 설명해야 한다.
_SPLIT_NOTE = """\
  ⚠️ 입력의 shipping_cs 재료 하나가 cs 와 shipping 두 섹션으로 갈린다. 물류 사실(발송·도착
     속도·포장·파손·누락)은 shipping, 사람의 응대(문의 답변·교환·환불·사과·태도)는 cs 로 보낸다.
     한 문장에 둘이 섞여 있으면 각 섹션에 해당 부분만 나눠 쓴다. 한쪽만 있으면 나머지는 null.
  ⚠️ 배송은 '주문 단위' 사실이라 같은 글의 여러 제품에 같은 내용이 붙어 있을 수 있다 —
     같은 주문의 배송 얘기를 제품 수만큼 부풀려 세지 마라.
  ⚠️ '슬라임이 빨리 죽었다' 같은 제품 수명은 배송도 응대도 아니다 → longevity 로 보낸다."""

# 출처별(인스타 또는 디시) 후기 요약 프롬프트. 이 출처 후기만 근거로.
SECTION_PROMPT = """\
너는 슬라임 한 제품(또는 한 마켓의 여러 제품)에 대한 '한 출처(플랫폼)'의 실사용 후기를
6개 평가 기준(질감/향/소리/지속력/고객 응대/배송) + 장단점으로 요약한다.
입력 by_attr 의 evidence 에 실제로 나온 내용만 근거로 삼는다. 지어내기 금지.
규칙:
- texture: 질감(말랑·쫀득·흐름성·꾸덕 등) 언급이 있으면 1~2문장 요약. 없으면 null.
- scent: 향(냄새) 언급이 있으면 1~2문장으로 요약. 향 언급이 전혀 없으면 null (억지 서술 금지).
- sound: 소리(걀걀거림·꾸덕소리·기포음·터짐음) 언급이 있으면 1~2문장 요약. 없으면 null.
  ⚠️ 소리는 질감과 별개 축이다 — '걀걀거린다'를 texture 에 넣지 마라.
- longevity: 지속력(제품 수명·굳음·묽어짐·'빨리 죽음') 언급이 있으면 1~2문장 요약. 없으면 null.
- cs: 고객 응대(문의 답변·교환·환불·사과·판매자 태도) 언급이 있으면 1~2문장 요약. 없으면 null.
- shipping: 배송(발송·도착 속도·포장 상태·파손·누락) 언급이 있으면 1~2문장 요약. 없으면 null.
{split}
- pros/cons: 6기준을 포함한 모든 측면(가격 등)의 장점/단점을 각각 짧은 항목으로.
  해당 없으면 빈 배열 []. 근거 없는 장단점 창작 금지.
- 항목에 product 라벨이 있으면(마켓 단위 요약) 제품별로 갈리는 평가는 제품명을 표기해 구분한다
  (예: '[한글과자한줌] 비누향 지적'). 서로 다른 제품의 평가를 하나로 뭉뚱그리지 않는다.
- 이 출처의 후기만 본다. 다른 출처와 비교하지 않는다.
(대가·무상 '홍보성' 후기와 판매자 게시물은 이미 분리됐다 — 입력은 실사용분만.)
{tone}
""".format(split=_SPLIT_NOTE, tone=TONE)

# 통합 리뷰 프롬프트 — 두 출처의 '이미 요약된' 결과 + 갭 지표를 받아 reconciliation. 평균 금지.
INTEGRATED_PROMPT = """\
너는 인스타 후기 요약과 디시 후기 요약, 그리고 출처 갭 지표를 받아 두 출처를 '통합'한다.
절대 평균내지 말 것 — 두 출처가 '일치하는 점'과 '갈리는 점'을 드러내는 게 목적이다.
규칙:
- 6기준(texture/scent/sound/longevity/cs/shipping) 각각에 같은 규칙을 적용한다:
  두 출처 평가가 수렴하면 그 합의를, 갈리면 어떻게 다른지(sentiment_gap 수치 참고) 명시.
  한쪽에만 언급이 있으면 그 출처만 있었다고 밝힌다. 둘 다 없으면 null.
  (디시는 배송 지연을, 인스타는 포장을 말하는 식으로 자주 갈린다.)
- 입력 요약에 없는 기준을 새로 지어내지 마라 — 두 입력이 모두 null 인 기준은 결과도 null 이다.
- pros/cons: 두 출처 '공통' 장/단점과 '한쪽에서만' 나온 장/단점을 구분해 항목화
  (출처 표기 권장: 예 '[디시] 배송이 늦어요').
- 점수를 하나로 섞지 말고 출처별 관점을 유지하라.
{tone}
""".format(tone=TONE)

# 서포터(홍보성) 후기 전용 요약 — 실사용과 '분리'하되, 실제 언급된 6기준·장단점을 담백히 요약.
SUPPORTER_SECTION_PROMPT = """\
아래는 이 제품(또는 이 마켓)의 '서포터/무상 제공(협찬)' 인스타 후기다. 실제 언급된
6기준(질감/향/소리/지속력/고객 응대/배송)과 장단점만 요약한다.
지어내기 금지, 미언급은 null/빈배열. 실사용 후기와 합치지 말고 이 버킷만 요약한다.
규칙:
- texture / scent / sound / longevity: 언급이 있으면 각각 1~2문장, 없으면 null.
  소리는 질감과 별개 축이고, 지속력은 배송과 무관한 제품 속성이다.
- cs: 고객 응대(문의 답변·교환·환불·태도) 언급이 있으면 1~2문장, 없으면 null.
- shipping: 배송(발송·도착 속도·포장·파손) 언급이 있으면 1~2문장, 없으면 null. (서포터 발송은
  일반 주문과 경로가 달라 배송 경험이 실사용 후기와 다를 수 있다 — 그래서 버킷을 섞지 않는다.)
{split}
- pros/cons: 6기준을 포함한 모든 측면의 장점/단점을 항목화. 없으면 빈 배열 [].
- 항목에 product 라벨이 있으면(마켓 단위) 제품별로 갈리는 평가는 제품명을 표기한다.
{tone}
""".format(split=_SPLIT_NOTE, tone=TONE)


def _is_promo(r: dict) -> bool:
    return r.get("review_class") == "promo"


# 속성별로 요약 근거가 되는 salient 필드(evidence·sentiment 외 추가로 넘길 것).
_SALIENT = {
    "scent":       ["perceived", "vs_official_comment"],
    "texture":     ["feel", "feel_simile", "feel_other", "hand_stick", "hand_residue"],
    "sound":       ["notes"],
    "longevity":   ["notes"],
    "shipping_cs": ["notes"],
}


def _source_material(reviews: list[dict], *, tag_products: bool = False) -> dict:
    """한 소스 후기 → 속성별 evidence 재료(LLM 섹션 요약 입력). 근거 없는 속성은 키 자체를 뺀다.

    tag_products=True(마켓 단위 요약): 항목마다 product 라벨을 붙여 서로 다른 제품의 평가가
    요약에서 뭉개지지 않게 한다. 제품 단위 요약에선 라벨이 불필요한 노이즈라 붙이지 않는다.
    """
    by_attr: dict[str, list] = {}
    for f in ATTR_FIELDS:
        items = []
        for r in reviews:
            blk = r.get(f)
            if not isinstance(blk, dict):
                continue
            item = {"sentiment": blk.get("sentiment"), "evidence": blk.get("evidence")}
            if tag_products:
                item["product"] = (r.get("product_ref") or {}).get("product") or "제품미상"
            for k in _SALIENT.get(f, []):
                v = blk.get(k)
                if v not in (None, [], ""):
                    item[k] = v
            items.append(item)
        if items:
            by_attr[f] = items
    return by_attr


def _sectionize_source(reviews: list[dict], platform: str,
                       llm_sectionize: Callable[[str, dict], dict],
                       *, tag_products: bool = False) -> Optional[dict]:
    """소스(인스타/디시) 후기 → {6기준, pros, cons}. 후기 없으면 호출 안 함(상위 가드)."""
    payload = {"platform": platform,
               "by_attr": _source_material(reviews, tag_products=tag_products)}
    prompt = SECTION_PROMPT + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return llm_sectionize(prompt, SOURCE_REVIEW_SCHEMA)


def _sectionize_supporter(reviews: list[dict],
                          llm_sectionize: Callable[[str, dict], dict],
                          *, tag_products: bool = False) -> dict:
    """서포터(홍보성) 후기 → {6기준, pros, cons}. 실사용과 분리하되 실내용 요약."""
    payload = {"by_attr": _source_material(reviews, tag_products=tag_products)}
    prompt = SUPPORTER_SECTION_PROMPT + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return llm_sectionize(prompt, SOURCE_REVIEW_SCHEMA)


def _sectionize_integrated(ig_sum: dict, dc_sum: dict, gap: Optional[dict],
                           llm_sectionize: Callable[[str, dict], dict]) -> dict:
    """두 소스 요약 + 갭 → 통합 리뷰(reconciliation). 두 소스 모두 있을 때만 호출.

    입력은 2층(후기) 파생물만 — 1층(공식 스펙)에서 파생된 scent_divergence 는 넣지 않는다.
    스펙↔후기 완전 분리: 향 불일치는 코드 계산 지표로 뷰에 별도 표시되며, 요약 LLM 은
    공식 스펙을 볼 수 없어야 한다(스펙이 요약 문장에 스며드는 것 방지).
    """
    payload = {"instagram": ig_sum, "dcinside": dc_sum, "sentiment_gap": gap}
    prompt = INTEGRATED_PROMPT + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return llm_sectionize(prompt, SOURCE_REVIEW_SCHEMA)


def build_consolidated(product_ref: dict,
                       official_spec: Optional[dict],
                       reviews: list[dict],
                       llm_sectionize: Optional[Callable[[str, dict], dict]] = None) -> dict:
    """제품 하나(또는 product=None 이면 마켓 전체)에 대한 종합 뷰(구조화).

    - llm_sectionize 주입 시 review_summaries(인스타/디시/통합)를 6기준(CRITERIA)/장단점으로 산출.
      · 각 기준은 해당 소스에 언급이 없으면 null(빈칸) — 지어내기 금지.
      · 통합은 두 소스 모두 실사용 후기가 있을 때만 생성(reconciliation, 평균 금지). 아니면 None.
      · 요약 입력은 2층(후기)만 — official_spec 은 어떤 요약 프롬프트에도 들어가지 않는다
        (스펙↔후기 완전 분리). 스펙은 뷰의 official_spec 키로만 나간다.
        ⚠️ `official_spec["official_texture"]`(판매자가 쓴 질감 서술)도 예외가 아니다. 판매자 말은
        구조상 항상 긍정이라, 후기 요약에 섞으면 인스타 편향이 한 겹 더 얹힌다(소스 미평균 규칙).
        화면에서도 1층 스펙 카드에만 나가고 '판매자 제공 정보'로 라벨된다.
    - 마켓 모드(product_ref["product"] 가 없음): official_spec 은 제품 단위 개념이라 None 전제
      (scent_divergence 도 자연히 None). 요약 재료엔 항목별 product 라벨을 붙여 제품 간
      평가가 뭉개지지 않게 한다.

    서포터(review_class='promo') 후기는 headline·review_summaries 에서 제외하되, 별도 promo_view 에
    '서포터 리뷰'로 6기준·장단점을 실제 내용 그대로 요약(소수라도 포함). 없으면 promo_view=None.
    """
    market_mode = not product_ref.get("product")
    genuine = [r for r in reviews if not _is_promo(r)]
    promo = [r for r in reviews if _is_promo(r)]
    ig = [r for r in genuine if _platform(r) == "instagram"]
    dc = [r for r in genuine if _platform(r) == "dcinside"]

    by_src = per_source_sentiment(genuine)              # headline = 실사용만
    official_scent = (official_spec or {}).get("official_scent")
    view = {
        "product": product_ref,
        "official_spec": official_spec,                 # 향료/풀조합/종류/질감서술/URL (1층)
        "n_reviews": len(genuine),                      # 실사용 건수(서포터 제외)
        "by_source": by_src,                            # 소스별 정서 분포(net·건수)
        "sentiment_gap": sentiment_gap(by_src),         # 인스타↔디시 갭 (없으면 None)
        "praised": top_points(genuine, "pos"),          # 소스별 호평 속성
        "criticized": top_points(genuine, "neg"),       # 소스별 지적 속성
        "scent_divergence": scent_divergence(official_scent, genuine),
        # 소스별 6기준·장단점 요약 + 통합. llm_sectionize 없으면 전부 None.
        "review_summaries": {"instagram": None, "dcinside": None, "integrated": None},
        "promo_view": None,                             # 서포터 있으면 아래에서 채움
        # 근거 원문 링크 목록 — 실사용/서포터를 버킷째 분리한다(집계를 안 섞듯 링크도 안 섞는다).
        # 조각 식별자로 중복 제거 + 렌더된 URL 로 그룹핑(source_links). 링크 없는 행은 빠진다.
        "sources": {"genuine": source_links.group_evidence_sources(genuine),
                    "promo": source_links.group_evidence_sources(promo)},
    }

    if llm_sectionize:
        rs = view["review_summaries"]
        if ig:
            rs["instagram"] = _sectionize_source(ig, "instagram", llm_sectionize,
                                                 tag_products=market_mode)
        if dc:
            rs["dcinside"] = _sectionize_source(dc, "dcinside", llm_sectionize,
                                                tag_products=market_mode)
        if rs["instagram"] and rs["dcinside"]:          # 두 소스 다 있어야 통합(평균 아님)
            rs["integrated"] = _sectionize_integrated(
                rs["instagram"], rs["dcinside"], view["sentiment_gap"], llm_sectionize)

    if promo:                                           # 서포터(홍보성) 분리 버킷 — 실내용 요약
        promo_view = {"n_promo": len(promo), **{k: None for k in CRITERIA_KEYS},
                      "pros": [], "cons": []}
        if llm_sectionize:
            promo_view.update(_sectionize_supporter(promo, llm_sectionize,
                                                    tag_products=market_mode))
        view["promo_view"] = promo_view
    return view


if __name__ == "__main__":
    # 미니 예시(가짜 데이터)
    demo = [
        {"source": {"platform": "instagram"}, "product_ref": {"market": "빈짱", "product": "연유스무디"},
         "overall": {"model_sentiment": "pos"}, "scent": {"perceived": "연유향", "sentiment": "pos"},
         "texture": {"sentiment": "pos"}},
        {"source": {"platform": "dcinside"}, "product_ref": {"market": "빈짱", "product": "연유스무디"},
         "overall": {"model_sentiment": "neg"}, "scent": {"perceived": "비누향", "sentiment": "neg"},
         "texture": {"sentiment": "neg"}},
        {"source": {"platform": "dcinside"}, "product_ref": {"market": "빈짱", "product": "연유스무디"},
         "overall": {"model_sentiment": "neu"}, "scent": {"perceived": "비누향", "sentiment": "neu"},
         "shipping_cs": {"notes": "배송 3일 지연", "sentiment": "neg"}},
    ]
    # 가짜 sectionize: 실제 LLM 없이 재료가 넘어오는지·빈 섹션 처리만 확인.
    # 재료 필드(shipping_cs) 하나가 cs·shipping 두 섹션으로 갈리는 것도 여기서 흉내낸다.
    _MATERIAL = {"texture": "texture", "scent": "scent", "sound": "sound",
                 "longevity": "longevity", "cs": "shipping_cs", "shipping": "shipping_cs"}

    def _fake_sectionize(prompt: str, schema: dict) -> dict:
        tail = prompt.split("[입력]", 1)[-1]
        out = {k: (f"{k} 요약(mock)" if f'"{_MATERIAL[k]}"' in tail else None)
               for k in CRITERIA_KEYS}
        return {**out, "pros": ["말랑함(mock)"], "cons": []}

    v = build_consolidated({"market": "빈짱", "product": "연유스무디"},
                           {"official_scent": "연유향", "type": ["지글리"]}, demo,
                           llm_sectionize=_fake_sectionize)
    print(json.dumps(v, ensure_ascii=False, indent=2))
