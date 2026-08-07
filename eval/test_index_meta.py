# -*- coding: utf-8 -*-
"""
index_post 의 relevance_meta·source_ref JSONB 적재 오프라인 테스트 — 네트워크·모델 다운로드·KB 파일 읽기 없음.

`index.embed`(BGE-M3), `linking.load_kb`(KB 로드), `linking.link_post`(개체연결) 를 전부
monkeypatch 한다 — `index_post`(index.py:87) 는 `load_kb()` 를 무조건 호출하고
`linking.py:94-95` 는 `settings.kb_demo_path` 를 디스크에서 읽으므로, `link_post` 하나만
막으면 여전히 실제 파일 I/O 가 한 번 남는다(계획 WS2 §5).

가짜 DB 커넥션/커서로 실행된 SQL 파라미터를 기록해, JSONB 인자가 있을 때는 INSERT 가
Jsonb 로 감싼 값을 실어 보내고, 없을 때는 NULL(None) 을 실어 보내는지 검증한다.
파라미터는 **위치가 아니라 컬럼 이름으로** 찾는다 — INSERT 에 컬럼이 하나 추가되는 것만으로
`params[-1]` 단언이 엉뚱한 값을 보게 되기 때문(source_ref 추가 때 실제로 그렇게 깨졌다).

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
    """psycopg 커서 대역. `rowcount` 는 index_post 가 **실제 적재 행 수**를 세는 데 쓴다 —
    ON CONFLICT DO NOTHING 이 충돌분을 조용히 스킵하므로 len(texts) 는 더 이상 정답이 아니다.
    기본 1(정상 적재)로 두고, 중복 스킵 시나리오는 rowcount=0 으로 주입한다."""
    def __init__(self, sink: list, rowcount: int = 1):
        self.sink = sink
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.sink.append((sql, params))


class _FakeConn:
    """psycopg 연결 대역 — cursor()/commit() 만 필요. index_post 는 conn=own 이 아니므로 close() 안 부름."""
    def __init__(self, rowcount: int = 1):
        self.executed: list = []
        self.committed = False
        self.rowcount = rowcount        # 커서가 보고할 적재 행 수(0 = 중복 스킵)

    def cursor(self):
        return _FakeCursor(self.executed, self.rowcount)

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


def _run_index_post(relevance_meta=None, source_ref=None):
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
                              relevance_meta=relevance_meta, source_ref=source_ref,
                              conn=fake_conn)
    finally:
        _restore(originals)
    return n, fake_conn


def _param(sql: str, params, column: str):
    """INSERT 의 컬럼 목록에서 `column` 위치를 찾아 그 자리 파라미터를 돌려준다.

    위치 하드코딩(`params[-1]`)을 쓰지 않는 이유: 컬럼을 하나 덧붙이는 것만으로 단언이
    조용히 옆자리 값을 보게 된다. 이름으로 찾으면 컬럼이 늘어도 계약이 유지된다.
    """
    cols = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert column in cols, f"INSERT 컬럼 목록에 {column} 없음: {cols}"
    assert len(cols) == len(params), f"컬럼 {len(cols)}개 vs 파라미터 {len(params)}개"
    return params[cols.index(column)]


def test_relevance_meta_carried_when_provided():
    """relevance_meta 인자가 있으면 그 컬럼 자리에 dict 를 감싼 Jsonb 가 실린다."""
    from psycopg.types.json import Jsonb

    meta = {
        "axis": "none", "topic_score": 0.87, "M": 0, "Q": 0, "E": 1,
        "e_rule": 1, "e_probe": 0.6, "e_bucket": 2, "bias_hold": False,
        "rank": 0, "near_boundary": False,
        "target": {"market": "빈짱", "slime": "한줌"}, "target_scope": "product",
        "tau": 0.45, "rank_score": None,
    }
    n, conn = _run_index_post(relevance_meta=meta)
    assert n == 1, f"적재 행 수 기대 1, 실제 {n}"
    assert len(conn.executed) == 1, "INSERT 1회 기대"
    sql, params = conn.executed[0]
    val = _param(sql, params, "relevance_meta")
    assert isinstance(val, Jsonb), f"relevance_meta 파라미터가 Jsonb 가 아님: {type(val)}"
    assert val.obj == meta, "Jsonb 가 감싼 값이 원본 verdict dict 와 다름"
    print("✓ relevance_meta 제공 시 Jsonb 로 적재 OK")


def test_relevance_meta_null_when_absent():
    """relevance_meta 미전달(기본 None) 이면 그 컬럼 자리가 None(NULL)."""
    n, conn = _run_index_post()
    assert n == 1
    sql, params = conn.executed[0]
    assert _param(sql, params, "relevance_meta") is None, "미전달인데 NULL 이 아님"
    print("✓ relevance_meta 미전달 시 NULL(None) OK")


def test_source_ref_carried_when_provided():
    """source_ref 인자가 있으면 그 컬럼 자리에 식별자 dict 를 감싼 Jsonb 가 실린다."""
    from psycopg.types.json import Jsonb

    ref = {"platform": "dcinside",
           "url": "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=201513",
           "thread_no": "201513", "comment_no": "1088714"}
    n, conn = _run_index_post(source_ref=ref)
    assert n == 1
    sql, params = conn.executed[0]
    val = _param(sql, params, "source_ref")
    assert isinstance(val, Jsonb), f"source_ref 파라미터가 Jsonb 가 아님: {type(val)}"
    assert val.obj == ref, "Jsonb 가 감싼 값이 원본 식별자와 다름"
    print("✓ source_ref 제공 시 Jsonb 로 적재 OK")


def test_source_ref_null_when_absent():
    """source_ref 미전달이면 NULL — 링크 없는 행은 조용히 링크 없음으로 남는다."""
    n, conn = _run_index_post()
    assert n == 1
    sql, params = conn.executed[0]
    assert _param(sql, params, "source_ref") is None, "미전달인데 NULL 이 아님"
    print("✓ source_ref 미전달 시 NULL(None) OK")


def test_source_ref_replicated_to_fanout_rows():
    """조각 단위 속성이라 제품별 팬아웃 행 **전체**에 같은 식별자가 복제된다.

    복제를 빼면 두 번째 제품 행부터 링크가 사라진다. 대신 읽는 쪽에서 중복 제거가
    필요해지는데, 그건 `source_links.evidence_group_key` 의 책임이다.
    """
    from psycopg.types.json import Jsonb
    ref = {"platform": "instagram", "url": "https://www.instagram.com/p/ABC123/"}
    doc = {"reviews": [{"overall": {"summary": "A", "model_sentiment": "pos"}},
                       {"overall": {"summary": "B", "model_sentiment": "neg"}}]}
    fake_conn = _FakeConn()
    originals = _patch({
        index: {"embed": lambda texts: [[0.0] * 4 for _ in texts]},
        linking: {"load_kb": lambda: object(),
                  "link_post": lambda d, *, kb, aliases=None: [
                      _FakeLink("빈짱", 0.9, "한줌"), _FakeLink("빈짱", 0.9, "빠삭귤")]},
    })
    try:
        n = index.index_post(doc, source="instagram", post_id="s1",
                             source_ref=ref, conn=fake_conn)
    finally:
        _restore(originals)
    assert n == 2, f"팬아웃 행 2건 기대, 실제 {n}"
    vals = [_param(sql, p, "source_ref") for sql, p in fake_conn.executed]
    assert len(vals) == 2 and all(isinstance(v, Jsonb) and v.obj == ref for v in vals), \
        "팬아웃 행 전체에 식별자가 복제되지 않았다"
    print("✓ source_ref 가 제품별 팬아웃 행 전체에 복제 OK")


def test_insert_carries_on_conflict_clause():
    """INSERT 에 `ON CONFLICT (source, post_id, product) DO NOTHING` 이 반드시 붙는다.

    멱등성을 여기서 잃으면 수집 배치를 다시 돌릴 때 같은 글이 통째로 재색인된다.
    실측(2026-08-07): 인스타 80행 중 28행이 그렇게 생긴 중복이었고, 같은 글이 런마다
    **다른 감성**으로 추출돼 독립된 후기 두 건처럼 `criterion_stats` 집계에 들어갔다.
    ⚠️ `DO UPDATE` 로 바꾸는 것도 회귀다 — 재수집이 기존 판정을 조용히 덮어쓴다.
    """
    _n, conn = _run_index_post()
    sql, _params = conn.executed[0]
    flat = " ".join(sql.split())
    assert "ON CONFLICT (source, post_id, product) DO NOTHING" in flat, \
        f"ON CONFLICT DO NOTHING 절이 없다: {flat}"
    print("✓ INSERT 에 ON CONFLICT DO NOTHING 절 부착 OK")


def test_returns_actual_inserted_count_not_row_count():
    """반환값은 **실제 적재된** 행 수다 — 충돌로 스킵된 행은 세지 않는다.

    예전엔 `len(texts)` 를 그대로 돌려줬다. ON CONFLICT 가 붙은 지금 그러면 한 행도
    안 들어간 재실행이 '2건 색인'이라고 보고한다 — 조용한 과대보고이고, 파이프라인
    카운터(`ingest_*` 의 반환 dict)가 그 값을 그대로 싣는다.
    """
    doc = {"reviews": [{"overall": {"summary": "A", "model_sentiment": "pos"}},
                       {"overall": {"summary": "B", "model_sentiment": "neg"}}]}
    fake_conn = _FakeConn(rowcount=0)          # 전 행이 기존 행과 충돌 → 스킵
    originals = _patch({
        index: {"embed": lambda texts: [[0.0] * 4 for _ in texts]},
        linking: {"load_kb": lambda: object(),
                  "link_post": lambda d, *, kb, aliases=None: [
                      _FakeLink("빈짱", 0.9, "한줌"), _FakeLink("빈짱", 0.9, "빠삭귤")]},
    })
    try:
        n = index.index_post(doc, source="instagram", post_id="s1", conn=fake_conn)
    finally:
        _restore(originals)
    assert len(fake_conn.executed) == 2, "INSERT 는 두 번 시도돼야 한다(스킵은 DB가 판단)"
    assert n == 0, f"중복 스킵이면 0 을 반환해야 한다, 실제 {n}"
    print("✓ 중복 스킵 시 실제 적재 수(0) 반환 OK")


if __name__ == "__main__":
    test_relevance_meta_carried_when_provided()
    test_relevance_meta_null_when_absent()
    test_source_ref_carried_when_provided()
    test_source_ref_null_when_absent()
    test_source_ref_replicated_to_fanout_rows()
    test_insert_carries_on_conflict_clause()
    test_returns_actual_inserted_count_not_row_count()
    print("\n모든 오프라인 테스트 통과 ✅")
