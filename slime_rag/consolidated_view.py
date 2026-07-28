# -*- coding: utf-8 -*-
"""
슬라임 RAG — 종합 뷰 + 소스 편향 집계

입력: 추출+개체연결이 끝난 2층 리뷰 레코드 리스트(추출 프롬프트 스키마).
      각 레코드는 최소:
        r["source"]["platform"]        # 'instagram' | 'dcinside'
        r["product_ref"]               # {"market":..., "product":...}
        r["overall"]["model_sentiment"]# 'pos'|'neu'|'neg'
        r["scent"]["perceived"], r["scent"]["sentiment"], ...
        r["texture"]..., r["value"]..., 등 (없으면 None)

핵심 원칙: 소스를 순진하게 평균내지 않는다.
  - 소스별로 따로 집계하고 '갭'을 시그널로(net·건수). 보정(점수 깎기) 대신 소스별 투명화.
  - '긍정/부정 쏠림' 같은 편향 라벨은 노출하지 않는다(사용자 결정 2026-07-15).
  - 서포터(홍보성) 후기는 실사용과 분리하되, 소수라도 향/질감/장단점 실내용을 요약해 포함.
"""

from __future__ import annotations
import json
from collections import Counter, defaultdict
from typing import Optional, Callable

SENT_SCORE = {"pos": 1.0, "neu": 0.0, "neg": -1.0}

# 종합 요약에서 다룰 속성 필드(필드별 정서가 있는 것들)
ATTR_FIELDS = ["scent", "texture", "sound", "longevity", "value", "shipping_cs"]


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
# 리뷰 요약(소스별/통합 공통) 스키마 — 향/질감은 미언급이면 null(빈칸), 장단점은 모든 측면 배열.
# strict structured outputs 용: 전 필드 required + additionalProperties=False.
SOURCE_REVIEW_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scent", "texture", "pros", "cons"],
    "properties": {
        "scent":   {"type": ["string", "null"],
                    "description": "향 관련 후기 요약(1~2문장). 향 언급이 하나도 없으면 null(지어내기 금지)."},
        "texture": {"type": ["string", "null"],
                    "description": "질감 관련 후기 요약(1~2문장). 질감 언급이 없으면 null."},
        "pros":    {"type": "array", "items": {"type": "string"},
                    "description": "장점(향·질감·배송·가격·지속력·소리 등 모든 측면). 없으면 []."},
        "cons":    {"type": "array", "items": {"type": "string"},
                    "description": "단점(모든 측면). 없으면 []."},
    },
}

# 소스별(인스타 또는 디시) 후기 요약 프롬프트. 이 소스 후기만 근거로.
SECTION_PROMPT = """\
너는 슬라임 한 제품에 대한 '한 소스(플랫폼)'의 실사용 후기를 향/질감/장단점으로 요약한다.
입력 by_attr 의 evidence 에 실제로 나온 내용만 근거로 삼는다. 지어내기 금지.
규칙:
- scent: 향(냄새) 언급이 있으면 1~2문장으로 요약. 향 언급이 전혀 없으면 null (억지 서술 금지).
- texture: 질감(말랑·쫀득·흐름성 등) 언급이 있으면 1~2문장 요약. 없으면 null.
- pros/cons: 향·질감을 포함한 모든 측면(배송·가격·지속력·소리 등)의 장점/단점을 각각 짧은 항목으로.
  해당 없으면 빈 배열 []. 근거 없는 장단점 창작 금지.
- 이 소스의 후기만 본다. 다른 소스와 비교하지 않는다.
(대가·무상 '홍보성' 후기와 판매자 게시물은 이미 분리됐다 — 입력은 실사용분만.)
"""

# 통합 리뷰 프롬프트 — 두 소스의 '이미 요약된' 결과 + 갭 지표를 받아 reconciliation. 평균 금지.
INTEGRATED_PROMPT = """\
너는 인스타 후기 요약과 디시 후기 요약, 그리고 소스 갭 지표를 받아 두 소스를 '통합'한다.
절대 평균내지 말 것 — 두 소스가 '일치하는 점'과 '갈리는 점'을 드러내는 게 목적이다.
규칙:
- scent: 두 소스 향 평가가 수렴하면 그 합의를, 갈리면 어떻게 다른지(sentiment_gap 수치 참고) 명시.
  한쪽만 향 언급이 있으면 그 소스만 있었다고 밝힌다. 둘 다 없으면 null.
- texture: 위와 동일.
- pros/cons: 두 소스 '공통' 장/단점과 '한쪽에서만' 나온 장/단점을 구분해 항목화(소스 표기 권장: 예 '[디시] 배송 지연').
- 점수를 하나로 섞지 말고 소스별 관점을 유지하라.
"""

# 서포터(홍보성) 후기 전용 요약 — 실사용과 '분리'하되, 실제 언급된 향/질감/장단점을 담백히 요약.
SUPPORTER_SECTION_PROMPT = """\
아래는 이 제품의 '서포터/무상 제공(협찬)' 인스타 후기다. 실제 언급된 향/질감/장단점만 요약한다.
지어내기 금지, 미언급은 null/빈배열. 실사용 후기와 합치지 말고 이 버킷만 요약한다.
규칙:
- scent: 향 언급이 있으면 1~2문장, 없으면 null.
- texture: 질감 언급이 있으면 1~2문장, 없으면 null.
- pros/cons: 향·질감을 포함한 모든 측면의 장점/단점을 항목화. 없으면 빈 배열 [].
"""


def _is_promo(r: dict) -> bool:
    return r.get("review_class") == "promo"


# 속성별로 요약 근거가 되는 salient 필드(evidence·sentiment 외 추가로 넘길 것).
_SALIENT = {
    "scent":       ["perceived", "vs_official_comment"],
    "texture":     ["feel", "feel_simile", "feel_other", "hand_stick", "hand_residue"],
    "sound":       ["notes"],
    "longevity":   ["notes"],
    "value":       ["krw"],
    "shipping_cs": ["notes"],
}


def _source_material(reviews: list[dict]) -> dict:
    """한 소스 후기 → 속성별 evidence 재료(LLM 섹션 요약 입력). 근거 없는 속성은 키 자체를 뺀다."""
    by_attr: dict[str, list] = {}
    for f in ATTR_FIELDS:
        items = []
        for r in reviews:
            blk = r.get(f)
            if not isinstance(blk, dict):
                continue
            item = {"sentiment": blk.get("sentiment"), "evidence": blk.get("evidence")}
            for k in _SALIENT.get(f, []):
                v = blk.get(k)
                if v not in (None, [], ""):
                    item[k] = v
            items.append(item)
        if items:
            by_attr[f] = items
    return by_attr


def _sectionize_source(reviews: list[dict], platform: str,
                       llm_sectionize: Callable[[str, dict], dict]) -> Optional[dict]:
    """소스(인스타/디시) 후기 → {scent, texture, pros, cons}. 후기 없으면 호출 안 함(상위 가드)."""
    payload = {"platform": platform, "by_attr": _source_material(reviews)}
    prompt = SECTION_PROMPT + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return llm_sectionize(prompt, SOURCE_REVIEW_SCHEMA)


def _sectionize_supporter(reviews: list[dict],
                          llm_sectionize: Callable[[str, dict], dict]) -> dict:
    """서포터(홍보성) 후기 → {scent, texture, pros, cons}. 실사용과 분리하되 실내용 요약."""
    payload = {"by_attr": _source_material(reviews)}
    prompt = SUPPORTER_SECTION_PROMPT + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return llm_sectionize(prompt, SOURCE_REVIEW_SCHEMA)


def _sectionize_integrated(ig_sum: dict, dc_sum: dict, gap: Optional[dict],
                           scent_div: Optional[dict],
                           llm_sectionize: Callable[[str, dict], dict]) -> dict:
    """두 소스 요약 + 갭 → 통합 리뷰(reconciliation). 두 소스 모두 있을 때만 호출."""
    payload = {"instagram": ig_sum, "dcinside": dc_sum,
               "sentiment_gap": gap, "scent_divergence": scent_div}
    prompt = INTEGRATED_PROMPT + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return llm_sectionize(prompt, SOURCE_REVIEW_SCHEMA)


def build_consolidated(product_ref: dict,
                       official_spec: Optional[dict],
                       reviews: list[dict],
                       llm_sectionize: Optional[Callable[[str, dict], dict]] = None) -> dict:
    """제품 하나에 대한 종합 뷰(구조화).

    - llm_sectionize 주입 시 review_summaries(인스타/디시/통합)를 향/질감/장단점으로 산출.
      · 향/질감은 해당 소스에 언급이 없으면 null(빈칸) — 지어내기 금지.
      · 통합은 두 소스 모두 실사용 후기가 있을 때만 생성(reconciliation, 평균 금지). 아니면 None.

    서포터(review_class='promo') 후기는 headline·review_summaries 에서 제외하되, 별도 promo_view 에
    '서포터 리뷰'로 향/질감/장단점을 실제 내용 그대로 요약(소수라도 포함). 없으면 promo_view=None.
    """
    genuine = [r for r in reviews if not _is_promo(r)]
    promo = [r for r in reviews if _is_promo(r)]
    ig = [r for r in genuine if _platform(r) == "instagram"]
    dc = [r for r in genuine if _platform(r) == "dcinside"]

    by_src = per_source_sentiment(genuine)              # headline = 실사용만
    official_scent = (official_spec or {}).get("official_scent")
    view = {
        "product": product_ref,
        "official_spec": official_spec,                 # 향료/풀조합/종류/URL (1층)
        "n_reviews": len(genuine),                      # 실사용 건수(서포터 제외)
        "by_source": by_src,                            # 소스별 정서 분포(net·건수)
        "sentiment_gap": sentiment_gap(by_src),         # 인스타↔디시 갭 (없으면 None)
        "praised": top_points(genuine, "pos"),          # 소스별 호평 속성
        "criticized": top_points(genuine, "neg"),       # 소스별 지적 속성
        "scent_divergence": scent_divergence(official_scent, genuine),
        # 소스별 향/질감/장단점 요약 + 통합. llm_sectionize 없으면 전부 None.
        "review_summaries": {"instagram": None, "dcinside": None, "integrated": None},
        "promo_view": None,                             # 서포터 있으면 아래에서 채움
    }

    if llm_sectionize:
        rs = view["review_summaries"]
        if ig:
            rs["instagram"] = _sectionize_source(ig, "instagram", llm_sectionize)
        if dc:
            rs["dcinside"] = _sectionize_source(dc, "dcinside", llm_sectionize)
        if rs["instagram"] and rs["dcinside"]:          # 두 소스 다 있어야 통합(평균 아님)
            rs["integrated"] = _sectionize_integrated(
                rs["instagram"], rs["dcinside"],
                view["sentiment_gap"], view["scent_divergence"], llm_sectionize)

    if promo:                                           # 서포터(홍보성) 분리 버킷 — 실내용 요약
        promo_view = {"n_promo": len(promo),
                      "scent": None, "texture": None, "pros": [], "cons": []}
        if llm_sectionize:
            promo_view.update(_sectionize_supporter(promo, llm_sectionize))
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
         "value": {"sentiment": "pos"}},
    ]
    # 가짜 sectionize: 실제 LLM 없이 재료가 넘어오는지·빈 섹션 처리만 확인.
    def _fake_sectionize(prompt: str, schema: dict) -> dict:
        has_scent = '"scent"' in prompt.split("[입력]", 1)[-1]
        has_texture = '"texture"' in prompt.split("[입력]", 1)[-1]
        return {"scent": "향 요약(mock)" if has_scent else None,
                "texture": "질감 요약(mock)" if has_texture else None,
                "pros": ["가성비(mock)"], "cons": []}

    v = build_consolidated({"market": "빈짱", "product": "연유스무디"},
                           {"official_scent": "연유향", "type": ["지글리"]}, demo,
                           llm_sectionize=_fake_sectionize)
    print(json.dumps(v, ensure_ascii=False, indent=2))
