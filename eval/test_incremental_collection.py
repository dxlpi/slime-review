# -*- coding: utf-8 -*-
"""
증분 수집 계약 오프라인 테스트 — 네트워크·LLM·DB 무접촉.

세 가지를 지킨다:
  · Phase 0 — 디시 댓글 `post_id` 가 **런에 의존하지 않는다**(`comment_no` 기반).
    이게 깨지면 `UNIQUE(source, post_id, product)` 가 댓글에 대해 아무것도 막지 못하고,
    그 위에 얹은 기보유 컷은 **조용히 아무것도 안 걸러진다**.
  · Phase 1 — 이미 색인된 조각은 **LLM 을 쓰는 첫 단계 앞에서** 잘린다.
    해시태그 경로의 첫 LLM 은 추출이 아니라 홍보성 캐스케이드라 컷이 `bias.partition` 앞이어야 한다.
  · Phase 2 — 워터마크(`min_thread_no`)가 **상세 HTTP 요청 앞에서** 목록을 자른다.

실행:  OPENAI_API_KEY="" python -m eval.test_incremental_collection   (repo 루트에서)
"""
from __future__ import annotations

from slime_rag import index, pipeline
from slime_rag import sources as sources_pkg
from slime_rag.sources import DCInsideSource
from slime_rag.sources.base import RawReview


# ---------------------------------------------------------------- 공용 대역
def _patch(targets: dict) -> dict:
    originals = {}
    for obj, attrs in targets.items():
        originals[obj] = {}
        for attr, new in attrs.items():
            originals[obj][attr] = getattr(obj, attr)
            setattr(obj, attr, new)
    return originals


def _restore(originals: dict) -> None:
    for obj, attrs in originals.items():
        for attr, old in attrs.items():
            setattr(obj, attr, old)


class _NoCallLLM:
    """LLM 대역 — 불리면 즉시 실패한다. '호출 0' 판정을 사후 카운트가 아니라 **즉시** 드러낸다."""
    calls = 0

    def complete(self, *a, **kw):
        type(self).calls += 1
        raise AssertionError("기보유 조각만 있는 입력인데 LLM 이 호출됐다")


class _FakeDB:
    """`pipeline.connect()` 대역. `UNIQUE(source, post_id, product)` 를 집합으로 흉내낸다.

    `reviews` 조회(기보유 컷)와 색인 양쪽이 **같은 집합**을 보므로, 컷이 실제로 무엇을
    걸러내는지가 테스트에서 그대로 재현된다.
    """

    def __init__(self, rows: set | None = None):
        self.rows = set(rows or ())          # {(source, post_id, product)}
        self.queries: list[tuple] = []

    # -- 커넥션 프로토콜 --
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    rowcount = 0                             # join_specs 가 읽는다

    def commit(self):
        pass

    def close(self):
        pass

    def cursor(self):
        return self                          # 커서도 같은 대역 — execute/컨텍스트만 쓰인다

    def execute(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params))
        return self

    def fetchall(self):
        sql, params = self.queries[-1]
        if "SELECT DISTINCT post_id FROM reviews" in sql:
            source, wanted = params
            return [(pid,) for (s, pid, _p) in self.rows
                    if s == source and pid in set(wanted)]
        if "max((source_ref->>'thread_no')::bigint)" in sql:
            return [(self.max_thread_no,)]
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    max_thread_no = None


def _fake_index_post(db: _FakeDB, products=("한줌",)):
    """`index.index_post` 대역 — DB 제약(중복 스킵)을 그대로 흉내내고 적재 행 수를 돌려준다."""
    def _fn(doc, *, source, post_id=None, conn=None, **kw):
        n = 0
        for p in products:
            key = (source, post_id, p)
            if key in db.rows:
                continue                     # ON CONFLICT DO NOTHING
            db.rows.add(key)
            n += 1
        return n
    return _fn


def _dc_raw(kind: str, thread: str, cno: str | None = None, text: str = "이거 향 진짜 좋아요"):
    """`DCInsideSource._candidates_for` 가 **실제로 싣는 meta** 만 담는다.

    ⚠️ 글에는 `thread_no` 를 넣지 않는다 — 수집기가 안 싣기 때문이다(`_parse_post` 는
      nick·ip·조회·댓글수·추천만 준다). 편의로 넣으면 파이프라인이 글에서도 그 키를 읽어도
      된다고 착각하게 되고, 그 착각이 실제로 통과한 적이 있다(2026-08-07 스레드 문맥 결함).
      글의 스레드 번호는 URL 에서 나온다 — `extract.thread_key` 가 그 규칙의 단일 출처다.
    """
    url = f"https://gall.dcinside.com/mgallery/board/view/?id=amos&no={thread}"
    if kind == "post":
        return RawReview(text=text, url=url, platform="dcinside", raw_title="제목",
                         meta={"keyword": "뭉치", "gallery": "amos", "type": "post",
                               "toxic": False, "nick": "ㅇㅇ", "views": "조회 42"})
    return RawReview(text=text, url=f"{url}#cmt", platform="dcinside",
                     meta={"keyword": "뭉치", "gallery": "amos", "type": "comment",
                           "parent_no": thread, "parent_title": "제목",
                           "thread_no": thread, "comment_no": cno, "ordinal": 0,
                           "nick": "ㅇㅇ", "toxic": False})


def _ig_post(shortcode: str, url: str):
    return RawReview(text=f"#머머슬라임 #{shortcode} 신제품이에요", url=url, platform="instagram",
                     meta={"shortcode": shortcode, "owner_username": "murmur"})


class _FakeSource:
    """수집기 대역 — 주입한 raws 를 그대로 돌려준다. `last_gate` 는 관측성 카운터 자리."""
    last_gate = None

    def __init__(self, raws):
        self._raws = list(raws)

    def __call__(self, *a, **kw):
        return self

    def collect(self, *a, **kw):
        return list(self._raws)


def _run_ingest_dcinside(raws, db: _FakeDB, llm=None, **kwargs) -> dict:
    """`ingest_dcinside` 를 수집·추출·LLM·DB 없이 돌린다. 반환은 실제 counts."""
    llm = llm or _NoCallLLM()

    originals = _patch({
        sources_pkg: {"DCInsideSource": _FakeSource(raws)},
        pipeline: {"connect": db, "LLM": lambda: llm},
        index: {"index_post": _fake_index_post(db)},
    })
    # extract 는 함수 안에서 지연 import 되므로 모듈 객체를 직접 패치한다.
    from slime_rag import extract
    ex_orig = _patch({extract: {"extract_collected": lambda rs, *a, **kw: [(r, {}) for r in rs]}})
    try:
        return pipeline.ingest_dcinside(slime="뭉치", market="예찬", dry_run=False, **kwargs)
    finally:
        _restore(ex_orig)
        _restore(originals)


# ---------------------------------------------------------------- Phase 0
def test_comment_post_id_is_run_independent():
    """댓글 `post_id` 는 `comment_no` 로 만들고 enumerate 위치를 담지 않는다.

    옛 형식은 `…#cmt:{parent_no}:{i}` 였고 `i` 가 그 런의 리스트 위치였다 — 수집 결과가 한 건만
    달라져도 이후 모든 댓글의 키가 밀려서 같은 댓글이 새 행으로 들어갔다.
    """
    raw = _dc_raw("comment", "202244", cno="1091684")
    pid = pipeline.dc_post_id(raw)
    assert pid == "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=202244#cmt:1091684", pid
    # 마이그레이션 SQL 이 만드는 값과 **같은 형식**이어야 한다(schema.sql 의 UPDATE 문).
    assert pid.startswith("https://") and pid.count("#cmt") == 1
    # 옛 형식 판별식(`:숫자:숫자$`)에 걸리면 안 된다 — 걸리면 마이그레이션이 자기 산출물을 다시 문다.
    import re
    assert not re.search(r":[0-9]+:[0-9]+$", pid), f"옛 형식 패턴에 매칭됨: {pid}"
    print("✓ 댓글 post_id 가 comment_no 기반(런 무관) OK")


def test_post_post_id_unchanged():
    """글은 URL 그대로 — 원래 안정적이었으므로 바꾸지 않는다(하위호환)."""
    raw = _dc_raw("post", "202244")
    assert pipeline.dc_post_id(raw) == raw.url
    print("✓ 글 post_id 는 URL 그대로 OK")


def test_ordinal_is_never_used_as_fallback():
    """`comment_no` 가 없으면 **None** 이다 — `ordinal` 로 때우지 않는다.

    수집기가 그 값을 일부러 다른 이름으로 싣는다("앵커 조립에 절대 안 쓰이게 한다").
    폴백으로 쓰면 방금 없앤 런 의존성이 그대로 재발한다.
    """
    raw = _dc_raw("comment", "202244", cno=None)
    assert raw.meta["ordinal"] == 0, "테스트 전제: ordinal 은 실려 있다"
    assert pipeline.dc_post_id(raw) is None
    print("✓ comment_no 없으면 None(ordinal 폴백 없음) OK")


def test_missing_comment_no_is_counted_not_silently_dropped():
    """id 없는 댓글은 색인되지 않되 **세어서 드러난다**(계획 R2 — 무음 스킵 금지)."""
    db = _FakeDB()
    raws = [_dc_raw("comment", "1", cno="11"), _dc_raw("comment", "2", cno=None)]
    counts = _run_ingest_dcinside(raws, db, llm=object())
    assert counts["skipped_no_comment_no"] == 1, counts
    assert counts["indexed_rows"] == 1, counts
    print("✓ comment_no 결손이 카운트로 노출 OK")


def test_second_run_indexes_nothing():
    """같은 입력을 두 번 넣으면 두 번째 `indexed_rows == 0`(AC-0).

    Phase 0 이전엔 댓글 키가 런마다 달라져 두 번째 런이 **전부 새 행**이 됐다.
    """
    db = _FakeDB()
    raws = [_dc_raw("post", "100"), _dc_raw("comment", "100", cno="900"),
            _dc_raw("comment", "100", cno="901")]
    first = _run_ingest_dcinside(raws, db, llm=object())
    assert first["indexed_rows"] == 3, first
    # 두 번째 런은 수집 순서가 흔들려도(리스트 뒤집기) 같은 키가 나와야 한다.
    second = _run_ingest_dcinside(list(reversed(raws)), db)
    assert second["indexed_rows"] == 0, second
    print("✓ 재실행 indexed_rows == 0 (멱등) OK")


# ---------------------------------------------------------------- Phase 1
def test_existing_post_ids_skips_roundtrip_when_empty():
    """빈 목록이면 DB 왕복 자체를 하지 않는다 — `ANY(ARRAY[])` 를 던지지 않는다."""
    db = _FakeDB({("amos", "p1", "한줌")})
    assert index.existing_post_ids(db, "amos", []) == set()
    assert db.queries == [], f"빈 입력인데 쿼리가 나갔다: {db.queries}"
    assert index.existing_post_ids(db, "amos", ["p1", "p2"]) == {"p1"}
    print("✓ existing_post_ids 빈 입력 무왕복 · 교집합만 반환 OK")


def test_dcinside_cut_runs_before_extraction():
    """기보유 조각만 넣으면 `extract_collected` 가 **빈 목록**을 받는다(AC-1).

    컷이 추출 뒤에 있으면 이미 LLM 값을 치른 뒤라, '스킵했다'는 카운트가 절감을 뜻하지 않는다.
    """
    raws = [_dc_raw("post", "100"), _dc_raw("comment", "100", cno="900")]
    db = _FakeDB({("amos", pipeline.dc_post_id(r), "한줌") for r in raws})
    seen_by_extract: list = []
    from slime_rag import extract

    def _spy(rs, *a, **kw):
        seen_by_extract.append(list(rs))
        return [(r, {}) for r in rs]

    originals = _patch({
        sources_pkg: {"DCInsideSource": _FakeSource(raws)},
        pipeline: {"connect": db, "LLM": lambda: _NoCallLLM()},
        index: {"index_post": _fake_index_post(db)},
    })
    ex_orig = _patch({extract: {"extract_collected": _spy}})
    try:
        counts = pipeline.ingest_dcinside(slime="뭉치", market="예찬", dry_run=False)
    finally:
        _restore(ex_orig)
        _restore(originals)
    # 전량 기보유면 추출기는 아예 안 불린다(빈 입력에 LLM 객체를 만들 이유도 없다).
    # 불리더라도 빈 배치여야 한다 — 조각이 하나라도 들어가면 그 순간 유료다.
    assert not any(seen_by_extract), f"추출기가 기보유 조각을 받았다: {seen_by_extract}"
    assert counts["skipped_seen"] == 2, counts
    assert counts["indexed_rows"] == 0, counts
    assert _NoCallLLM.calls == 0, "LLM 이 호출됐다"
    print("✓ 디시 컷이 추출 앞 · LLM 호출 0 OK")


def test_dcinside_dedup_saving_counts_batches_not_pieces():
    """절감 보고가 **배치 수**다 — 디시는 조각당 1콜이 아니라 스레드 배치당 1콜이라서.

    한 스레드에서 조각 하나만 새로 들어오면 그 스레드 배치는 여전히 돌아야 하므로 절감은 0이다.
    거른 조각 수를 그대로 보고하면 아끼지도 않은 호출을 아꼈다고 말하게 된다.
    """
    raws = [_dc_raw("post", "100"), _dc_raw("comment", "100", cno="900"),
            _dc_raw("comment", "100", cno="901")]
    db = _FakeDB({("amos", pipeline.dc_post_id(raws[1]), "한줌")})   # 댓글 하나만 기보유
    counts = _run_ingest_dcinside(raws, db, llm=object())
    assert counts["skipped_seen"] == 1, counts
    assert counts["llm_calls_saved_by_dedup"] == 0, \
        f"같은 스레드가 남았으니 배치는 그대로 돈다: {counts}"
    print("✓ 절감 카운트가 조각 수가 아니라 배치 수 OK")


def test_dedup_counter_is_named_apart_from_promo_gate_saving():
    """`llm_calls_saved`(홍보 게이트 단락분)와 `llm_calls_saved_by_dedup` 은 **다른 키**다.

    합치면 어느 절감인지 사후에 못 가른다 — 아끼는 대상이 다르다(판정 단락 vs 조각 미열람).
    """
    import inspect
    src = inspect.getsource(pipeline.ingest_hashtag)
    assert '"llm_calls_saved":' in src and '"llm_calls_saved_by_dedup":' in src, src
    print("✓ 절감 카운터 두 축이 별도 키 OK")


def test_hashtag_cut_runs_before_promo_cascade():
    """해시태그 경로의 컷은 **`bias.partition` 앞**이다(AC-1).

    이 경로에서 먼저 도는 LLM 은 추출이 아니라 홍보성 캐스케이드다 — `partition` 에 넘긴
    `promo_detector` 가 게이트 통과분마다 verdict 를 부른다. 컷이 추출 직전에 있으면 그 값을
    이미 치른 뒤라 '기보유 조각만 넣으면 호출 0' 이 성립하지 않는다.
    """
    from slime_rag import bias, extract, llm_ops

    raws = [RawReview(text="이거 홍보 아니고 협찬 받아서 써봤어요", url="https://instagram.com/p/AAA/",
                      platform="instagram", meta={"shortcode": "AAA"}),
            RawReview(text="향이 진짜 좋아요", url="https://instagram.com/p/BBB/",
                      platform="instagram", meta={"shortcode": "BBB"})]
    db = _FakeDB({("instagram", "AAA", "한줌"), ("instagram", "BBB", "한줌")})
    partitioned: list = []

    def _spy_partition(rs, kb, *, promo_detector=None):
        partitioned.append(list(rs))
        return [], list(rs)

    before = llm_ops.summary()["calls"]
    originals = _patch({
        sources_pkg: {"ApifyHashtagSource": _FakeSource(raws)},
        pipeline: {"connect": db, "LLM": lambda: _NoCallLLM()},
        bias: {"partition": _spy_partition},
        extract: {"extract_review": lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("기보유 조각인데 추출이 호출됐다"))},
    })
    try:
        counts = pipeline.ingest_hashtag(["슬라임후기"])
    finally:
        _restore(originals)
    assert partitioned == [[]], f"홍보성 캐스케이드가 기보유 조각을 받았다: {partitioned}"
    assert counts["skipped_seen"] == 2, counts
    assert counts["collected"] == 2, f"collected 는 컷 전 원본 수여야 한다: {counts}"
    assert llm_ops.summary()["calls"] - before == 0, "LLM 호출이 기록됐다"
    print("✓ 해시태그 컷이 bias.partition 앞 · LLM 호출 0 OK")


def _run_seller_profiles(seen_urls: set[str], posts, **kwargs):
    """`ingest_seller_profiles` 를 네트워크·LLM·DB 없이 돌리고 `(counts, 추출된 URL 목록)`."""
    from slime_rag import linking
    from slime_rag import sources as sp

    extracted: list[str] = []

    class _DB(_FakeDB):
        def fetchall(self):
            sql, _p = self.queries[-1]
            if "source_permalink" in sql:
                return [(u,) for u in seen_urls]
            return super().fetchall()

    class _Profiles:
        COST_PER_1000 = 21.0

        def __init__(self, *a, **kw):
            pass

        def collect(self, *a, **kw):
            return list(posts)

    class _KB:
        markets = [{"handle": "murmur", "market_word": "머머"}]

    originals = _patch({
        sp: {"InstagramProfileSource": _Profiles},
        linking: {"load_kb": lambda: _KB()},
        pipeline: {"connect": _DB(), "LLM": lambda: _NoCallLLM(),
                   "_specs_from_seller_post": lambda cur, market, text, url, llm:
                       (extracted.append(url), (1, False, 0))[1]},
    })
    try:
        return pipeline.ingest_seller_profiles(dry_run=False, **kwargs), extracted
    finally:
        _restore(originals)


def test_seller_profile_cut_runs_before_extract_spec():
    """기보유 게시물은 `_specs_from_seller_post`(= `extract_spec` 유료 호출) **앞에서** 잘린다.

    컷을 그 호출 **뒤**로 옮겨도 통과하는 판정은 아무것도 지키지 않는다 — 이 경로에서 아끼려는
    건 중복 행이 아니라 순수 LLM 비용이라, 호출이 나간 뒤의 스킵은 절감이 0이다.
    """
    posts = [_ig_post("SEEN", "https://www.instagram.com/p/SEEN/"),
             _ig_post("NEW", "https://www.instagram.com/p/NEW/")]
    counts, extracted = _run_seller_profiles({"https://www.instagram.com/p/SEEN/"}, posts)
    assert extracted == ["https://www.instagram.com/p/NEW/"], extracted
    assert counts["skipped_seen"] == 1 and counts["llm_calls_saved_by_dedup"] == 1, counts
    print("✓ 판매자 컷이 extract_spec 앞 · 절감 카운트 노출 OK")


def test_seller_profile_cut_is_optional():
    """판매자 경로의 컷은 **옵션**이다(계획 R4) — 캡션 수정 반영을 위해 강제 재추출이 남는다."""
    import inspect
    assert inspect.signature(pipeline.ingest_seller_profiles).parameters["skip_seen"].default is True
    posts = [_ig_post("SEEN", "https://www.instagram.com/p/SEEN/")]
    _counts, extracted = _run_seller_profiles({"https://www.instagram.com/p/SEEN/"}, posts,
                                              skip_seen=False)
    assert extracted == ["https://www.instagram.com/p/SEEN/"], \
        "skip_seen=False 인데도 기보유 게시물이 잘렸다 — 강제 재추출 경로가 죽었다"
    print("✓ skip_seen=False 강제 재추출 경로 살아 있음 OK")


# ---------------------------------------------------------------- Phase 2
_LIST_HTML = "".join(
    f"<a href='/mgallery/board/view/?id=amos&no={no}'>글</a>" for no in (300, 250, 200, 150))


def _collect_urls_requested(robots=None, **kwargs) -> list[str]:
    """`DCInsideSource.collect` 를 네트워크 없이 돌리고 **상세 요청이 나간 URL** 을 돌려준다.

    `robots`: URL → 허용 여부. 미지정이면 전부 허용.
    """
    from slime_rag.sources import dcinside as dc

    requested: list[str] = []

    def _fake_get(session, url, throttle=None, **kw):
        if "lists/" in url:
            return _LIST_HTML
        requested.append(url)
        return ""                              # 상세 HTML 은 빈 값 → 이후 파싱이 조용히 끝난다

    class _PassGate:
        def __init__(self, *a, **kw):
            pass

        def should_stop(self):
            return False

        def filter(self, cands):
            return cands

        def finish(self):
            pass

    originals = _patch({
        dc: {"get": _fake_get,
             "robots_allowed": (lambda url, *a, **kw: robots(url)) if robots
                               else (lambda *a, **kw: True),
             "RelevanceGate": _PassGate},
    })
    try:
        list(dc.DCInsideSource(gallery_id="amos").collect(["뭉치"], max_pages=1, **kwargs))
    finally:
        _restore(originals)
    return requested


def _no_of(url: str) -> int:
    import re
    return int(re.search(r"[?&]no=(\d+)", url).group(1))


def test_watermark_cuts_before_detail_request():
    """`min_thread_no` 이하 글은 **상세 HTTP 요청이 나가지 않는다**(AC-2).

    컷이 상세 요청 뒤에 있으면 목록만 줄고 실제 트래픽은 그대로다 — 절감이 없다.
    """
    requested = _collect_urls_requested(min_thread_no=200)
    assert sorted(_no_of(u) for u in requested) == [250, 300], requested
    print("✓ 워터마크 이하 글에 상세 요청 없음 OK")


def test_watermark_absent_keeps_legacy_behaviour():
    """`min_thread_no` 미지정이면 필터가 통째로 꺼진다(하위호환)."""
    assert sorted(_no_of(u) for u in _collect_urls_requested()) == [150, 200, 250, 300]
    print("✓ min_thread_no 미지정 시 기존 동작 OK")


def test_revisit_threads_pass_the_watermark():
    """재방문 대상은 워터마크 아래여도 통과한다 — 새 댓글은 옛 글에 달리기 때문."""
    requested = _collect_urls_requested(min_thread_no=280, revisit_threads=[150])
    assert sorted(_no_of(u) for u in requested) == [150, 300], requested
    print("✓ revisit_threads 가 워터마크를 통과 OK")


def test_revisit_threads_reach_posts_absent_from_search():
    """재방문 글은 **검색 목록에 없어도** 조회된다.

    워터마크 예외로만 두면 그 글이 이번 키워드 검색 결과에 없을 때 영영 안 닿는다 —
    이름이 약속하는 '재방문'이 되려면 URL 을 직접 만들어 받아야 한다.
    """
    requested = _collect_urls_requested(min_thread_no=999, revisit_threads=[42])
    assert [_no_of(u) for u in requested] == [42], \
        f"검색 목록에 없는 재방문 글에 요청이 안 갔다: {requested}"
    print("✓ 검색 목록 밖 재방문 글도 직접 조회 OK")


def test_revisit_threads_are_fetched_once_even_if_listed_twice():
    """중복 재방문 대상은 **한 번만** 조회한다.

    재방문 목록은 대개 `SELECT thread_no FROM reviews …` 로 만들어져 같은 글이 여러 번 들어온다.
    두 번 받으면 HTTP 가 두 배로 나가고, 같은 글의 RawReview 가 둘이 되어 스레드 그룹핑이
    하나를 덮어쓴다(그러면 '배치에 넣은 수'와 '색인에서 건너뛴 수'의 등식도 깨진다).
    """
    requested = _collect_urls_requested(min_thread_no=999, revisit_threads=[42, 42, 42])
    assert [_no_of(u) for u in requested] == [42], f"같은 글을 여러 번 받았다: {requested}"
    print("✓ 중복 재방문 대상 1회 조회 OK")


def test_robots_denial_blocks_the_direct_revisit_fetch():
    """robots 가 상세 경로를 막으면 재방문 글은 **받지 않는다**.

    재방문은 목록을 건너뛰고 URL 로 직행하므로, 여기서 robots 를 안 물으면 이 소스의 유일한
    robots 확인을 통째로 우회하게 된다(`base.get` 은 robots 를 보지 않는다).
    책임 수집(robots·지연·페이지 상한)은 이 저장소의 1급 규칙이다.

    검증 대상은 **직접 조회**다 — 그래서 목록에 없는 글번호(42)를 쓴다. 목록에 있는 글을 쓰면
    워터마크가 어차피 걸러서 통과 여부가 robots 와 무관해진다.
    """
    denied = _collect_urls_requested(
        robots=lambda url: "lists/" in url,          # 목록은 허용, 상세는 거부
        min_thread_no=999, revisit_threads=[42])
    assert 42 not in [_no_of(u) for u in denied], \
        f"robots 로 거부했는데 재방문 글을 받았다: {denied}"
    # 대조군: 허용하면 같은 글을 받는다 — 테스트가 '아무것도 안 받아서' 통과하는 게 아니다.
    allowed = _collect_urls_requested(min_thread_no=999, revisit_threads=[42])
    assert [_no_of(u) for u in allowed] == [42], allowed
    print("✓ robots 거부 시 재방문 직접 조회 차단(대조군 통과) OK")


def test_orphan_thread_key_never_becomes_a_wildcard():
    """스레드 번호를 못 읽는 조각(`thread_key` == '')은 문맥 매칭의 **와일드카드가 되면 안 된다**.

    빈 키를 집합에 넣으면 같은 처지의 기보유 글이 전부 매칭돼 버릴 글에 유료 배치가 나간다.
    `None`/'' 을 '아무거나와 같음'으로 취급하지 않는다는 계약이다.
    """
    from slime_rag.sources.base import RawReview

    # URL 에 `no=` 가 없는 글 — thread_key 가 '' 로 떨어진다.
    orphan = RawReview(text="본문", url="https://gall.dcinside.com/mgallery/board/view/",
                       platform="dcinside", raw_title="제목",
                       meta={"keyword": "뭉치", "gallery": "amos", "type": "post", "toxic": False})
    fresh_cmt = _dc_raw("comment", "300", cno="777")     # 전혀 다른 스레드의 새 댓글
    db = _FakeDB({("amos", pipeline.dc_post_id(orphan), "한줌")})
    counts = _run_ingest_dcinside([orphan, fresh_cmt], db, llm=object())
    assert counts["context_posts"] == 0, \
        f"고아 키가 와일드카드로 매칭됐다(유료 호출 낭비): {counts}"
    assert counts["context_skipped"] == 0, counts
    print("✓ 고아 스레드 키가 와일드카드가 되지 않음 OK")


def test_context_invariant_is_reported_not_asserted():
    """문맥 불변식은 **양쪽 카운트를 다 내보내** 사후 검증이 가능해야 한다.

    `assert` 로만 두면 `python -O` 에서 검사가 통째로 사라지고, 터질 땐 이미 유료 추출·커밋이
    끝난 뒤라 그 런의 비용 원장까지 같이 날아간다.
    """
    post = _dc_raw("post", "100")
    db = _FakeDB({("amos", pipeline.dc_post_id(post), "한줌")})
    counts = _run_ingest_dcinside([post, _dc_raw("comment", "100", cno="901")], db, llm=object())
    assert counts["context_posts"] == counts["context_skipped"] == 1, counts
    print("✓ 문맥 불변식이 카운트 양쪽으로 노출 OK")


def test_watermark_is_scoped_to_the_collection_anchor():
    """워터마크는 **앵커별**이다 — `source='amos'` 전체 최댓값이 아니다.

    수집이 제품 앵커 키워드 검색이라(ADR-0007), 전체 최댓값을 쓰면 **처음 수집하는 제품**의
    워터마크가 남의 제품이 올려놓은 최신 글번호가 된다. 그러면 그 제품의 과거 글이 상세 요청도
    없이 잘리고, 목록이 최신순이라 첫 페이지에서 페이징까지 끝난다 — 카운트에는 '새 글 없음'과
    구분되지 않는 0 만 남는 **조용한 유실**이다.
    """
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(pipeline._max_thread_no))).body[0]
    code = "\n".join(ast.unparse(n) for n in fn.body[1:])
    assert "product = ANY(%s)" in code, f"앵커 술어가 없다(전체 최댓값을 쓴다): {code}"

    # 앵커 이력이 없으면 None → 전량 수집(페일오픈). 컷이 켜지면 안 된다.
    db = _FakeDB()
    db.max_thread_no = None
    counts = _run_ingest_dcinside([_dc_raw("post", "100")], db, llm=object())
    assert counts["min_thread_no"] is None, counts
    assert counts["watermark_anchors"] == ["뭉치"], counts
    print("✓ 워터마크가 앵커 스코프 · 이력 없으면 전량 수집 OK")


def test_seen_post_stays_in_batch_as_context_but_is_not_indexed():
    """기보유 **글**은 새 댓글이 남은 스레드에서 배치에 문맥으로 남되 색인되지 않는다.

    배치 추출의 존재 이유 절반이 형제 문맥이다(제품명 생략 댓글의 귀속 · market 상속은 글에서
    온 값이 권위). 글을 빼면 증분 런에서만 조용히 그 성질을 잃는다. 배치는 어차피 돌기 때문에
    추가 호출은 없다 — 늘어나는 건 그 호출의 입력 토큰뿐이다.
    """
    from slime_rag import extract

    post = _dc_raw("post", "100")
    old_c, new_c = _dc_raw("comment", "100", cno="900"), _dc_raw("comment", "100", cno="901")
    db = _FakeDB({("amos", pipeline.dc_post_id(r), "한줌") for r in (post, old_c)})
    seen_by_extract: list = []

    originals = _patch({
        sources_pkg: {"DCInsideSource": _FakeSource([post, old_c, new_c])},
        pipeline: {"connect": db, "LLM": lambda: object()},
        index: {"index_post": _fake_index_post(db)},
    })
    ex_orig = _patch({extract: {"extract_collected": lambda rs, *a, **kw: (
        seen_by_extract.append(list(rs)) or [(r, {}) for r in rs])}})
    try:
        counts = pipeline.ingest_dcinside(slime="뭉치", market="예찬", dry_run=False)
    finally:
        _restore(ex_orig)
        _restore(originals)

    batch = seen_by_extract[0]
    assert post in batch, "새 댓글이 남은 스레드인데 기보유 글이 배치에서 빠졌다"
    assert old_c not in batch, "기보유 댓글은 배치에 남길 이유가 없다"
    assert counts["context_posts"] == 1, counts
    assert counts["indexed_rows"] == 1, f"문맥용 글까지 색인됐다: {counts}"
    print("✓ 기보유 글은 문맥으로만 남고 색인 제외 OK")


def test_seen_post_is_dropped_when_its_thread_has_nothing_new():
    """새 조각이 하나도 없는 스레드는 글도 남기지 않는다 — 문맥을 남길 대상이 없다."""
    post, cmt = _dc_raw("post", "100"), _dc_raw("comment", "100", cno="900")
    db = _FakeDB({("amos", pipeline.dc_post_id(r), "한줌") for r in (post, cmt)})
    counts = _run_ingest_dcinside([post, cmt], db)
    assert counts["context_posts"] == 0, counts
    assert counts["llm_calls_saved_by_dedup"] == 1, f"배치가 통째로 사라져야 한다: {counts}"
    print("✓ 새 조각 없는 스레드는 문맥도 안 남김 OK")


def test_dead_thread_post_is_not_dragged_in_by_an_unrelated_fresh_post():
    """다른 스레드에 새 글이 있다고 **죽은 스레드의 기보유 글**까지 배치에 끌려오면 안 된다.

    스레드 키를 글이 갖지 않는 meta 로 맞추면 글 쪽이 늘 `None` 이 되고, 그 `None` 이 집합에
    섞이는 순간 기보유 글 전부가 매칭된다 — 버릴 글에 유료 호출만 나가는 오작동이다.
    한 런의 무관한 결과가 다른 스레드의 처리 방식을 바꾸면 안 된다는 뜻이기도 하다.
    """
    dead_post = _dc_raw("post", "100")                       # 기보유 · 이 스레드엔 새 조각 없음
    fresh_post = _dc_raw("post", "200")                      # 전혀 다른 스레드의 새 글
    db = _FakeDB({("amos", pipeline.dc_post_id(dead_post), "한줌")})
    counts = _run_ingest_dcinside([dead_post, fresh_post], db, llm=object())
    assert counts["context_posts"] == 0, \
        f"죽은 스레드의 기보유 글이 배치에 끌려왔다(유료 호출 낭비): {counts}"
    assert counts["indexed_rows"] == 1, counts
    print("✓ 무관한 새 글이 죽은 스레드 글을 끌어오지 않음 OK")


def test_thread_key_is_a_single_shared_rule():
    """스레드 판정은 `extract.thread_key` 한 곳에서 온다 — 글은 URL, 댓글은 `parent_no`.

    글의 meta 엔 스레드 번호가 없다는 게 이 분기의 이유다. 파이프라인이 자기만의 규칙을
    만들면 `group_threads` 와 갈라지고, 갈라진 순간 문맥 판정이 조용히 어긋난다.
    """
    from slime_rag import extract

    post, cmt = _dc_raw("post", "100"), _dc_raw("comment", "100", cno="900")
    assert "thread_no" not in post.meta, "수집기는 글에 thread_no 를 싣지 않는다(테스트 전제)"
    assert extract.thread_key(post) == "100", "글은 URL 에서 읽어야 한다"
    assert extract.thread_key(cmt) == "100", "댓글은 parent_no 에서 읽어야 한다"
    # 같은 스레드로 묶여야 배치가 하나다 — 키가 갈라지면 여기서 2가 된다.
    assert extract.count_thread_batches([post, cmt]) == 1
    print("✓ thread_key 단일 규칙(글=URL · 댓글=parent_no) OK")


def test_watermark_query_and_margin():
    """워터마크는 DB 최댓값에서 마진을 뺀 값이다(계획 R5) — '마지막 런 시각'이 아니다."""
    import inspect
    src = inspect.getsource(pipeline._max_thread_no)
    assert "max((source_ref->>'thread_no')::bigint)" in src, src
    assert pipeline.WATERMARK_MARGIN > 0, "마진 0 이면 뒤늦게 매칭되는 옛 글이 영영 안 들어온다"

    db = _FakeDB()
    db.max_thread_no = 202244
    raws = [_dc_raw("post", "202300")]
    counts = _run_ingest_dcinside(raws, db, llm=object())
    assert counts["min_thread_no"] == 202244 - pipeline.WATERMARK_MARGIN, counts
    print("✓ 워터마크 = DB 최댓값 - 마진 OK")


def test_pagination_break_is_documented_as_intentional():
    """빈 목록에서 페이징을 끝내는 `break` 가 **의도**임이 주석에 남아야 한다.

    필터를 통과한 글이 0건이면 '검색 결과 없음'으로 읽히지만, 실제로는 목록이 최신순이라
    뒤 페이지가 더 옛 글뿐이라는 뜻이다. 주석이 없으면 나중에 '버그'로 고쳐진다.
    """
    import inspect
    src = inspect.getsource(DCInsideSource.collect)
    assert "페이징 종료" in src, "페이징 종료가 의도임을 밝히는 주석이 없다"
    print("✓ 페이징 종료 주석 존재 OK")


# ---------------------------------------------------------------- Phase 3
def _fresh_row(n: int = 10) -> dict:
    from slime_rag.config import settings
    return {"n_reviews": n, "model": settings.model_judge,
            "generated_at": "2026-08-07T12:00:00+00:00", "payload": {}}


def test_is_stale_rules():
    """신선도 판정 네 조건 — 전부 **무료**(LLM·SQL 재구현 없음)."""
    from slime_rag.config import settings

    assert pipeline._is_stale(None, 5, 3) is True, "미생성은 항상 대상"
    assert pipeline._is_stale(_fresh_row(10), 10, 3) is False, "변화 없으면 대상 아님"
    assert pipeline._is_stale(_fresh_row(10), 12, 3) is False, "min_delta 미만 증가는 제외"
    assert pipeline._is_stale(_fresh_row(10), 13, 3) is True, "min_delta 이상 증가는 대상"
    assert pipeline._is_stale({**_fresh_row(10), "model": "gpt-4o"}, 10, 3) is True, "모델 교체"
    assert pipeline._is_stale({**_fresh_row(10), "generated_at": "2026-08-01T00:00:00+00:00"},
                              10, 3) is True, "ADR-0015 이전 payload 형태"
    assert settings.model_judge, "테스트 전제: 판정 모델이 설정돼 있다"
    print("✓ _is_stale 네 조건 OK")


def test_is_stale_survives_naive_and_missing_timestamps():
    """커토프가 tz-aware 라 naive/결손 시각과 비교해도 **TypeError 가 나면 안 된다**.

    naive 는 UTC 로 올려서 비교하고, 못 읽는 값은 '오래된 것'으로 본다 —
    모르는 걸 최신으로 치면 옛 형태 payload 가 영영 안 갱신된다.
    """
    assert pipeline.ADR_0015_CUTOFF.tzinfo is not None, "커토프에서 timezone.utc 를 떼지 말 것"
    assert pipeline._is_stale({**_fresh_row(), "generated_at": "2026-08-09T00:00:00"}, 10, 3) is False
    assert pipeline._is_stale({**_fresh_row(), "generated_at": None}, 10, 3) is True
    assert pipeline._is_stale({**_fresh_row(), "generated_at": "그저께"}, 10, 3) is True
    print("✓ naive·결손 타임스탬프 안전 OK")


def test_refresh_uses_view_counts_not_sql_recount():
    """개수는 `consolidated_for`/`order_view_for` 에서 온다 — SQL `count(*)` 로 재구현하지 않는다.

    두 축의 정의가 다르기 때문이다: `n_reviews` 는 행 수, `n_orders` 는 팬아웃을 접은 **조각 수**.
    주문 축을 행 수로 세면 '배송 후기 27건'처럼 부풀려진다.
    """
    import ast
    import inspect
    import textwrap

    fn = ast.parse(textwrap.dedent(inspect.getsource(pipeline.refresh_stale_summaries))).body[0]
    # 독스트링은 뺀다 — 거기엔 'count(*) 로 세면 안 된다'는 **금지 설명**이 들어 있어서,
    # 원문 그대로 검사하면 규칙을 적어 둔 것 때문에 테스트가 깨진다.
    body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    code = "\n".join(ast.unparse(n) for n in body)
    assert 'consolidated_for(market, product, with_summary=False)' in code, code
    assert 'order_view_for(market, with_summary=False)' in code, code
    assert "count(*)" not in code, "개수를 SQL 로 재구현했다"
    print("✓ 개수 출처가 뷰 계산(축별 정의 보존) OK")


def test_refresh_dry_run_is_default_and_free():
    """기본이 `dry_run=True` 이고, 그 경로는 요약 생성 함수를 **부르지 않는다**(유료 차단)."""
    import inspect
    assert inspect.signature(pipeline.refresh_stale_summaries).parameters["dry_run"].default is True

    called: list = []
    originals = _patch({
        pipeline: {
            "list_markets": lambda: ["빈짱"],
            "list_products": lambda m: [{"product": "한줌"}],
            "consolidated_for": lambda m, p, **kw: {"n_reviews": 99},
            "order_view_for": lambda m, **kw: {"n_orders": 99},
            "stored_summaries": lambda m, p: None,
            "stored_market_summaries": lambda m: None,
            "generate_summaries": lambda *a: called.append(a),
            "generate_market_summaries": lambda *a: called.append(a),
        },
    })
    try:
        out = pipeline.refresh_stale_summaries()
        assert out["count"] == 2 and out["est_cost_usd"] > 0, out
        assert called == [], f"dry_run 인데 생성이 호출됐다: {called}"
        # 근거가 min_evidence 미만이면 아예 대상이 아니다.
        pipeline.consolidated_for = lambda m, p, **kw: {"n_reviews": 1}
        pipeline.order_view_for = lambda m, **kw: {"n_orders": 1}
        assert pipeline.refresh_stale_summaries()["count"] == 0
    finally:
        _restore(originals)
    print("✓ dry_run 기본 · 무생성 · min_evidence 게이트 OK")


def test_refresh_is_idempotent_after_generation():
    """요약을 만든 직후 다시 돌리면 그 대상은 빠진다(멱등) — 저장 메타가 그대로 판정 재료다."""
    from slime_rag.config import settings

    store: dict = {}
    originals = _patch({
        pipeline: {
            "list_markets": lambda: ["빈짱"],
            "list_products": lambda m: [{"product": "한줌"}],
            "consolidated_for": lambda m, p, **kw: {"n_reviews": 7},
            "order_view_for": lambda m, **kw: {"n_orders": 7},
            "stored_summaries": lambda m, p: store.get((m, p)),
            "stored_market_summaries": lambda m: store.get((m, None)),
            "generate_summaries": lambda m, p: store.__setitem__(
                (m, p), {"n_reviews": 7, "model": settings.model_judge,
                         "generated_at": "2026-08-07T13:00:00+00:00"}),
            "generate_market_summaries": lambda m: store.__setitem__(
                (m, None), {"n_reviews": 7, "model": settings.model_judge,
                            "generated_at": "2026-08-07T13:00:00+00:00"}),
        },
    })
    try:
        assert pipeline.refresh_stale_summaries(dry_run=False)["regenerated"] == 2
        assert pipeline.refresh_stale_summaries()["count"] == 0, "재실행에 대상이 남았다"
    finally:
        _restore(originals)
    print("✓ 생성 직후 재실행 대상 0건(멱등) OK")


if __name__ == "__main__":
    test_comment_post_id_is_run_independent()
    test_post_post_id_unchanged()
    test_ordinal_is_never_used_as_fallback()
    test_missing_comment_no_is_counted_not_silently_dropped()
    test_second_run_indexes_nothing()
    test_existing_post_ids_skips_roundtrip_when_empty()
    test_dcinside_cut_runs_before_extraction()
    test_dcinside_dedup_saving_counts_batches_not_pieces()
    test_dedup_counter_is_named_apart_from_promo_gate_saving()
    test_hashtag_cut_runs_before_promo_cascade()
    test_seller_profile_cut_runs_before_extract_spec()
    test_seller_profile_cut_is_optional()
    test_watermark_cuts_before_detail_request()
    test_watermark_absent_keeps_legacy_behaviour()
    test_revisit_threads_pass_the_watermark()
    test_revisit_threads_reach_posts_absent_from_search()
    test_revisit_threads_are_fetched_once_even_if_listed_twice()
    test_robots_denial_blocks_the_direct_revisit_fetch()
    test_orphan_thread_key_never_becomes_a_wildcard()
    test_context_invariant_is_reported_not_asserted()
    test_watermark_is_scoped_to_the_collection_anchor()
    test_seen_post_stays_in_batch_as_context_but_is_not_indexed()
    test_seen_post_is_dropped_when_its_thread_has_nothing_new()
    test_dead_thread_post_is_not_dragged_in_by_an_unrelated_fresh_post()
    test_thread_key_is_a_single_shared_rule()
    test_watermark_query_and_margin()
    test_pagination_break_is_documented_as_intentional()
    test_is_stale_rules()
    test_is_stale_survives_naive_and_missing_timestamps()
    test_refresh_uses_view_counts_not_sql_recount()
    test_refresh_dry_run_is_default_and_free()
    test_refresh_is_idempotent_after_generation()
    print("\n모든 오프라인 테스트 통과 ✅")
