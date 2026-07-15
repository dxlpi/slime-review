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
  - 인스타=긍정편향, 디시=부정편향 → 소스별로 따로 집계하고 '갭'을 시그널로.
  - 보정(점수 깎기) 대신 투명화(양쪽 + 편향 라벨).
"""

from __future__ import annotations
from collections import Counter, defaultdict
from typing import Optional, Callable

SENT_SCORE = {"pos": 1.0, "neu": 0.0, "neg": -1.0}
PLATFORM_BIAS = {            # 알려진 편향 라벨 (투명화용)
    "instagram": "긍정 쏠림(실명·공개)",
    "dcinside":  "부정 쏠림(익명)",
}

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
        out[plat] = {**c, "net": round(net, 3),
                     "bias_label": PLATFORM_BIAS.get(plat, "미상")}
    return out


def sentiment_gap(by_source: dict) -> Optional[dict]:
    """인스타 net - 디시 net. 둘 다 있어야 의미."""
    ig, dc = by_source.get("instagram"), by_source.get("dcinside")
    if not ig or not dc:
        return None
    gap = round(ig["net"] - dc["net"], 3)
    return {"instagram_net": ig["net"], "dcinside_net": dc["net"], "gap": gap,
            "reading": _gap_reading(gap)}

def _gap_reading(gap: float) -> str:
    if gap >= 0.5:
        return "인스타선 호평인데 디시선 박함 — 마케팅 띄움 vs 실사용 괴리 의심"
    if gap <= -0.5:
        return "디시가 오히려 더 후함 — 드문 케이스, 확인 필요"
    return "두 소스 평가 대체로 수렴"


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
SUMMARY_PROMPT = """\
너는 슬라임 제품의 '실사용 후기'를 소스별로 균형 있게 요약한다.
입력에는 인스타 후기와 디시 후기가 섞여 있고, 각 플랫폼의 알려진 편향이 함께 주어진다.
(대가·무상 제공 '홍보성' 후기와 판매자 게시물은 이미 분리·제외됐다 — 여기 입력은 실사용분만.)
규칙:
- 두 소스를 하나로 평균내지 말 것. 인스타 관점과 디시 관점을 각각 제시.
- 두 소스가 일치하는 점과 갈리는 점을 명시.
- 각 플랫폼의 편향(인스타=긍정쏠림, 디시=부정쏠림)을 독자에게 라벨로 알려줄 것.
- 근거 없는 단정 금지. 후기에서 실제로 언급된 속성만.
출력: 3~5문장 한국어 요약.
"""

# 홍보성 후기 전용 요약 — 실사용과 '분리'해 편향을 명시(§10 라벨링, 보정 금지).
PROMO_SUMMARY_PROMPT = """\
아래는 이 제품의 '홍보성' 인스타 후기다 — 서포터즈/무상 제공/협찬 등 대가가 개입해
긍정으로 쏠릴 수 있는 글이다. 실사용 후기와 절대 합치지 말고, 이 버킷만 따로 요약한다.
규칙:
- 이 후기들이 '홍보성(대가·무상 제공)'임을 첫 문장에 라벨로 명시.
- 그 안에서 반복적으로 강조된 속성(향·질감 등)만 담백하게 정리.
- 실사용 후기 대비 신뢰도가 낮다는 점을 독자에게 알릴 것.
출력: 2~3문장 한국어 요약.
"""


def _is_promo(r: dict) -> bool:
    return r.get("review_class") == "promo"


def build_consolidated(product_ref: dict,
                       official_spec: Optional[dict],
                       reviews: list[dict],
                       llm_summarize: Optional[Callable[[str], str]] = None) -> dict:
    """제품 하나에 대한 종합 뷰(구조화). llm_summarize 주입 시 소스-aware 요약도 생성.

    홍보성(review_class='promo') 후기는 headline(by_source/gap/praised/criticized/summary)에서
    제외하고 별도 promo_view 로 분리 집계한다(§2·§10: 평균 금지, 라벨링+분리).
    홍보성이 하나도 없으면 promo_view=None → 기존 데이터와 출력이 동일(회귀 없음).
    """
    genuine = [r for r in reviews if not _is_promo(r)]
    promo = [r for r in reviews if _is_promo(r)]

    by_src = per_source_sentiment(genuine)              # headline = 실사용만
    official_scent = (official_spec or {}).get("official_scent")
    view = {
        "product": product_ref,
        "official_spec": official_spec,                 # 향료/풀조합/종류 (1층)
        "n_reviews": len(genuine),                      # 실사용 건수(홍보성 제외)
        "by_source": by_src,                            # 소스별 정서 분포 + 편향 라벨
        "sentiment_gap": sentiment_gap(by_src),         # 인스타↔디시 갭 (없으면 None)
        "praised": top_points(genuine, "pos"),          # 소스별 호평 속성
        "criticized": top_points(genuine, "neg"),       # 소스별 지적 속성
        "scent_divergence": scent_divergence(official_scent, genuine),
        "summary": None,
        "promo_view": None,                             # 홍보성 있으면 아래에서 채움
    }
    import json
    if llm_summarize and genuine:
        payload = {
            "product": product_ref,
            "by_source": by_src,
            "praised": view["praised"],
            "criticized": view["criticized"],
            "scent_divergence": view["scent_divergence"],
        }
        view["summary"] = llm_summarize(SUMMARY_PROMPT + "\n\n[입력]\n" +
                                        json.dumps(payload, ensure_ascii=False, indent=2))

    if promo:                                           # 홍보성 분리 버킷(있을 때만)
        promo_by_src = per_source_sentiment(promo)
        promo_view = {
            "n_promo": len(promo),
            "by_source": promo_by_src,
            "praised": top_points(promo, "pos"),
            "criticized": top_points(promo, "neg"),
            "summary": None,
        }
        if llm_summarize:
            promo_payload = {
                "product": product_ref,
                "by_source": promo_by_src,
                "praised": promo_view["praised"],
                "criticized": promo_view["criticized"],
            }
            promo_view["summary"] = llm_summarize(
                PROMO_SUMMARY_PROMPT + "\n\n[입력]\n" +
                json.dumps(promo_payload, ensure_ascii=False, indent=2))
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
    import json
    v = build_consolidated({"market": "빈짱", "product": "연유스무디"},
                           {"official_scent": "연유향", "type": ["지글리"]}, demo)
    print(json.dumps(v, ensure_ascii=False, indent=2))
