# -*- coding: utf-8 -*-
"""
index_post 의 relevance_meta 컬럼 적재 오프라인 테스트 — 네트워크·모델 다운로드·KB 파일 읽기 없음.

`index.embed`(BGE-M3), `linking.load_kb`(KB 로드), `linking.link_post`(개체연결) 를 전부
monkeypatch 한다 — `index_post`(index.py:87) 는 `load_kb()` 를 무조건 호출하고
`linking.py:94-95` 는 `settings.kb_demo_path` 를 디스크에서 읽으므로, `link_post` 하나만
막으면 여전히 실제 파일 I/O 가 한 번 남는다(계획 WS2 §5).

가짜 DB 커넥션/커서로 실행된 SQL 파라미터를 기록해, relevance_meta 인자가 있을 때는
INSERT 가 Jsonb 로 감싼 값을 실어 보내고, 없을 때는 NULL(None) 을 실어 보내는지 검증한다.

실행:  python -m eval.test_index_meta   (repo 루트에서)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from slime_rag import index, linking


@dataclass
class _FakeLink:
    """linking.LinkResult 대역 — index_post 가 실제로 읽는 속성만 노출(market/market_confidence/product)."""
    market: Optional[str]
    market_confidence: float
    product: Optional[str]


class _FakeCursor:
    def __init__(self, sink: list):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.sink.append((sql, params))


class _FakeConn:
    """psycopg 연결 대역 — cursor()/commit() 만 필요. index_post 는 conn=own 이 아니므로 close() 안 부름."""
    def __init__(self):
        self.executed: list = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.executed)

    def commit(self):
        self.committed = True


_DOC = {
    "reviews": [
        {"overall": {"summary": "향이 좋아요", "model_sentiment": "pos"}},
    ]
}


def _patch(monkeypatch_targets: dict) -> dict:
    """{obj: {attr: new}} 형태로 패치하고 원본을 반환(복원용)."""
    originals = {}
    for obj, attrs in monkeypatch_targets.items():
        originals[obj] = {}
        for attr, new in attrs.items():
            originals[obj][attr] = getattr(obj, attr)
            setattr(obj, attr, new)
    return originals


def _restore(originals: dict) -> None:
    for obj, attrs in originals.items():
        for attr, old in attrs.items():
            setattr(obj, attr, old)


def _run_index_post(relevance_meta):
    fake_conn = _FakeConn()
    originals = _patch({
        index: {"embed": lambda texts: [[0.0] * 4 for _ in texts]},
        linking: {
            "load_kb": lambda: object(),           # index_post 가 무조건 호출 — 더미로 흡수
            "link_post": lambda doc, *, kb, aliases=None: [
                _FakeLink(market="빈짱", market_confidence=0.9, product="한줌")
            ],
        },
    })
    try:
        n = index.index_post(_DOC, source="amos", post_id="p1",
                              relevance_meta=relevance_meta, conn=fake_conn)
    finally:
        _restore(originals)
    return n, fake_conn


def test_relevance_meta_carried_when_provided():
    """relevance_meta 인자가 있으면 INSERT 파라미터의 마지막 값이 그 dict 를 감싼 Jsonb 여야."""
    from psycopg.types.json import Jsonb

    meta = {
        "axis": "none", "topic_score": 0.87, "M": 0, "Q": 0, "E": 1,
        "e_rule": 1, "e_probe": 0.6, "e_bucket": 2, "bias_hold": False,
        "rank": 0, "near_boundary": False,
        "target": {"market": "빈짱", "slime": "한줌"}, "target_scope": "product",
        "tau": 0.45, "rank_score": None,
    }
    n, conn = _run_index_post(meta)
    assert n == 1, f"적재 행 수 기대 1, 실제 {n}"
    assert len(conn.executed) == 1, "INSERT 1회 기대"
    sql, params = conn.executed[0]
    assert "relevance_meta" in sql, "INSERT 컬럼 목록에 relevance_meta 없음"
    last = params[-1]
    assert isinstance(last, Jsonb), f"relevance_meta 파라미터가 Jsonb 가 아님: {type(last)}"
    assert last.obj == meta, "Jsonb 가 감싼 값이 원본 verdict dict 와 다름"
    print("✓ relevance_meta 제공 시 Jsonb 로 적재 OK")


def test_relevance_meta_null_when_absent():
    """relevance_meta 미전달(기본 None) 이면 INSERT 파라미터 마지막 값이 None(NULL)."""
    n, conn = _run_index_post(None)
    assert n == 1
    sql, params = conn.executed[0]
    assert params[-1] is None, f"relevance_meta 미전달 시 None 기대, 실제 {params[-1]!r}"
    print("✓ relevance_meta 미전달 시 NULL(None) OK")


if __name__ == "__main__":
    test_relevance_meta_carried_when_provided()
    test_relevance_meta_null_when_absent()
    print("\n모든 오프라인 테스트 통과 ✅")
