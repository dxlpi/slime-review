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
    - Axis 2 (M/Q/E, 주로 디시): **독립 이진 3축**. 배타 4분류 `kind` 를 대체한다
      (계획 `.omc/plans/kind-axis-resolution.md` §1-B — 배타 taxonomy 가 centroid 붕괴의 원인이었다).
        M = 갤 메타/드라마/뉴스 위젯  → **유일한 DROP 사유**
        Q = 질문·조언요청 화행        → 관측용(드롭 아님)
        E = 1인칭 실사용 평가          → 추출 순위의 주 신호(드롭 아님)
      후보 집합 = 규칙 OR 프로브의 **합집합**, 정렬 = E 신뢰도 버킷, 절단 = 예산 N(§C-3).
    - Axis 0 (domain, 인스타 name-collision 폴백): 슬라임/비슬라임 centroid. 기본 OFF — 도메인 인식
      앵커('… 슬라임')로 충분. 검증에서 leak 시 conf['domain_gate']='centroid' 로 활성.
- **불변식**(D6): Layer 1 미적용. 홍보/서포터 판단 금지(bias.py 전담).
  소스 편향(부정 디시/긍정 인스타)은 1급 기능 — 관련성 필터가 절대 건드리지 않는다.
  **부정 감성 항목은 E 와 무관하게 후보에서 빼지 않는다**(AC8 하드게이트, `bias_hold`).

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
    "dcinside":  {"tau_topic": 0.45, "mqe_axis": True,  "domain_gate": False, "margin": 0.05, "types": ("post", "comment")},
    "instagram": {"tau_topic": 0.45, "mqe_axis": False, "domain_gate": True,  "margin": 0.05, "types": ("post",)},
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

# E 신뢰도 버킷(§C-3 정렬 키). 규칙·프로브가 둘 다 양성이면 상위, 하나면 중간, 둘 다 음성이면 하위.
E_BUCKET_BOTH, E_BUCKET_ONE, E_BUCKET_NONE = 2, 1, 0

_GOLD_PATH = ROOT / "evals" / "gold" / "relevance_gold.json"
_PROBE_PATH = ROOT / "evals" / "gold" / "e_probe.npz"


@dataclass
class RelevanceVerdict:
    keep: bool
    axis: str                 # 'topic' | 'meta' | 'domain' | 'none'  (DROP 사유 축, KEEP 이면 'none')
    topic_score: float        # 타깃 앵커에 대한 청크 최대 코사인
    # --- 절대 축 3개 (mqe_axis 미적용 소스면 전부 0) ---
    M: int = 0                # 갤 메타/드라마/뉴스 위젯 — 유일한 DROP 사유
    Q: int = 0                # 질문 화행 — 관측용
    E: int = 0                # 1인칭 실사용 평가 (규칙 ∪ 프로브 합집합)
    e_rule: int = 0           # 규칙 캐스케이드 단독 판정
    e_probe: float = 0.0      # 로지스틱 프로브 확률(0.0 = 프로브 없음)
    e_bucket: int = 0         # 정렬 키 — 2:둘 다 / 1:하나만 / 0:둘 다 음성
    bias_hold: bool = False   # AC8: 부정 감성이라 E 와 무관하게 후보 유지
    rank: int = -1            # 배치 내 순위(예산 절단 기준). 게이트가 채운다.
    unprocessed: bool = False # 예산 초과 — **드롭이 아니라 미처리**(AC10, 침묵 절단 금지)
    near_boundary: bool = False
    reason: str = ""

    @property
    def axes(self) -> str:
        """로그용 한 줄 표기 — 'M0Q1E1'."""
        return f"M{self.M}Q{self.Q}E{self.E}"


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
_e_probe: tuple[np.ndarray, float] | None = None      # (w, b) — 오프라인 학습본
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


def load_e_probe() -> tuple[np.ndarray, float] | None:
    """
    E 축 로지스틱 프로브(w, b) — `evals/train_e_probe.py` 가 오프라인 학습해 `.npz` 로 커밋한 것.
    **런타임 학습 없음**(sklearn 불필요). BGE-M3 는 이미 로드돼 있으므로 추가 모델 의존성 0.
    파일이 없으면 None → 규칙 캐스케이드 단독으로 동작한다(합집합이 규칙만 남는 형태, 무해).
    """
    global _e_probe
    if _e_probe is not None:
        return _e_probe
    if not _PROBE_PATH.exists():
        log.info("relevance: E 프로브 없음(%s) → 규칙 단독", _PROBE_PATH)
        return None
    try:
        z = np.load(_PROBE_PATH)
        _e_probe = (np.asarray(z["w"], dtype=float), float(z["b"]))
    except Exception as e:                      # 깨진 가중치에 수집이 죽지 않도록
        log.warning("E 프로브 로드 실패(%s): %s", _PROBE_PATH, e)
        return None
    return _e_probe


def e_probe_prob(mean_vec: np.ndarray) -> float:
    """정규화된 평균 청크 벡터 → E 확률. 프로브 없으면 0.0."""
    wb = load_e_probe()
    if wb is None:
        return 0.0
    w, b = wb
    return float(1.0 / (1.0 + np.exp(-(mean_vec @ w + b))))


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


def _nearest_centroid(vec: np.ndarray, proto: dict[str, np.ndarray]) -> str:
    """벡터와 가장 가까운 centroid 이름. 이제 **도메인 축(Axis 0) 전용**이다."""
    v = vec / (np.linalg.norm(vec) + 1e-12)
    best, best_sim = "", -1.0
    for kind, cen in proto.items():
        sim = float(v @ (cen / (np.linalg.norm(cen) + 1e-12)))
        if sim > best_sim:
            best, best_sim = kind, sim
    return best


# ---------------------------------------------------------------- Axis 2: M/Q/E 신호
def mqe_signals(text: str, mean_vec: np.ndarray, conf: dict) -> dict:
    """
    절대 축 3개 + 합집합 후보 판정. `_verdict` 와 보정 스크립트(`evals/calibrate_relevance.py`)가
    **같은 함수**를 쓴다 — 두 곳에 같은 규칙을 적어 두면 반드시 갈라지기 때문이다.

    candidate = (규칙 E) OR (프로브 E) OR (부정 감성).
    마지막 항이 AC8 편향 보존 하드게이트다: 부정 후기 1건의 손실이 헛호출 10회보다 비싸다(D6).
    """
    from . import relevance_rules as rules
    ax = rules.axes(text)
    prob = 0.0 if ax["M"] else e_probe_prob(mean_vec)
    e_rule = ax["E"]
    e_probe_pos = int(prob >= conf.get("tau_e_probe", 0.5))
    bucket = (E_BUCKET_BOTH if (e_rule and e_probe_pos)
              else E_BUCKET_ONE if (e_rule or e_probe_pos) else E_BUCKET_NONE)
    hold = rules.is_negative(text)
    return {"M": ax["M"], "Q": ax["Q"], "E": int(bucket > 0),
            "e_rule": e_rule, "e_probe": round(prob, 4), "e_bucket": bucket,
            "bias_hold": hold, "candidate": bool(bucket) or hold}


# ---------------------------------------------------------------- Step 3: classify (스텁 대체)
def _verdict(chunk_vecs: np.ndarray, anchor_vec: np.ndarray, conf: dict,
             text: str = "") -> RelevanceVerdict:
    """미리 임베딩된 청크 벡터 + 앵커 벡터로 판정(배치 경로 공유). text 는 규칙 캐스케이드용."""
    tau = conf.get("tau_topic", 0.45)
    margin = conf.get("margin", 0.05)
    domain_gate = conf.get("domain_gate", False)

    if len(chunk_vecs) == 0:
        return RelevanceVerdict(False, "topic", 0.0, reason="빈 텍스트")

    # Axis 1 — 온토픽(양쪽 소스). 도메인 인식 앵커라 비슬라임 글은 대개 여기서 걸린다.
    topic_score = _max_cosine(chunk_vecs, anchor_vec)
    near = abs(topic_score - tau) < margin
    if topic_score < tau:
        return RelevanceVerdict(False, "topic", topic_score,
                                near_boundary=near, reason=f"topic<{tau:.2f}")

    # Axis 0 — 슬라임 도메인 centroid(인스타 폴백, 기본 OFF).
    if domain_gate == "centroid":
        dom = load_domain_prototypes()
        if dom:
            mean_vec = _normalize(np.asarray(chunk_vecs, dtype=float)).mean(axis=0)
            if _nearest_centroid(mean_vec, dom) == "not_slime":
                return RelevanceVerdict(False, "domain", topic_score,
                                        near_boundary=near, reason="비슬라임(domain centroid)")

    # Axis 2 — M/Q/E 이진 3축(주로 디시). **M 만 DROP 사유**다.
    # Q(질문)는 드롭도 순위 조정도 하지 않는 **순수 관측 축**이다(정렬 키에 안 들어간다).
    # 질문글이 뒤로 밀리는 건 Q 때문이 아니라 그런 글이 대개 E=0 이라서다 — 배타 4분류에서 질문을 KEEP/DROP 으로 가르던
    # 설계가 §1-B 의 근본 문제였다.
    if not conf.get("mqe_axis"):
        return RelevanceVerdict(True, "none", topic_score, near_boundary=near, reason="keep")

    sig = mqe_signals(text, _normalize(np.asarray(chunk_vecs, dtype=float)).mean(axis=0), conf)
    if sig["M"]:
        return RelevanceVerdict(False, "meta", topic_score, M=1, Q=sig["Q"], E=0,
                                near_boundary=near, reason="meta/noise")
    keep = sig["candidate"]
    return RelevanceVerdict(
        keep, "none" if keep else "e_union", topic_score,
        M=0, Q=sig["Q"], E=sig["E"], e_rule=sig["e_rule"], e_probe=sig["e_probe"],
        e_bucket=sig["e_bucket"], bias_hold=sig["bias_hold"], near_boundary=near,
        reason="keep" if keep else "E 합집합 음성")


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
    for r, (s, e) in zip(reviews, spans):
        out.append(_verdict(np.asarray(vecs[s:e], dtype=float), anchor_vec, conf, r.text))
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
        print(f"  keep={v.keep} axis={v.axis} score={v.topic_score:.3f} {v.axes} :: {r.text[:20]}")
