# -*- coding: utf-8 -*-
"""
관련성 게이트 오프라인 테스트 — 신규 API 미호출(로컬 BGE-M3만, 무비용).

계획 `.omc/plans/relevance-aware-collection.md` 의 인수 기준 검증:
- AC1  관련성 카운트: 비관련 섞인 고정 입력에서 collect(limit=N) → N개의 KEEP(또는 상한 이하), 전부 KEEP.
- AC2  자동 연결: classify 가 NotImplementedError 없이 동작 + dcinside/apify 수집 경로가 호출.
- AC3  쿼리 조건부(랜박): 동일 랜박 텍스트가 타깃 포함이면 KEEP, 무관 타깃이면 DROP.
- AC3b 인스타 name-collision: 도메인 centroid 게이트가 비슬라임(음식) 글을 DROP(axis=domain).
        ※ 여기선 게이트 '메커니즘'을 실텍스트 프로토타입 주입으로 검증. 실서비스 임계·프로토타입은
          골드셋 보정(US-006/Step 4·5)에서 확정 — 그전까지 domain_gate 기본은 앵커 접미(비활성).
- AC5  상한/책임 수집: examined 가 K*limit 에 도달하면 조기 종료 + shortfall WARN.
- AC7  관측성: 모든 DROP 이 axis/score/kind/url 과 함께, 경계 KEEP 은 별도 마킹 로그.

실행:  python -m eval.test_relevance_gate   (repo 루트에서)
"""
from __future__ import annotations

import logging

import numpy as np

from slime_rag.sources import ApifyHashtagSource
from slime_rag.sources.base import RawReview, RelevanceGate
import slime_rag.relevance as R


# ---------------------------------------------------------------- 로그 캡처 유틸
class _Capture(logging.Handler):
    """logger 'sources' 레코드를 모아 관측성(AC7)·shortfall(AC5) 로그를 검증한다."""
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]   # getMessage 가 이미 args 를 보간


def _capture_sources_log() -> _Capture:
    cap = _Capture()
    lg = logging.getLogger("sources")
    lg.setLevel(logging.INFO)
    lg.addHandler(cap)
    return cap


# ---------------------------------------------------------------- 고정 입력 소스(네트워크 경계 주입)
_KEEP_CAPTIONS = [
    "봄 허니푸냥이 말랑하고 향 진짜 좋아요 재구매각",
    "허니푸냥이 촉감 쫀득하고 향도 좋음 완전 만족",
    "랜박 뜯었는데 허니푸냥이 나왔다 향 좋네 대만족",
    "허니푸냥이 클리어 슬라임 반짝이 예쁘고 소리도 좋아요",
]
_DROP_CAPTIONS = [
    "오늘 점심 뭐 먹지 배고프다 치킨 먹고 싶다",
    "속보 오늘 전국 폭염 주의보 발효 기온 35도",
    "랜박 뜯었는데 레몬커드쉘도넛 나왔다 이건 별로였음",
]


def _apify_with_items(captions: list[str]) -> ApifyHashtagSource:
    """_run(네트워크 경계)을 고정 캡션 아이템으로 대체한 소스."""
    src = ApifyHashtagSource(token="dummy", hashtags=["허니푸냥이"])
    items = [{"caption": c, "shortCode": f"C{i:03d}", "url": f"https://insta/p/C{i:03d}/",
              "hashtags": ["봄슬라임"]} for i, c in enumerate(captions)]
    src._run = lambda hashtags: items
    return src


# ---------------------------------------------------------------- AC1
def test_ac1_counts_only_relevant():
    """비관련이 섞인 고정 입력에서 limit 이 '관련 항목'으로만 채워지고, 전부 KEEP 이어야."""
    # KEEP/DROP 교차 배치 — DROP 이 한도를 소진하지 않음을 확인.
    interleaved = [_KEEP_CAPTIONS[0], _DROP_CAPTIONS[0], _KEEP_CAPTIONS[1],
                   _DROP_CAPTIONS[1], _KEEP_CAPTIONS[2], _DROP_CAPTIONS[2], _KEEP_CAPTIONS[3]]
    src = _apify_with_items(interleaved)
    tgt = {"market": "봄", "slime": "허니푸냥이"}
    out = list(src.collect(keywords=["허니푸냥이"], limit=3, target=tgt))
    assert len(out) == 3, f"관련 3건 기대, 실제 {len(out)}"
    # 반환된 모든 항목은 KEEP(meta.relevance 부착) + DROP 캡션은 하나도 없어야.
    for r in out:
        assert r.meta.get("relevance"), "KEEP 항목에 relevance 메타 없음"
        assert r.meta["relevance"]["axis"] == "none"
    texts = {r.text for r in out}
    assert not (texts & set(_DROP_CAPTIONS)), "비관련 캡션이 한도에 섞여 들어옴"
    print("✓ AC1 관련성 카운트: limit 이 관련 항목으로만 채워짐(전부 KEEP) OK")


def test_ac1_relevant_scarcity_returns_fewer():
    """관련 항목이 limit 보다 적으면 그만큼만(그 이하) 반환."""
    src = _apify_with_items([_KEEP_CAPTIONS[0]] + _DROP_CAPTIONS)   # KEEP 1 + DROP 3
    out = list(src.collect(keywords=["허니푸냥이"], limit=5,
                           target={"market": "봄", "slime": "허니푸냥이"}))
    assert len(out) == 1, f"관련 1건 기대, 실제 {len(out)}"
    print("✓ AC1 관련 부족 시 한도 이하 반환 OK")


# ---------------------------------------------------------------- AC2
def test_ac2_classify_wired_and_callable():
    """classify 가 NotImplementedError 없이 동작 + 수집 경로(dcinside/apify)가 게이트를 경유."""
    v = R.classify(RawReview(text="허니푸냥이 향 좋아요 말랑", url="u", platform="dcinside"),
                   {"market": "봄", "slime": "허니푸냥이"}, R.RELEVANCE_CONF["dcinside"])
    assert isinstance(v, R.RelevanceVerdict) and v.keep
    # 두 수집 경로가 RelevanceGate(→ classify_batch)를 사용하는지 소스 레벨 확인.
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "slime_rag" / "sources"
    for fn in ("dcinside.py", "apify.py"):
        body = (root / fn).read_text(encoding="utf-8")
        assert "RelevanceGate" in body and "gate.filter" in body, f"{fn} 가 게이트를 호출하지 않음"
    print("✓ AC2 classify 자동연결(수집 경로가 게이트 경유) OK")


# ---------------------------------------------------------------- AC3 (쿼리 조건부 / 랜박)
def test_ac3_query_conditional_ranbox():
    """동일 랜박 텍스트: 타깃 제품 포함이면 KEEP, 무관 타깃이면 DROP."""
    ranbox = RawReview(text="랜박 뜯었는데 허니푸냥이 나왔다 향 좋네 대만족",
                       url="u", platform="dcinside")
    conf = R.RELEVANCE_CONF["dcinside"]
    keep = R.classify(ranbox, {"market": "봄", "slime": "허니푸냥이"}, conf)
    drop = R.classify(ranbox, {"market": None, "slime": "레몬커드쉘도넛"}, conf)
    assert keep.keep, f"타깃 포함 랜박은 KEEP 이어야 (score={keep.topic_score:.3f})"
    assert not drop.keep and drop.axis == "topic", \
        f"무관 타깃 랜박은 DROP(topic) 이어야 (score={drop.topic_score:.3f})"
    print(f"✓ AC3 쿼리 조건부 랜박 KEEP({keep.topic_score:.2f})/DROP({drop.topic_score:.2f}) OK")


# ---------------------------------------------------------------- AC3b (name-collision, 메커니즘)
def test_ac3b_domain_gate_drops_nonslime():
    """도메인 centroid 게이트: 비슬라임(음식) 글을 axis=domain 으로 DROP, 슬라임 글은 KEEP.
    실텍스트 프로토타입 주입으로 게이트 메커니즘을 검증(임계/프로토타입 확정은 골드셋 보정)."""
    slime_ex = ["말랑하고 쫀득한 슬라임 촉감 향 좋아요", "슬라임 지글지글 크런치 소리 좋음",
                "클리어 슬라임 반짝이 비즈 예뻐요"]
    food_ex = ["오늘 점심 과일 사서 먹었어요 새콤달콤 맛있다", "사과 배 포도 디저트 카페 다녀옴",
               "치킨 먹고 커피 마심 배부르다"]
    saved = R._domain_prototypes
    try:
        R._domain_prototypes = {
            "slime": R._normalize(np.asarray(R._embed(slime_ex), dtype=float)).mean(axis=0),
            "not_slime": R._normalize(np.asarray(R._embed(food_ex), dtype=float)).mean(axis=0),
        }
        conf = dict(R.RELEVANCE_CONF["instagram"]); conf["domain_gate"] = "centroid"
        tgt = {"market": None, "slime": "사과몽땅"}      # 일상어(음식)와 충돌하는 제품명
        slime_post = R.classify(RawReview(text="사과몽땅 슬라임 촉감 쫀득 향 좋아요 후기",
                                          url="u", platform="instagram"), tgt, conf)
        food_post = R.classify(RawReview(text="사과몽땅 사서 먹었어요 새콤달콤 과일 맛있다 점심",
                                         url="u", platform="instagram"), tgt, conf)
        assert slime_post.keep, "슬라임 글이 잘못 DROP 됨"
        assert not food_post.keep and food_post.axis == "domain", \
            f"비슬라임 음식 글이 DROP(domain) 되지 않음 (keep={food_post.keep}, axis={food_post.axis})"
        print("✓ AC3b 도메인 게이트가 비슬라임 글 DROP(axis=domain) OK")
    finally:
        R._domain_prototypes = saved


# ---------------------------------------------------------------- AC5 (상한 / 책임 수집)
def test_ac5_examined_cap_and_shortfall():
    """examined 가 K*limit 에 도달하면 조기 종료하고 shortfall 을 WARN 로깅."""
    cap = _capture_sources_log()
    # 전부 비관련 → KEEP 0. limit=2, K=3 → cap=6 에서 조기 종료(20건 중 6건만 examine).
    drops = [RawReview(text=f"오늘 날씨 뉴스 폭염 잡담 {i} 점심 치킨", url=f"u{i}", platform="dcinside")
             for i in range(20)]
    gate = RelevanceGate("dcinside", {"market": None, "slime": "허니푸냥이"}, [], limit=2)
    assert gate.cap == 6, f"cap=K*limit=6 기대, 실제 {gate.cap}"
    out = list(gate.filter(drops))
    gate.finish()
    assert out == [], "비관련만 있는데 KEEP 이 나옴"
    assert gate.examined == 6, f"examined 가 상한 6 에서 멈춰야, 실제 {gate.examined}"
    assert gate.should_stop(), "상한 도달 후 should_stop 이 True 여야"
    msgs = cap.messages()
    assert any("shortfall" in m and "상한" in m for m in msgs), \
        f"shortfall WARN 로그 없음: {msgs}"
    print("✓ AC5 examined 상한 조기종료 + shortfall WARN OK")


def test_ac5_max_pages_preserved():
    """기존 페이지 상한(max_pages) 파라미터가 collect 시그니처에 유지."""
    import inspect
    from slime_rag.sources.dcinside import DCInsideSource
    sig = inspect.signature(DCInsideSource.collect)
    assert "max_pages" in sig.parameters, "max_pages 상한이 사라짐"
    assert "target" in sig.parameters, "target 게이트 인자가 없음"
    print("✓ AC5 max_pages 상한 유지 + target 인자 추가 OK")


# ---------------------------------------------------------------- AC7 (관측성)
def test_ac7_drop_log_schema():
    """모든 DROP 이 axis/score/kind/url 과 함께 로깅된다."""
    cap = _capture_sources_log()
    reviews = [RawReview(text="오늘 점심 뭐 먹지 배고프다 치킨", url="drop-url-1", platform="dcinside")]
    gate = RelevanceGate("dcinside", {"market": None, "slime": "허니푸냥이"}, [], limit=5)
    list(gate.filter(reviews))
    gate.finish()
    drop_logs = [m for m in cap.messages() if "relevance drop" in m]
    assert drop_logs, "DROP 로그가 없음"
    m = drop_logs[0]
    assert "axis=" in m and "score=" in m and "kind=" in m and "url=drop-url-1" in m, \
        f"DROP 로그 스키마 불완전: {m}"
    assert any("relevance summary" in x for x in cap.messages()), "요약 카운터 로그 없음"
    print("✓ AC7 DROP 로그 스키마(axis/score/kind/url) + 요약 OK")


def test_ac7_near_boundary_marked():
    """경계 근처(near_boundary) KEEP 은 별도 마킹 로그가 남는다."""
    cap = _capture_sources_log()
    # τ 근처 점수를 만들기 위해 conf 를 조작: tau 를 관측 점수에 맞춰 near_boundary 유도.
    review = RawReview(text="봄 허니푸냥이 말랑하고 향 진짜 좋아요 재구매각", url="near-url", platform="instagram")
    v = R.classify(review, {"market": "봄", "slime": "허니푸냥이"}, R.RELEVANCE_CONF["instagram"])
    conf = dict(R.RELEVANCE_CONF["instagram"])
    conf["tau_topic"] = round(v.topic_score - 0.01, 3)   # 점수가 τ 바로 위 → near_boundary
    R.RELEVANCE_CONF["_test_near"] = conf
    try:
        gate = RelevanceGate("_test_near", {"market": "봄", "slime": "허니푸냥이"}, [], limit=5)
        out = list(gate.filter([review]))
        assert len(out) == 1 and out[0].meta["relevance"]["near_boundary"], "경계 KEEP 마킹 안 됨"
        assert any("near-boundary" in m for m in cap.messages()), "경계 KEEP 로그 없음"
        print("✓ AC7 near_boundary 경계 KEEP 마킹 로그 OK")
    finally:
        R.RELEVANCE_CONF.pop("_test_near", None)


if __name__ == "__main__":
    test_ac1_counts_only_relevant()
    test_ac1_relevant_scarcity_returns_fewer()
    test_ac2_classify_wired_and_callable()
    test_ac3_query_conditional_ranbox()
    test_ac3b_domain_gate_drops_nonslime()
    test_ac5_examined_cap_and_shortfall()
    test_ac5_max_pages_preserved()
    test_ac7_drop_log_schema()
    test_ac7_near_boundary_marked()
    print("\n관련성 게이트 오프라인 테스트 통과 ✅")
