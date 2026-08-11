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
  - 서포터(홍보성) 후기는 실사용과 분리하되, 소수라도 기준/장단점 실내용을 요약해 포함.

요약은 **축이 둘**이다(ADR-0015):
  · 제품 축 — 질감·향·소리·지속력 + 장단점. `build_consolidated`, 제품(또는 마켓)당 한 벌.
  · 주문 축 — 고객 응대·배송. `build_order_view`, **마켓당 한 벌**. 판매자/주문의 사실이라
    제품에 귀속되지 않는다. 제품 페이지는 이 한 벌을 '이 마켓 전체 기준'으로 빌려 쓴다.
"""

from __future__ import annotations
import json
from collections import Counter, defaultdict
from typing import Optional, Callable

from . import source_links

SENT_SCORE = {"pos": 1.0, "neu": 0.0, "neg": -1.0}

# 종합 요약에서 다룰 속성 필드(필드별 정서가 있는 것들). **축이 둘**이다(ADR-0015):
# 제품 축은 제품 하나에 붙는 속성이고, 주문 축(`shipping_cs`)은 주문 하나에 붙는 사실이라
# 제품이 아니라 **마켓**에 귀속된다. `top_points` 처럼 전 속성을 훑는 집계만 둘을 합쳐 본다.
PRODUCT_ATTR_FIELDS = ["scent", "texture", "sound", "longevity"]
ORDER_ATTR_FIELDS = ["shipping_cs"]
ATTR_FIELDS = [*PRODUCT_ATTR_FIELDS, *ORDER_ATTR_FIELDS]


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


def _fold_orders(reviews: list[dict]) -> list[dict]:
    """주문 축 재료에서 **팬아웃 복제분을 접는다**(ADR-0015).

    `index_post` 가 `shipping_cs` 를 제품 행마다 복제하므로, 접지 않으면 한 주문의 배송
    이야기가 그 글이 언급한 제품 수만큼 세어진다. 접는 키는 근거 목록·커뮤니티 패널이 쓰는
    것과 **같은 조각 식별자**(`source_links.evidence_group_key`)다 — 한 조각을 세는 규칙이
    화면마다 갈리면 안 된다.

    ⚠️ `source_ref` 가 없는 행(ADR-0009 이전 색인분 — 백필 없음)은 접을 키가 없어 그대로
       남고, 여전히 제품 수만큼 세어진다. 해결은 재수집·재적재(`setup(reset=True)`)뿐이고,
       여기서 내용 해시 같은 대체 키를 쓰지는 않는다 — 서로 다른 주문의 '배송 빨라요'가 한
       건으로 접히는 **과소 집계**가 과대 집계보다 나중에 알아채기 어렵다.
    """
    out, seen = [], set()
    for i, r in enumerate(reviews):
        key = source_links.evidence_group_key(r.get("source_ref")) or ("_nokey", i)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def criterion_stats(reviews: list[dict], keys: Optional[list[str]] = None) -> dict:
    """기준 각각의 **소스별 정서 건수** — 요약 문장의 '다수/소수' 판정 근거.

    왜 코드가 세나: 어느 쪽이 다수인지는 집계 사실이지 문장이 아니다. LLM 에게 세게 하면
    표본에 없는 확신이 산문에 섞이고(향 불일치·소스 갭을 여기서 계산하는 것과 같은 이유),
    '출처 갭이 큰 편이라' 같은 메타 설명이 본문으로 샌다. 숫자는 여기서 내고 문장은 내용만 쓴다.

    `keys` 를 주면 그 기준만 센다 — 축이 갈렸기 때문이다(ADR-0015). 제품 축 요약은 제품
    기준만, 주문 축 요약은 주문 기준만 재료로 받는다.

    반환: {기준키: {by_source, total, only_source, split}}
      · only_source — 한 출처에서만 언급된 기준. 둘 다면 None.
      · split — 같은 기준에 pos 와 neg 가 함께 있음. 임계값 없음, 존재 여부다.

    ⚠️ `cs` 와 `shipping` 은 재료(`shipping_cs`)가 하나라 정서도 하나다 — 두 기준의 건수가
       같게 나오는 건 버그가 아니라 ADR-0011 의 재료 분기 그대로다.
    ⚠️ 주문 축(`cs`·`shipping`)은 `_fold_orders` 로 **조각 단위로 접고 나서** 센다. 접지 않으면
       비교글 하나가 제품 수만큼 세어진다(ADR-0014 '유보'에 적혀 있던 부풀림 — ADR-0015 가
       코드로 막는다). 제품 축은 접지 않는다: 거긴 행 하나가 곧 제품 하나라 복제가 아니다.
    """
    out: dict = {}
    for key in (keys if keys is not None else CRITERIA_KEYS):
        field = CRITERION_MATERIAL[key]
        rows = _fold_orders(reviews) if CRITERION_SCOPE[key] == "market" else reviews
        by_src: dict[str, dict] = {}
        for r in rows:
            blk = r.get(field)
            if not isinstance(blk, dict):
                continue
            s = blk.get("sentiment")
            if s not in SENT_SCORE:
                continue
            b = by_src.setdefault(_platform(r), {"pos": 0, "neg": 0, "neu": 0, "n": 0})
            b[s] += 1
            b["n"] += 1
        total = {"pos": 0, "neg": 0, "neu": 0, "n": 0}
        for b in by_src.values():
            for k in total:
                total[k] += b[k]
        mentioned = [p for p, b in by_src.items() if b["n"]]
        out[key] = {
            "by_source": by_src,
            "total": total,
            "only_source": mentioned[0] if len(mentioned) == 1 else None,
            "split": total["pos"] > 0 and total["neg"] > 0,
        }
    return out


def _counts_material(stats: dict, platform: Optional[str] = None) -> dict:
    """프롬프트에 실을 압축 카운트 — 언급된 기준만, pos/neg 만. 다수/소수 판단 근거다.

    platform 을 주면 그 출처 것만(소스별 요약은 다른 출처를 보면 안 된다).
    ⚠️ 숫자를 문장에 쓰라는 재료가 아니다 — 어느 쪽을 verdict 에 둘지 고르라는 재료다.
    """
    out = {}
    for key, st in stats.items():
        c = st["by_source"].get(platform) if platform else st["total"]
        if c and c["n"]:
            out[key] = {"pos": c["pos"], "neg": c["neg"]}
    return out


# ---------------------------------------------------------------- 종합 뷰 빌더
# 평가 기준(요약 섹션) 단일 출처 — 백엔드 스키마와 UI 표가 이 리스트 하나를 공유한다.
# 순서는 화면 표의 행 순서다. 제품 축 넷(질감·향·소리·지속력)은 ADR-0008 이 규정한 축과
# 같고, 주문 축(shipping_cs)은 표시 단계에서 고객 응대 / 배송 둘로 갈린다(ADR-0011).
#
# `scope` — 그 기준이 **무엇에 귀속되는가**(ADR-0015). 제품 축은 제품 하나의 속성이고,
# 주문 축(고객 응대·배송)은 판매자/주문의 사실이라 **마켓 단위로 한 번** 집계·요약한다.
# 예전엔 여섯 개를 전부 제품 단위로 요약했는데, `index_post` 가 `shipping_cs` 를 제품 행마다
# 복제하기 때문에 비교글 하나의 배송 불만이 그 글이 언급한 **모든 제품의 요약**에 각각
# 관측된 것처럼 실렸다. 그건 표시 문제가 아니라 귀속(attribution) 오류다.
CRITERIA: list[dict] = [
    {"key": "texture",   "ko": "질감",      "en": "Texture",          "scope": "product"},
    {"key": "scent",     "ko": "향",        "en": "Scent",            "scope": "product"},
    {"key": "sound",     "ko": "소리",      "en": "Sound",            "scope": "product"},
    {"key": "longevity", "ko": "지속력",    "en": "Longevity",        "scope": "product"},
    {"key": "cs",        "ko": "고객 응대", "en": "Customer service", "scope": "market"},
    {"key": "shipping",  "ko": "배송",      "en": "Shipping",         "scope": "market"},
]
CRITERIA_KEYS = [c["key"] for c in CRITERIA]
CRITERION_SCOPE: dict[str, str] = {c["key"]: c["scope"] for c in CRITERIA}
# 축별 기준 키. 표 행 순서를 유지한다(화면이 두 벌을 합쳐 그릴 때 순서가 튀지 않게).
PRODUCT_KEYS = [c["key"] for c in CRITERIA if c["scope"] == "product"]
MARKET_KEYS = [c["key"] for c in CRITERIA if c["scope"] == "market"]

# 기준 → 그 기준을 채우는 재료 필드. 주문 축(shipping_cs) 재료 하나가 cs·shipping 둘로 갈린다
# (ADR-0011) — 그래서 두 기준의 건수 집계는 같은 블록에서 나온다(아래 criterion_stats 주석).
CRITERION_MATERIAL: dict[str, str] = {
    "texture": "texture", "scent": "scent", "sound": "sound",
    "longevity": "longevity", "cs": "shipping_cs", "shipping": "shipping_cs",
}

# 기준 하나의 값 — '다수 의견'과 '소수/반대'를 **구조로** 가른다(ADR-0014).
# 예전엔 문자열 한 칸이라 대립 평가가 '유분기 많다 vs 잘 안 붙는다'처럼 그냥 나란히 놓였고,
# 읽는 사람이 어느 쪽이 다수인지 알 길이 없었다. 자리를 둘로 나누면 그 판단이 강제된다.
_CRITERION_GUIDE = {
    "texture":   "질감(말랑·쫀득·흐름성·꾸덕·손붙음 등).",
    "scent":     "향(냄새).",
    "sound":     "소리(걀걀거림·꾸덕소리·기포음·터짐음). 소리는 질감이 아니다(별개 축).",
    "longevity": "지속력(제품 수명·굳음·묽어짐·'빨리 죽음'). 배송과 무관한 제품 속성이다.",
    "cs":        "고객 응대(문의 답변·교환·환불·사과·판매자 태도). 물류 사실은 shipping 이다.",
    "shipping":  "배송(발송·도착 속도·포장 상태·파손·누락). 사람의 응대는 cs, 제품 수명은 longevity 다.",
}


def _criterion_schema(ko: str, guide: str) -> dict:
    """`{verdict, minority}` — 미언급은 **두 칸 다 null**(빈칸이 곧 '언급 없음'이다)."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "minority"],
        "properties": {
            "verdict": {"type": ["string", "null"],
                        "description": f"{ko} — 다수 의견 1문장. {guide} "
                                       "언급이 없으면 null. '언급이 없어요' 같은 부재 서술을 "
                                       "문장으로 쓰지 마라 — null 이 그 뜻이다."},
            "minority": {"type": ["string", "null"],
                         "description": f"{ko} — 다수와 갈리는 소수 의견 1문장('일부는 ~'). "
                                        "갈리는 평가가 없으면 null. verdict 가 null 이면 여기도 null."},
        },
    }


def _review_schema(keys: list[str], *, pros_cons: bool) -> dict:
    """축 하나의 요약 스키마 — 기준 각각 {verdict, minority}(+장단점).

    strict structured outputs 용: 전 필드 required + additionalProperties=False.
    """
    props = {c["key"]: _criterion_schema(c["ko"], _CRITERION_GUIDE[c["key"]])
             for c in CRITERIA if c["key"] in keys}
    if pros_cons:
        # 장단점은 개조식('배송 지연')으로 새기 쉬워 말투 규칙을 필드에도 박아 둔다.
        props["pros"] = {"type": "array", "items": {"type": "string"},
                         "description": "장점(제품 기준 + 가격 등 모든 측면)을 '~해요'체 "
                                        "짧은 문장으로. 없으면 []."}
        props["cons"] = {"type": "array", "items": {"type": "string"},
                         "description": "단점(모든 측면)을 '~해요'체 짧은 문장으로. 없으면 []."}
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


# 제품 축 요약 스키마 — 질감·향·소리·지속력 + 장단점. 제품 하나(또는 마켓의 제품들)에 붙는다.
PRODUCT_REVIEW_SCHEMA: dict = _review_schema(PRODUCT_KEYS, pros_cons=True)

# 주문 축 요약 스키마 — 고객 응대·배송만. **장단점 칸이 없다**(ADR-0015): 제품 축 요약이 이미
# 장단점 목록을 갖고 있어서, 여기서 또 만들면 한 화면에 서로 다른 두 목록이 서게 된다.
# 주문 축은 두 줄만 담당한다 — 만들지 않은 값은 화면에서 고를 일도 없다.
ORDER_REVIEW_SCHEMA: dict = _review_schema(MARKET_KEYS, pros_cons=False)

EMPTY_CRITERION: dict = {"verdict": None, "minority": None}


def empty_section(keys: Optional[list[str]] = None, *, pros_cons: bool = True) -> dict:
    """요약이 없을 때의 빈 섹션 — 화면이 키 유무를 검사하지 않아도 되게 모양을 맞춰 둔다."""
    out = {k: dict(EMPTY_CRITERION) for k in (keys if keys is not None else CRITERIA_KEYS)}
    return {**out, "pros": [], "cons": []} if pros_cons else out

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

# 다수/소수 두 칸 규칙 — 네 프롬프트 공통(ADR-0014). 대립 평가를 그냥 병렬로 놓는 걸 막는다.
_MAJORITY = """\
[기준 한 칸의 모양 — verdict / minority]
- verdict: 그 기준에서 **다수가 말한 내용** 1문장. 입력 counts 의 pos/neg 중 많은 쪽이 다수다.
- minority: 다수와 **갈리는 평가**가 있으면 '일부는 ~' 으로 1문장. 갈리지 않으면 null.
- 언급이 아예 없는 기준은 verdict·minority **둘 다 null**.
- ⚠️ 대립 평가를 그냥 나란히 놓지 마라. '유분기가 많다는 말과 잘 안 붙는다는 말이 있어요'는
  읽는 사람이 판단할 수 없다 — 다수를 verdict 에, 나머지를 minority 에 갈라 담아야 한다.
- ⚠️ counts 는 어느 쪽이 다수인지 **고르라고** 주는 재료다. 숫자를 문장에 옮기지 마라
  ('7명이 ~' X, '대체로 ~' O)."""

# 화면이 배지·표로 따로 보여주는 것 — 본문에서 빼는 금지 목록(ADR-0014).
_NO_META = """\
[본문에 쓰지 말 것 — 화면이 따로 보여준다]
- **부재 서술 금지.** 언급이 없으면 그냥 null 이다. '지속력 평가는 없어요' / '향 언급은 찾기
  어려워요' 같은 문장을 쓰지 마라. 다른 내용에 붙여 쓰는 것도 금지다
  ('방치했다는 말은 있지만 빨리 죽는다는 말은 없어요' X → 앞부분만 쓴다).
- **메타 설명 금지.** 출처 갭·건수·'인스타에서만 언급돼요'·'두 출처 모두' 같은 말을 쓰지 마라.
  화면이 배지로 보여준다. 너는 후기의 **내용**만 쓴다.
- **출처 이름을 기준 문장에 넣지 마라.** '인스타는 ~하고 디시는 ~' 형태 금지 — 화면에 출처별
  칸이 이미 따로 있어서, 문장이 그걸 반복하면 읽는 사람이 매번 다시 갈라 읽어야 한다.
  (출처 표기는 pros/cons 항목에서만 쓴다.)"""

# 축별 기준 설명 블록 — 프롬프트 셋(출처별·통합·서포터)이 이 두 개를 나눠 쓴다.
# 한 축의 프롬프트에는 **다른 축의 기준이 아예 등장하지 않는다**(ADR-0015): 담을 칸이 없는
# 기준을 설명하면 모델이 그 내용을 남은 칸에 밀어 넣는다.
_PRODUCT_CRITERIA = """\
- texture: 질감(말랑·쫀득·흐름성·꾸덕·손붙음 등).
- scent: 향(냄새).
- sound: 소리(걀걀거림·꾸덕소리·기포음·터짐음).
  ⚠️ 소리는 질감과 별개 축이다 — '걀걀거린다'를 texture 에 넣지 마라.
- longevity: 지속력(제품 수명·굳음·묽어짐·'빨리 죽음').
  ⚠️ 배송과 무관한 제품 속성이다.
  ⚠️ 배송·포장·판매자 응대 이야기는 이 요약의 대상이 아니다(마켓 단위로 따로 요약한다) —
     재료에 섞여 들어와도 어느 칸에도 쓰지 마라."""

# 주문 축(마켓 단위). 재료 필드 하나(shipping_cs)가 두 칸으로 갈리는 곳이라 분기를 설명한다.
# ⚠️ 예전에 여기 있던 '같은 주문의 배송 얘기를 제품 수만큼 부풀려 세지 마라'는 **삭제됐다**.
#    팬아웃 복제는 이제 `_fold_orders` 가 재료 단계에서 접는다(ADR-0015) — 모델이 볼 입력에
#    애초에 중복이 없다. 데이터 문제를 프롬프트로 고쳐 달라고 부탁하지 않는다.
_ORDER_CRITERIA = """\
- cs: 고객 응대(문의 답변·교환·환불·사과·판매자 태도).
- shipping: 배송(발송·도착 속도·포장 상태·파손·누락).
  ⚠️ 입력의 shipping_cs 재료 하나가 cs 와 shipping 두 섹션으로 갈린다. 물류 사실(발송·도착
     속도·포장·파손·누락)은 shipping, 사람의 응대(문의 답변·교환·환불·사과·태도)는 cs 로 보낸다.
     한 문장에 둘이 섞여 있으면 각 섹션에 해당 부분만 나눠 쓴다. 한쪽만 있으면 나머지는 null.
  ⚠️ '슬라임이 빨리 죽었다' 같은 제품 수명은 배송도 응대도 아니다 — 제품 축에서 따로 요약하니
     여기서는 어느 칸에도 쓰지 마라."""

# 축별 대상 문장 + 장단점 규칙. 주문 축은 장단점 칸이 없다(ORDER_REVIEW_SCHEMA 주석 참조).
_PRODUCT_SUBJECT = ("슬라임 한 제품(또는 한 마켓의 여러 제품)에 대한 **제품 평가**를 "
                    "4개 기준(질감/향/소리/지속력) + 장단점으로")
_ORDER_SUBJECT = ("한 마켓의 **주문 경험**(고객 응대·배송)을 2개 기준(고객 응대/배송)으로")
_PRODUCT_PROS = """
- pros/cons: 제품 기준 4개를 포함한 모든 측면(가격 등)의 장점/단점을 각각 짧은 항목으로.
  해당 없으면 빈 배열 []. 근거 없는 장단점 창작 금지."""
_ORDER_PROS = ""

# 제품 라벨 규칙 — 마켓 단위 요약에서만 붙는다. 주문 축은 애초에 마켓 단위라 제품이 무의미하다.
_PRODUCT_LABEL_RULE = """
- 항목에 product 라벨이 있으면(마켓 단위 요약) 제품별로 갈리는 평가는 제품명을 표기해 구분한다
  (예: '[한글과자한줌] 비누향 지적'). 서로 다른 제품의 평가를 하나로 뭉뚱그리지 않는다."""
_ORDER_LABEL_RULE = """
- 이 요약은 마켓 하나에 대한 것이다. 제품명을 문장에 넣지 마라 — 주문 경험은 제품별 사실이
  아니고, 제품명을 달면 읽는 사람이 그 제품만의 문제로 오해한다."""

# 출처별(인스타 또는 디시) 후기 요약 프롬프트. 이 출처 후기만 근거로.
_SECTION_TEMPLATE = """\
너는 {subject} 요약한다. 입력은 '한 출처(플랫폼)'의 실사용 후기다.
입력 by_attr 의 evidence 에 실제로 나온 내용만 근거로 삼는다. 지어내기 금지.
기준별 규칙(각 기준은 {{{{verdict, minority}}}} 두 칸이다):
{criteria}
{{majority}}
{{nometa}}{pros}{label}
- 이 출처의 후기만 본다. 다른 출처와 비교하지 않는다.
(대가·무상 '홍보성' 후기와 판매자 게시물은 이미 분리됐다 — 입력은 실사용분만.)
{{tone}}
"""

# 통합 리뷰 프롬프트 — 두 출처의 '이미 요약된' 결과 + 기준별 카운트를 받아 reconciliation. 평균 금지.
# ⚠️ sentiment_gap 은 **일부러 넣지 않는다**(ADR-0014). 넣었더니 '출처 갭이 큰 편이라' 같은
#    메타 설명이 본문으로 샜다 — 갭은 코드가 계산해 화면이 보여주는 값이지 요약의 소재가 아니다.
_INTEGRATED_TEMPLATE = """\
너는 인스타 후기 요약과 디시 후기 요약을 받아 두 출처를 '통합'한다.
절대 평균내지 말 것 — 두 출처가 '일치하는 점'과 '갈리는 점'을 드러내는 게 목적이다.
대상은 {subject_short} 이고, 기준은 아래 목록이 전부다:
{criteria}
규칙:
- 각 기준에 같은 규칙을 적용한다:
  · 두 출처가 수렴하면 그 합의를 verdict 에 쓰고 minority 는 null.
  · 갈리면 counts 로 다수 쪽을 골라 verdict 에, 반대쪽을 minority 에 쓴다.
  · 한쪽 출처에만 언급이 있으면 그 내용을 그대로 verdict 에 쓴다 —
    **어느 출처였는지는 쓰지 마라**(화면이 출처별 칸으로 이미 보여준다).
  · 두 입력이 모두 null 인 기준은 verdict·minority 둘 다 null.
- 입력 요약에 없는 기준을 새로 지어내지 마라.{pros_integrated}
- 점수를 하나로 섞지 말고 출처별 관점을 유지하라.
{{majority}}
{{nometa}}
{{tone}}
"""
_PRODUCT_PROS_INTEGRATED = """
- pros/cons: 두 출처 '공통' 장/단점과 '한쪽에서만' 나온 장/단점을 구분해 항목화.
  **출처 표기는 여기서만 한다** (예 '[디시] 향이 인공적이에요' / '[공통] 향이 좋아요')."""

# 서포터(홍보성) 후기 전용 요약 — 실사용과 '분리'하되, 실제 언급된 내용을 담백히 요약.
_SUPPORTER_TEMPLATE = """\
아래는 '서포터/무상 제공(협찬)' 인스타 후기다. {subject} 요약한다.
실제 언급된 내용만 쓴다. 지어내기 금지, 미언급은 null/빈배열.
실사용 후기와 합치지 말고 이 버킷만 요약한다.
기준별 규칙(각 기준은 {{{{verdict, minority}}}} 두 칸이다):
{criteria}{supporter_note}
{{majority}}
{{nometa}}{pros}{label}
{{tone}}
"""
_ORDER_SUPPORTER_NOTE = """
  ⚠️ 서포터 발송은 일반 주문과 경로가 달라 배송·응대 경험이 실사용 후기와 다를 수 있다 —
     그래서 버킷을 섞지 않는다."""


def _fill(template: str, **parts: str) -> str:
    """축 조각을 끼운 뒤 공통 블록(다수/소수·메타 금지·말투)을 채운다.

    두 단계인 이유: 축 조각 안에 `{verdict, minority}` 같은 중괄호가 들어 있어서, 한 번에
    format 하면 그게 치환 자리로 잡힌다. 템플릿의 `{{{{...}}}}` 이스케이프도 그래서다.
    """
    return template.format(**parts).format(majority=_MAJORITY, nometa=_NO_META, tone=TONE)


PRODUCT_SECTION_PROMPT = _fill(_SECTION_TEMPLATE, subject=_PRODUCT_SUBJECT,
                               criteria=_PRODUCT_CRITERIA, pros=_PRODUCT_PROS,
                               label=_PRODUCT_LABEL_RULE)
ORDER_SECTION_PROMPT = _fill(_SECTION_TEMPLATE, subject=_ORDER_SUBJECT,
                             criteria=_ORDER_CRITERIA, pros=_ORDER_PROS,
                             label=_ORDER_LABEL_RULE)

PRODUCT_INTEGRATED_PROMPT = _fill(_INTEGRATED_TEMPLATE, subject_short="제품 평가",
                                  criteria=_PRODUCT_CRITERIA,
                                  pros_integrated=_PRODUCT_PROS_INTEGRATED)
ORDER_INTEGRATED_PROMPT = _fill(_INTEGRATED_TEMPLATE, subject_short="한 마켓의 주문 경험",
                                criteria=_ORDER_CRITERIA, pros_integrated="")

PRODUCT_SUPPORTER_PROMPT = _fill(_SUPPORTER_TEMPLATE, subject=_PRODUCT_SUBJECT,
                                 criteria=_PRODUCT_CRITERIA, supporter_note="",
                                 pros=_PRODUCT_PROS, label=_PRODUCT_LABEL_RULE)
ORDER_SUPPORTER_PROMPT = _fill(_SUPPORTER_TEMPLATE, subject=_ORDER_SUBJECT,
                               criteria=_ORDER_CRITERIA, supporter_note=_ORDER_SUPPORTER_NOTE,
                               pros=_ORDER_PROS, label=_ORDER_LABEL_RULE)

# 축 기술자 — 요약 한 벌을 만드는 데 필요한 모든 것을 한 곳에 묶는다. 새 축이 생기면
# 여기 항목 하나가 늘고, 아래 `_sectionize_*` 는 그대로다.
PRODUCT_AXIS: dict = {
    "name": "product", "keys": PRODUCT_KEYS, "fields": PRODUCT_ATTR_FIELDS,
    "schema": PRODUCT_REVIEW_SCHEMA, "pros_cons": True, "fold": False,
    "section": PRODUCT_SECTION_PROMPT, "integrated": PRODUCT_INTEGRATED_PROMPT,
    "supporter": PRODUCT_SUPPORTER_PROMPT,
}
ORDER_AXIS: dict = {
    "name": "order", "keys": MARKET_KEYS, "fields": ORDER_ATTR_FIELDS,
    "schema": ORDER_REVIEW_SCHEMA, "pros_cons": False, "fold": True,
    "section": ORDER_SECTION_PROMPT, "integrated": ORDER_INTEGRATED_PROMPT,
    "supporter": ORDER_SUPPORTER_PROMPT,
}


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


# 감정 축 **수리 사이드카**가 레코드에 실리는 키(ADR-0018). DB 컬럼은 `reviews.attribute_repairs`
# 이고 `pipeline._records_for` 가 이 이름으로 얹는다. `attributes` 안에 예약 네임스페이스를
# 파지 않은 이유: 감사 골드의 안정 키가 `attributes->>'mentioned_product'` 이고 그 계약의 전제가
# "이 JSONB 는 불변"이라, 같은 칸을 건드리기 시작하면 그 전제가 약해진다.
ATTR_REPAIRS_KEY = "_attribute_repairs"


def _source_material(reviews: list[dict], *, tag_products: bool = False,
                     fields: Optional[list[str]] = None) -> dict:
    """한 소스 후기 → 속성별 evidence 재료(LLM 섹션 요약 입력). 근거 없는 속성은 키 자체를 뺀다.

    tag_products=True(마켓 단위 요약): 항목마다 product 라벨을 붙여 서로 다른 제품의 평가가
    요약에서 뭉개지지 않게 한다. 제품 단위 요약에선 라벨이 불필요한 노이즈라 붙이지 않는다.

    `fields` 는 축이 정한다(ADR-0015). 주문 축 재료는 호출부가 `_fold_orders` 로 접어서 넘기므로
    여기서 다시 접지 않는다 — 접는 규칙이 두 곳에 있으면 조용히 갈라진다.

    **수리 사이드카**(`ATTR_REPAIRS_KEY`)가 있으면 그 축을 건너뛴다 — 잘못 배치된 판정
    (실측: `소리` 축에 들어간 `비즈 탈출`)이 **유료 요약을 재생성할 때마다 다시 오염**시키기
    때문이다. 7행이 작아 보여도 비용은 반복적이다.
    ⚠️ **다른 축으로 옮겨 담지 않는다** — 옮기면 없던 판단을 만든다(1급 규칙 위반).
      원본 `attributes` 도 한 바이트도 안 바뀐다(provenance 스냅샷).
    """
    by_attr: dict[str, list] = {}
    for f in (fields if fields is not None else ATTR_FIELDS):
        items = []
        for r in reviews:
            blk = r.get(f)
            if not isinstance(blk, dict):
                continue
            if ((r.get(ATTR_REPAIRS_KEY) or {}).get(f) or {}).get("action") == "drop":
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


# ------------------------------------------------- 부재·메타 서술 차단(코드 게이트, ADR-0014)
# 프롬프트에만 맡기지 않는 이유는 전언(hearsay) 차단과 같다 — 같은 규칙을 적어 둬도 모델은
# 종종 '~는 없어요'로 보류 근거를 본문에 흘린다. 규칙은 프롬프트에 적고, **강제는 코드가** 한다.

# '언급/평가/말/…이 없다' 류. 문장 전체가 이 모양이면 그 문장은 내용이 아니라 보류 근거다.
_ABSENCE_RE = re.compile(
    r"(?:언급|평가|말|얘기|이야기|반응|후기|지적|내용|정보|서술)\s*(?:은|는|이|가|도)?\s*"
    r"(?:거의\s*|따로\s*|딱히\s*|전혀\s*|별다른\s*|특별히\s*)?"
    r"(?:없|안\s*보이|보이지\s*않|나오지\s*않|드러나지\s*않|찾기\s*어렵|확인되지\s*않)"
)

# 화면이 배지·표로 이미 보여주는 메타 — 갭·건수·'한쪽 출처만'·출처 이름.
_META_RE = re.compile(
    r"출처\s*갭|갭이|갭은|갭\s*지표|평균(?:내|을|적)|건수|\d+\s*건|"
    r"두\s*출처|양쪽\s*출처|한쪽\s*출처|출처\s*(?:간|별|마다)|"
    r"인스타|디시|아모스갤|instagram|dcinside"
)

_RETRY_NOTE = """

⚠️ 직전 응답이 아래 규칙을 어겼다. 같은 입력으로 다시 쓰되 이 항목들을 고쳐라
   (내용을 지우라는 게 아니라, 부재/메타 문장은 빼고 실제 후기 내용만 남기라는 뜻이다):
"""


def _sentences(text: str) -> list[str]:
    """마침표 기준 문장 분해. 종결부호가 없으면 통째로 한 문장으로 본다."""
    return [s for s in (p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip())) if s]


def _offence(sentence: str) -> Optional[str]:
    if _ABSENCE_RE.search(sentence):
        return "부재 서술"
    if _META_RE.search(sentence):
        return "메타 설명"
    return None


def _violations(section: dict, keys: Optional[list[str]] = None) -> list[str]:
    """{기준}.{칸} 별 위반 목록. 비어 있으면 통과. 재시도 프롬프트에 그대로 실린다."""
    out = []
    for key in (keys if keys is not None else CRITERIA_KEYS):
        cell = section.get(key)
        if not isinstance(cell, dict):
            continue
        for slot in ("verdict", "minority"):
            text = cell.get(slot)
            if not isinstance(text, str):
                continue
            for s in _sentences(text):
                why = _offence(s)
                if why:
                    out.append(f"{key}.{slot}: {why} — '{s}'")
    return out


def _scrub_section(section: dict, keys: Optional[list[str]] = None,
                   *, pros_cons: bool = True) -> dict:
    """모양 정규화 + 남은 부재·메타 **문장**을 잘라낸다. 재시도 뒤의 fail-closed 그물.

    문장 단위로만 자른다 — 절 단위 수술('~는 있지만 ~는 없어요' 앞부분만 살리기)은 한국어
    종결어미를 다시 지어야 해서 없던 말을 만들 위험이 크다. 대신 그런 문장은 `_violations` 가
    재시도로 먼저 잡고, 그래도 남으면 통째로 버린다 — 틀린 문장보다 빈칸이 낫다.

    verdict 가 비고 minority 만 남으면 minority 를 verdict 로 올린다(내용 보존).
    """
    out = {}
    for key in (keys if keys is not None else CRITERIA_KEYS):
        cell = section.get(key)
        if not isinstance(cell, dict):           # 구 스키마(문자열) 방어
            cell = {"verdict": cell if isinstance(cell, str) else None, "minority": None}
        kept = {}
        for slot in ("verdict", "minority"):
            text = cell.get(slot)
            if not isinstance(text, str) or not text.strip():
                kept[slot] = None
                continue
            clean = [s for s in _sentences(text) if not _offence(s)]
            kept[slot] = " ".join(clean) or None
        if kept["verdict"] is None and kept["minority"] is not None:
            kept = {"verdict": kept["minority"], "minority": None}
        out[key] = kept
    if pros_cons:
        out["pros"] = [p for p in (section.get("pros") or []) if isinstance(p, str)]
        out["cons"] = [c for c in (section.get("cons") or []) if isinstance(c, str)]
    return out


def _run_sectionize(prompt: str, axis: dict, llm_sectionize: Callable[[str, dict], dict]) -> dict:
    """요약 1회 + 위반 시 1회 재시도 + 스크럽. 결정성 정책(파싱 실패 1회 재시도)과 같은 모양이다."""
    schema, keys = axis["schema"], axis["keys"]
    out = llm_sectionize(prompt, schema) or {}
    bad = _violations(out, keys)
    if bad:
        out = llm_sectionize(prompt + _RETRY_NOTE + "\n".join(f"- {b}" for b in bad), schema) or {}
    return _scrub_section(out, keys, pros_cons=axis["pros_cons"])


def _sectionize_source(reviews: list[dict], platform: str, axis: dict,
                       llm_sectionize: Callable[[str, dict], dict],
                       *, tag_products: bool = False,
                       counts: Optional[dict] = None) -> Optional[dict]:
    """소스(인스타/디시) 후기 → 그 축의 기준 칸(+장단점). 후기 없으면 호출 안 함(상위 가드)."""
    payload = {"platform": platform,
               "by_attr": _source_material(reviews, tag_products=tag_products,
                                           fields=axis["fields"]),
               "counts": counts or {}}
    prompt = axis["section"] + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return _run_sectionize(prompt, axis, llm_sectionize)


def _sectionize_supporter(reviews: list[dict], axis: dict,
                          llm_sectionize: Callable[[str, dict], dict],
                          *, tag_products: bool = False,
                          counts: Optional[dict] = None) -> dict:
    """서포터(홍보성) 후기 → 그 축의 기준 칸. 실사용과 분리하되 실내용 요약."""
    payload = {"by_attr": _source_material(reviews, tag_products=tag_products,
                                           fields=axis["fields"]),
               "counts": counts or {}}
    prompt = axis["supporter"] + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return _run_sectionize(prompt, axis, llm_sectionize)


def _sectionize_integrated(ig_sum: dict, dc_sum: dict, counts: dict, axis: dict,
                           llm_sectionize: Callable[[str, dict], dict]) -> dict:
    """두 소스 요약 + 기준별 카운트 → 통합 리뷰(reconciliation). 두 소스 모두 있을 때만 호출.

    입력은 2층(후기) 파생물만 — 1층(공식 스펙)에서 파생된 scent_divergence 는 넣지 않는다.
    스펙↔후기 완전 분리: 향 불일치는 코드 계산 지표로 뷰에 별도 표시되며, 요약 LLM 은
    공식 스펙을 볼 수 없어야 한다(스펙이 요약 문장에 스며드는 것 방지).

    ⚠️ `sentiment_gap` 은 넣지 않는다(ADR-0014). 갭 수치를 주면 모델이 '출처 갭이 큰 편이라'
       하고 메타 설명을 본문에 적는다 — 갭은 화면이 보여줄 값이지 요약의 소재가 아니다.
       대신 기준별 pos/neg 카운트를 준다: 그건 '어느 쪽이 다수인가'를 고르는 데 실제로 쓰인다.
    """
    payload = {"instagram": ig_sum, "dcinside": dc_sum, "counts": counts}
    prompt = axis["integrated"] + "\n\n[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return _run_sectionize(prompt, axis, llm_sectionize)


def _summarize_axis(genuine: list[dict], promo: list[dict], axis: dict,
                    llm_sectionize: Optional[Callable[[str, dict], dict]],
                    *, tag_products: bool) -> tuple[dict, dict, Optional[dict]]:
    """한 축의 요약 한 벌 → (review_summaries, criterion_stats, promo_view).

    축이 `fold=True` 면(주문 축) 재료·집계 모두 조각 단위로 접은 뒤 쓴다 — 팬아웃 복제분이
    제품 수만큼 세어지는 걸 여기 한 곳에서 막는다(ADR-0015).
    """
    rows = _fold_orders(genuine) if axis["fold"] else genuine
    promo_rows = _fold_orders(promo) if axis["fold"] else promo
    stats = criterion_stats(rows, axis["keys"])

    def _with_material(plat: str) -> list[dict]:
        """이 축의 재료가 실제로 있는 행만. 없으면 빈 리스트 → 호출 자체를 건너뛴다.

        축이 갈리면서 '후기는 있는데 이 축 재료는 0건'인 출처가 흔해졌다(제품 얘기만 하고
        배송은 안 쓴 인스타 후기 등). 그대로 부르면 전 칸이 null 인 요약에 돈을 낸다.
        """
        got = [r for r in rows if _platform(r) == plat]
        return got if _source_material(got, fields=axis["fields"]) else []

    ig, dc = _with_material("instagram"), _with_material("dcinside")

    rs: dict = {"instagram": None, "dcinside": None, "integrated": None}
    if llm_sectionize:
        if ig:
            rs["instagram"] = _sectionize_source(ig, "instagram", axis, llm_sectionize,
                                                 tag_products=tag_products,
                                                 counts=_counts_material(stats, "instagram"))
        if dc:
            rs["dcinside"] = _sectionize_source(dc, "dcinside", axis, llm_sectionize,
                                                tag_products=tag_products,
                                                counts=_counts_material(stats, "dcinside"))
        if rs["instagram"] and rs["dcinside"]:      # 두 소스 다 있어야 통합(평균 아님)
            rs["integrated"] = _sectionize_integrated(
                rs["instagram"], rs["dcinside"], _counts_material(stats), axis, llm_sectionize)

    promo_view = None
    if promo_rows:                                  # 서포터(홍보성) 분리 버킷 — 실내용 요약
        promo_view = {"n_promo": len(promo_rows),
                      **empty_section(axis["keys"], pros_cons=axis["pros_cons"])}
        if llm_sectionize and _source_material(promo_rows, fields=axis["fields"]):
            promo_view.update(_sectionize_supporter(
                promo_rows, axis, llm_sectionize, tag_products=tag_products,
                counts=_counts_material(criterion_stats(promo_rows, axis["keys"]))))
    return rs, stats, promo_view


def build_consolidated(product_ref: dict,
                       official_spec: Optional[dict],
                       reviews: list[dict],
                       llm_sectionize: Optional[Callable[[str, dict], dict]] = None) -> dict:
    """제품 하나(또는 product=None 이면 마켓 전체)에 대한 종합 뷰(구조화).

    ⚠️ **제품 축만** 요약한다(ADR-0015) — 질감·향·소리·지속력 + 장단점. 고객 응대·배송은
       주문(마켓) 단위 사실이라 `build_order_view` 가 마켓당 한 벌 따로 만든다. 여기서 같이
       내면 비교글 하나의 배송 불만이 그 글이 언급한 모든 제품에 각각 관측된 것처럼 실린다.
       `criterion_stats` 도 제품 기준만 센다.

    - llm_sectionize 주입 시 review_summaries(인스타/디시/통합)를 제품 기준/장단점으로 산출.
      · 각 기준은 `{verdict, minority}` 두 칸이다(ADR-0014) — 다수 의견과 소수/반대를 구조로
        가른다. 언급이 없으면 둘 다 null(빈칸) — 지어내기도, '없어요' 서술도 금지.
      · 다수/소수 판정 재료(`criterion_stats`)는 **코드가** 센다. 프롬프트엔 pos/neg 카운트만
        넘기고, 갭 수치는 넘기지 않는다(메타 설명이 본문으로 새던 자리).
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
    '서포터 리뷰'로 제품 기준·장단점을 실제 내용 그대로 요약(소수라도 포함). 없으면 promo_view=None.
    """
    market_mode = not product_ref.get("product")
    genuine = [r for r in reviews if not _is_promo(r)]
    promo = [r for r in reviews if _is_promo(r)]

    by_src = per_source_sentiment(genuine)              # headline = 실사용만
    rs, stats, promo_view = _summarize_axis(genuine, promo, PRODUCT_AXIS, llm_sectionize,
                                            tag_products=market_mode)
    official_scent = (official_spec or {}).get("official_scent")
    return {
        "product": product_ref,
        "official_spec": official_spec,                 # 향료/풀조합/종류/질감서술/URL (1층)
        "n_reviews": len(genuine),                      # 실사용 건수(서포터 제외)
        "by_source": by_src,                            # 소스별 정서 분포(net·건수)
        "sentiment_gap": sentiment_gap(by_src),         # 인스타↔디시 갭 (없으면 None)
        # 소스별 호평/지적 속성 순위. **전 속성을 훑는다**(주문 축 포함) — 축 분리 이전부터 그랬고,
        # 여기선 팬아웃을 접지 않아 shipping_cs 가 제품 수만큼 순위에 실린다(ADR-0014 '유보' 그대로).
        # 지금은 화면에 안 나가므로(`/api/page` 미포함) 남겨 두지만, 렌더할 일이 생기면
        # `criterion_stats` 처럼 축을 갈라 접어야 한다.
        "praised": top_points(genuine, "pos"),
        "criticized": top_points(genuine, "neg"),
        "scent_divergence": scent_divergence(official_scent, genuine),
        # 기준별 소스·정서 건수 — 다수 판정의 근거. LLM 무관, 순수 집계. 제품 기준만 센다.
        "criterion_stats": stats,
        # 소스별 제품 기준·장단점 요약 + 통합. llm_sectionize 없으면 전부 None.
        "review_summaries": rs,
        "promo_view": promo_view,
        # 근거 원문 링크 목록 — 실사용/서포터를 버킷째 분리한다(집계를 안 섞듯 링크도 안 섞는다).
        # 조각 식별자로 중복 제거 + 렌더된 URL 로 그룹핑(source_links). 링크 없는 행은 빠진다.
        "sources": {"genuine": source_links.group_evidence_sources(genuine),
                    "promo": source_links.group_evidence_sources(promo)},
    }


def build_order_view(market: str, reviews: list[dict],
                     llm_sectionize: Optional[Callable[[str, dict], dict]] = None) -> dict:
    """마켓 하나의 **주문 경험** 뷰 — 고객 응대·배송(ADR-0015). 제품이 아니라 마켓에 붙는다.

    왜 별건인가: `shipping_cs` 는 후기(주문) 단위 필드다(ADR-0005). 제품 행에 실려 있는 건
    `index_post` 가 팬아웃마다 **복제**했기 때문이지 제품의 속성이라서가 아니다. 제품별로
    요약하면 (a) 한 주문의 배송 사실이 여러 제품에 각각 관측된 것처럼 실리고, (b) 같은 마켓의
    제품 페이지들이 결국 같은 두 문장을 반복한다. 그래서 마켓당 한 벌만 만들고, 제품 페이지는
    그 한 벌을 **'이 마켓 전체 기준'으로 라벨해서** 빌려 쓴다.

    입력은 그 마켓의 후기 전 행이면 된다(제품 연결 보류 행 포함) — 여기서 `_fold_orders` 로
    조각 단위로 접으므로 팬아웃 복제분을 호출부가 미리 걸러낼 필요가 없다.

    `n_orders` 는 접은 뒤의 조각 수다. `n_reviews`(제품 뷰)와 다른 값인 게 정상이고, 화면의
    근거 건수도 이 값을 써야 한다 — 제품 행 수를 쓰면 '배송 후기 27건'처럼 부풀려 말하게 된다.
    """
    genuine = [r for r in reviews if not _is_promo(r)]
    promo = [r for r in reviews if _is_promo(r)]
    rs, stats, promo_view = _summarize_axis(genuine, promo, ORDER_AXIS, llm_sectionize,
                                            tag_products=False)
    folded = _fold_orders(genuine)
    return {
        "market": market,
        "n_orders": len(folded),                        # 접은 뒤 조각 수(제품 행 수가 아니다)
        "by_source": per_source_sentiment(folded),
        "criterion_stats": stats,                       # 주문 기준만
        "review_summaries": rs,
        "promo_view": promo_view,
        "sources": {"genuine": source_links.group_evidence_sources(genuine),
                    "promo": source_links.group_evidence_sources(promo)},
    }


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
    # 스키마로 축을 알아본다 — 축마다 required 키가 다르다(ADR-0015).
    def _fake_sectionize(prompt: str, schema: dict) -> dict:
        tail = prompt.split("[입력]", 1)[-1]
        keys = [k for k in schema["required"] if k in CRITERION_MATERIAL]
        out = {k: {"verdict": f"{k} 다수 의견(mock)", "minority": None}
                  if f'"{CRITERION_MATERIAL[k]}"' in tail else dict(EMPTY_CRITERION)
               for k in keys}
        if "pros" in schema["required"]:
            out.update({"pros": ["말랑함(mock)"], "cons": []})
        return out

    v = build_consolidated({"market": "빈짱", "product": "연유스무디"},
                           {"official_scent": "연유향", "type": ["지글리"]}, demo,
                           llm_sectionize=_fake_sectionize)
    print(json.dumps(v, ensure_ascii=False, indent=2))

    print("\n=== 주문 축(마켓 단위) — 고객 응대·배송 ===")
    print(json.dumps(build_order_view("빈짱", demo, llm_sectionize=_fake_sectionize),
                     ensure_ascii=False, indent=2))
