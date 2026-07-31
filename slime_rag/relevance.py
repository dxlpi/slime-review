# -*- coding: utf-8 -*-
"""
관련성 필터 (Layer 2) — RawReview 가 '우리가 찾는 타깃 제품에 관한 것'인지 자동 판정.

원칙(계획 `.omc/plans/relevance-aware-collection.md`, D1–D9):
- **쿼리 조건부**(D2): 절대적 "후기냐"가 아니라 "우리 타깃(마켓+슬라임)에 관한 것이냐".
  랜박(random box) 잡담도 타깃 제품을 언급하면 KEEP, 무관하면 DROP.
- **임베딩 기반, 신규 API 비용 0**(D3): `index.embed` 의 BGE-M3(로컬, fp16, 1024-dim)를 재사용.
  LLM 미사용. 청크 단위 최대 코사인으로 한 줄 신호가 전체글에 희석되지 않게.
- **두(세) 축**:
    - Axis 1 (topic, 양쪽 소스): 청크 최대 코사인 ≥ τ_topic → 온토픽.
    - Axis 2 (kind, 주로 디시): 종류 프로토타입 centroid 최근접. review/question=KEEP, resale/chitchat=DROP.
    - Axis 0 (domain, 인스타 name-collision 폴백): 슬라임/비슬라임 centroid. 기본 OFF — 도메인 인식
      앵커('… 슬라임')로 충분. 검증에서 leak 시 conf['domain_gate']='centroid' 로 활성.
- **불변식**(D6): Layer 1 미적용. 홍보/서포터 판단 금지(bias.py 전담). kind 는 4종만.
  소스 편향(부정 디시/긍정 인스타)은 1급 기능 — 관련성 필터가 절대 건드리지 않는다.

플랫폼 차이는 코드가 아니라 데이터 설정(`RELEVANCE_CONF`)으로. 신규 소스 = 설정 한 줄.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import numpy as np

from .config import ROOT
from .sources import RawReview
from .sources.base import is_low_quality
from . import linking

log = logging.getLogger("relevance")

# ---------------------------------------------------------------- 설정 (데이터, 코드 아님)
# τ_topic 는 골드셋 보정(evals/calibrate_relevance.py) 산출값으로 갱신한다. 아래는 보정 전
# 잠정 기본값 — 게이트 로직/카운트/랜박 테스트는 이 값으로 동작하지만, precision/recall
# 하드게이트(AC4)는 보정 후에만 유효. margin = 경계(near_boundary) 폭(보수적 KEEP 마킹).
RELEVANCE_CONF: dict[str, dict] = {
    "dcinside":  {"tau_topic": 0.45, "kind_axis": True,  "domain_gate": False, "margin": 0.05, "types": ("post", "comment")},
    "instagram": {"tau_topic": 0.45, "kind_axis": False, "domain_gate": True,  "margin": 0.05, "types": ("post",)},
}

# 안전 상한 배수(D9): examined == K*limit 도달 시 조기 종료(무료 컴퓨트 ≠ 무료 스크래핑).
RELEVANCE_K = 3

# 보정된 τ 오버라이드(evals/calibrate_relevance.py --write 산출). 하드코딩 근거=보정 리포트.
# 파일이 있으면 소스별 tau_topic 을 갱신한다(없으면 위 잠정 기본값 유지).
_TAU_PATH = ROOT / "evals" / "gold" / "relevance_tau.json"
if _TAU_PATH.exists():
    try:
        _tau = json.loads(_TAU_PATH.read_text(encoding="utf-8")).get("tau_topic", {})
        for _plat, _val in _tau.items():
            if _plat in RELEVANCE_CONF and isinstance(_val, (int, float)):
                RELEVANCE_CONF[_plat]["tau_topic"] = float(_val)
    except Exception as _e:                          # 보정 파일이 깨져도 잠정 기본값으로 동작
        logging.getLogger("relevance").warning("τ 보정 로드 실패(%s): %s", _TAU_PATH, _e)

# Axis 2 종류: KEEP 계열 vs DROP 계열.
_KEEP_KINDS = ("review", "question")
_DROP_KINDS = ("resale", "chitchat")

_GOLD_PATH = ROOT / "evals" / "gold" / "relevance_gold.json"


@dataclass
class RelevanceVerdict:
    keep: bool
    axis: str                 # 'topic' | 'kind' | 'domain' | 'none'  (DROP 사유 축, KEEP 이면 'none')
    topic_score: float        # 타깃 앵커에 대한 청크 최대 코사인
    kind: str = ""            # 'review'|'question'|'resale'|'chitchat'|'' (kind_axis 미적용 시 '')
    near_boundary: bool = False
    reason: str = ""


# ---------------------------------------------------------------- Step 0: 앵커 + 청커 (순수 함수)
def build_anchor(target: dict, *, domain: bool) -> str:
    """
    수집 타깃 → 코사인 비교용 앵커 텍스트.
    target = {"market": str|None, "slime": str}. 마켓 있으면 "{market} {slime}", 없으면 "{slime}".
    약칭은 linking.load_product_aliases() 로 해당 마켓 스코프에서 정규 제품명으로 치환.
    domain=True(인스타)면 슬라임 도메인 접미('… 슬라임') 부착 → name-collision 방어(§2 D4):
      일상어 제품명(예: '사과몽땅')이 음식 글을 끌어와도 앵커가 슬라임 공간이라 코사인 낮음.
    """
    slime = (target.get("slime") or "").strip()
    market = (target.get("market") or "").strip()
    if market and slime:                       # 약칭 정규화(마켓 스코프)
        scope = linking.load_product_aliases().get(market, {})
        slime = scope.get(slime, slime)
    base = f"{market} {slime}".strip() if market else slime
    if domain and base:
        base = f"{base} 슬라임"
    return " ".join(base.split())


def chunk(text: str) -> list[str]:
    """
    본문 → 문장/줄 청크. 전체글 임베딩은 한 줄 신호를 희석하므로 청크 최대 코사인을 쓴다(D2).
    빈/초단·노이즈 청크는 is_low_quality(sources/base) 로 제거. 남는 게 없으면 원문 전체 1청크.
    """
    if not text:
        return []
    out: list[str] = []
    for piece in re.split(r"[\n.!?。…·]+", text):
        piece = piece.strip()
        if piece and not is_low_quality(piece, min_len=3):
            out.append(piece)
    if not out:                                # 전부 걸러지면 원문 보존(신호 유실 방지)
        t = text.strip()
        if t:
            out.append(t)
    return out


# ---------------------------------------------------------------- Step 1: 임베딩 공유 + 코사인
def _embed(texts: list[str]) -> list[np.ndarray]:
    """index.embed(BGE-M3 지연 로드 싱글턴) 재사용 — 새 모델 로드 추가 금지(Step 1)."""
    from .index import embed
    return embed(texts)


def _normalize(mat: np.ndarray) -> np.ndarray:
    return mat / (np.linalg.norm(mat, axis=-1, keepdims=True) + 1e-12)


def _max_cosine(chunk_vecs: np.ndarray, anchor_vec: np.ndarray) -> float:
    """청크 벡터들과 앵커의 최대 코사인(정규화 후 dot)."""
    if len(chunk_vecs) == 0:
        return 0.0
    a = anchor_vec / (np.linalg.norm(anchor_vec) + 1e-12)
    sims = _normalize(np.asarray(chunk_vecs, dtype=float)) @ a
    return float(np.max(sims))


# ---------------------------------------------------------------- Step 2: 프로토타입 로더 (Axis 2 / Axis 0)
_prototypes: dict[str, np.ndarray] | None = None
_domain_prototypes: dict[str, np.ndarray] | None = None


def _load_gold() -> list[dict]:
    if not _GOLD_PATH.exists():
        return []
    try:
        data = json.loads(_GOLD_PATH.read_text(encoding="utf-8"))
    except Exception as e:                      # 깨진 골드셋에 수집이 죽지 않도록
        log.warning("relevance 골드셋 로드 실패(%s): %s", _GOLD_PATH, e)
        return []
    return data.get("items", []) if isinstance(data, dict) else (data or [])


def load_prototypes() -> dict[str, np.ndarray]:
    """
    Axis 2 종류 centroid — 골드셋 예시를 kind 별로 임베딩해 평균 벡터. 지연 로드 + 캐시(1회).
    골드셋 부재/비어있으면 {} (kind 축은 no-op → 온토픽 항목을 review 로 간주해 KEEP).
    """
    global _prototypes
    if _prototypes is not None:
        return _prototypes
    by_kind: dict[str, list[str]] = {}
    for it in _load_gold():
        kind = (it.get("label") or {}).get("kind")
        txt = it.get("text")
        if kind and txt:
            by_kind.setdefault(kind, []).append(txt)
    proto: dict[str, np.ndarray] = {}
    if by_kind:
        for kind, texts in by_kind.items():
            vecs = _normalize(np.asarray(_embed(texts), dtype=float))
            proto[kind] = vecs.mean(axis=0)
    else:
        log.info("relevance: 종류 프로토타입 없음(골드셋 미시드) → kind 축 no-op")
    _prototypes = proto
    return proto


def load_domain_prototypes() -> dict[str, np.ndarray]:
    """
    Axis 0 슬라임/비슬라임 centroid — 골드셋 label.keep(+슬라임 여부) 기반. 인스타 name-collision
    폴백 전용(기본 OFF). keep=True → slime, keep=False & label.why 에 'name-collision' 태그 → not_slime.
    부재 시 {} → centroid 모드 요청돼도 우아하게 skip(앵커 접미로 폴백).
    """
    global _domain_prototypes
    if _domain_prototypes is not None:
        return _domain_prototypes
    slime, not_slime = [], []
    for it in _load_gold():
        label = it.get("label") or {}
        txt = it.get("text")
        if not txt:
            continue
        if label.get("domain") == "not_slime" or (label.get("why") or "").find("name-collision") >= 0:
            not_slime.append(txt)
        elif label.get("keep"):
            slime.append(txt)
    proto: dict[str, np.ndarray] = {}
    if slime and not_slime:
        proto["slime"] = _normalize(np.asarray(_embed(slime), dtype=float)).mean(axis=0)
        proto["not_slime"] = _normalize(np.asarray(_embed(not_slime), dtype=float)).mean(axis=0)
    _domain_prototypes = proto
    return proto


def _nearest_kind(vec: np.ndarray, proto: dict[str, np.ndarray]) -> str:
    v = vec / (np.linalg.norm(vec) + 1e-12)
    best, best_sim = "", -1.0
    for kind, cen in proto.items():
        sim = float(v @ (cen / (np.linalg.norm(cen) + 1e-12)))
        if sim > best_sim:
            best, best_sim = kind, sim
    return best


# ---------------------------------------------------------------- Step 3: classify (스텁 대체)
def _verdict(chunk_vecs: np.ndarray, anchor_vec: np.ndarray, conf: dict) -> RelevanceVerdict:
    """미리 임베딩된 청크 벡터 + 앵커 벡터로 판정(배치 경로 공유)."""
    tau = conf.get("tau_topic", 0.45)
    margin = conf.get("margin", 0.05)
    domain_gate = conf.get("domain_gate", False)

    if len(chunk_vecs) == 0:
        return RelevanceVerdict(False, "topic", 0.0, "", False, "빈 텍스트")

    # Axis 1 — 온토픽(양쪽 소스). 도메인 인식 앵커라 비슬라임 글은 대개 여기서 걸린다.
    topic_score = _max_cosine(chunk_vecs, anchor_vec)
    near = abs(topic_score - tau) < margin
    if topic_score < tau:
        return RelevanceVerdict(False, "topic", topic_score, "", near, f"topic<{tau:.2f}")

    # Axis 0 — 슬라임 도메인 centroid(인스타 폴백, 기본 OFF).
    if domain_gate == "centroid":
        dom = load_domain_prototypes()
        if dom:
            mean_vec = _normalize(np.asarray(chunk_vecs, dtype=float)).mean(axis=0)
            if _nearest_kind(mean_vec, dom) == "not_slime":
                return RelevanceVerdict(False, "domain", topic_score, "", near, "비슬라임(domain centroid)")

    # Axis 2 — 종류(주로 디시). resale/chitchat DROP. 프로토타입 없으면 no-op(review 로 KEEP).
    kind = ""
    if conf.get("kind_axis"):
        proto = load_prototypes()
        if proto:
            mean_vec = _normalize(np.asarray(chunk_vecs, dtype=float)).mean(axis=0)
            kind = _nearest_kind(mean_vec, proto)
            if kind in _DROP_KINDS:
                return RelevanceVerdict(False, "kind", topic_score, kind, near, f"kind={kind}")

    return RelevanceVerdict(True, "none", topic_score, kind or "review", near, "keep")


def classify_batch(reviews: list[RawReview], target: dict, conf: dict) -> list[RelevanceVerdict]:
    """
    후보 다수를 한 번의 embed 호출로 판정(D8 배치 — BGE-M3 encode 호출 amortize).
    앵커 1개 + 모든 후보의 청크를 flat 하게 모아 임베딩 후 후보별로 되돌린다.
    """
    if not reviews:
        return []
    domain = bool(conf.get("domain_gate", False))     # True/'centroid' 모두 도메인 인식 앵커
    anchor = build_anchor(target, domain=domain)
    per_chunks = [chunk(r.text) for r in reviews]

    flat: list[str] = [anchor if anchor else " "]     # 앵커 빈 문자열 방어
    spans: list[tuple[int, int]] = []
    for chunks in per_chunks:
        start = len(flat)
        flat.extend(chunks)
        spans.append((start, len(flat)))

    vecs = _embed(flat)
    anchor_vec = np.asarray(vecs[0], dtype=float)
    out: list[RelevanceVerdict] = []
    for (s, e) in spans:
        out.append(_verdict(np.asarray(vecs[s:e], dtype=float), anchor_vec, conf))
    return out


def classify(review: RawReview, target: dict, conf: dict) -> RelevanceVerdict:
    """단건 판정 — classify_batch 의 1건 래퍼(코드 경로 단일화)."""
    return classify_batch([review], target, conf)[0]


# ---------------------------------------------------------------- 셀프테스트
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tgt = {"market": "봄", "slime": "푸냥이"}          # 약칭 → 허니푸냥이
    print("앵커(dcinside):", build_anchor(tgt, domain=False))
    print("앵커(instagram):", build_anchor(tgt, domain=True))
    print("청크:", chunk("봄 허니푸냥이 후기! 말랑하고 향 진짜 좋아요.\n재구매각"))
    samples = [
        RawReview(text="봄 허니푸냥이 말랑하고 향 진짜 좋아요 재구매각", url="u1", platform="instagram"),
        RawReview(text="오늘 점심 뭐 먹지 배고프다 사과 먹음", url="u2", platform="instagram"),
    ]
    for r, v in zip(samples, classify_batch(samples, tgt, RELEVANCE_CONF["instagram"])):
        print(f"  keep={v.keep} axis={v.axis} score={v.topic_score:.3f} kind={v.kind} :: {r.text[:20]}")
