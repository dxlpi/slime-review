# -*- coding: utf-8 -*-
"""디시 원문 저장소 오프라인 테스트 — 네트워크·LLM·DB 미접촉/무비용.

지키는 성질은 넷이다.
  ① **처리보다 먼저 저장한다.** 파싱이 죽어도 방금 받은 HTML 은 디스크에 남는다
     (셀렉터 하나 깨진 날 그 런의 HTTP 를 통째로 버리지 않는다).
  ② **가공하지 않는다.** 저장물은 HTML 원문 + 댓글 AJAX JSON 그대로다 — 디시콘 댓글
     제외 같은 **처리 규칙**이 저장 단계에 새면 규칙이 바뀔 때 원문에서 다시 못 뽑는다.
  ③ **재처리가 라이브와 같은 결과를 낸다.** 두 경로가 `_build_candidates` 한 벌을 공유하지
     않으면 `meta` 모양이 갈려 하류(스레드 키·원문 링크·작성 메타)가 소스마다 달라진다.
  ④ **재처리는 HTTP 를 안 쓴다.** 이 경로의 존재 이유가 그것이다.

실행:  python -m eval.test_dcinside_rawstore   (repo 루트에서)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from slime_rag import rawstore as rs
from slime_rag.sources import dcinside as dc

KIND = "dc_thread"
NO = "202158"
PURL = f"https://gall.dcinside.com/mgallery/board/view/?id=amos&no={NO}"

POST_HTML = """
<html><body>
  <span class="title_subject">ㅈㄴ 빠코볼 후기</span>
  <div class="gall_writer" data-loc="view" data-nick="슬붕이" data-ip="1.2">
    <span class="gall_date" title="2026-07-27 21:03:00">07.27</span>
    <span class="gall_count">조회 412</span>
    <span class="gall_comment">댓글 7</span>
  </div>
  <div class="write_div">
    <script>var ad=1;</script>
    빠코볼 세 개 샀는데 기포 터지는 소리가 진짜 좋았음. 무게감도 있고 잔여감 없음.
  </div>
  <span class="up_num">12</span><span class="down_num">1</span>
  <input type="hidden" name="e_s_n_o" value="TOKEN123" />
</body></html>
"""

# 댓글 원문 — 텍스트 댓글 2건 + **디시콘 댓글 1건**(`memo` 는 이미지 태그뿐이라 `_parse_comments`
# 가 버린다). 저장물에 이 항목이 살아 있어야 '처리 규칙이 저장 단계에 안 샜다'가 증명된다.
COMMENT_JSON = {
    "comments": [
        {"no": "1091268", "parent": NO, "name": "ㅇㅇ", "ip": "175.223",
         "memo": "나도 이거 <b>존좋</b>이었음 기포 미쳤다", "reg_date": "2026-07-27 21:40:11"},
        {"no": "1091269", "parent": NO, "name": "슬붕이2", "ip": "118.34",
         "memo": '<img src="//dcimg.dcinside.com/dccon.php?no=1" class="written_dccon">',
         "reg_date": "2026-07-27 21:44:02"},
        {"no": "1091270", "parent": NO, "name": "ㅇㅇ", "ip": "211.36",
         "memo": "배송은 좀 느렸는데 제품은 만족했어", "reg_date": "2026-07-27 22:01:55"},
    ]
}


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="dc-rawstore-test-"))


class _FakeSession:
    """`requests.Session` 스텁 — 댓글 AJAX 만 받는다. 네트워크 경계 주입점."""

    def __init__(self, payload: dict | None = None):
        self.headers = {"User-Agent": "test"}
        self.payload = payload if payload is not None else COMMENT_JSON
        self.posts: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append(dict(data or {}))
        return _FakeResponse(self.payload)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _source(root: Path, *, html: str = POST_HTML, payload: dict | None = None):
    """`get`(글 HTML)과 세션(댓글 JSON)만 갈아 끼운 수집기. 그 외는 실제 코드다."""
    src = dc.DCInsideSource(gallery_id="amos", comment_pages=1, raw_root=root)
    src.s = _FakeSession(payload)
    src.throttle.min_interval = 0.0
    calls: list[str] = []

    def fake_get(session, url, throttle=None, **kw):
        calls.append(url)
        return html

    dc.get = fake_get                       # 모듈 네임스페이스 주입(원복은 _restore)
    src._http_calls = calls
    return src


_REAL_GET = dc.get


def _restore():
    dc.get = _REAL_GET


# ---------------------------------------------------------------- ① 처리 전 저장
def test_raw_saved_before_processing():
    """`_build_candidates` 가 죽어도 원문 파일은 남아야 한다.

    회귀 근거: 저장을 파싱 뒤에 두면 셀렉터가 깨진 날 이미 받은 HTTP 가 전부 헛일이 된다.
    디시 글은 지워지면 재수집으로 못 되찾으므로 그 손실은 복구 불가다.
    """
    root = _root()
    src = _source(root)
    try:
        def boom(*a, **k):
            raise RuntimeError("셀렉터 깨짐(의도된 실패)")
        src._build_candidates = boom
        try:
            src._candidates_for(PURL, "빠코볼")
            raise AssertionError("주입한 예외가 안 났다 — 테스트가 실패 경로를 안 탔다")
        except RuntimeError as e:
            assert "셀렉터" in str(e)
        files = sorted(rs.run_dir(KIND, NO, root=root).glob("*.json"))
        assert len(files) == 1, f"처리 실패인데 원문이 안 남았다: {files}"
        doc = json.loads(files[0].read_text(encoding="utf-8"))
        assert doc["items"][0]["html"] == POST_HTML
    finally:
        _restore()
    print("✓ 파싱이 죽어도 원문은 디스크에 남는다(처리 전 저장) OK")


def test_envelope_shape():
    """봉투가 '무슨 요청의 답인가'를 담는다 — 액터·요청 URL·키워드·갤러리·댓글 페이지 수."""
    root = _root()
    src = _source(root)
    try:
        src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()
    doc = json.loads(sorted(rs.run_dir(KIND, NO, root=root).glob("*.json"))[0]
                     .read_text(encoding="utf-8"))
    assert doc["actor"] == "dcinside:amos"
    assert doc["kind"] == KIND and doc["key"] == NO
    assert doc["requested"]["url"] == PURL
    assert doc["requested"]["keyword"] == "빠코볼"
    assert doc["requested"]["comment_pages"] == 1
    # 액터가 아니라 직접 HTTP 라 건당 청구가 없다. 0.0 이면 '측정해서 0'처럼 읽힌다.
    assert doc["usage_total_usd"] is None, "무과금 경로는 None 이어야 한다(0.0 아님)"
    assert doc["scraped_at"] and doc["_note"]
    print("✓ 봉투(액터·요청 URL·앵커·무과금 None) OK")


# ---------------------------------------------------------------- ② 가공 없음
def test_comment_payload_is_stored_unparsed():
    """디시콘 댓글이 **저장물에는 남아야** 한다 — 제외는 처리 규칙이지 저장 규칙이 아니다."""
    root = _root()
    src = _source(root)
    try:
        cands = src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()
    doc = json.loads(sorted(rs.run_dir(KIND, NO, root=root).glob("*.json"))[0]
                     .read_text(encoding="utf-8"))
    cmt_items = [i for i in doc["items"] if i["type"] == "comments"]
    assert len(cmt_items) == 1 and cmt_items[0]["comment_page"] == 1
    stored = cmt_items[0]["payload"]["comments"]
    assert len(stored) == 3, f"원문 댓글 3건이 그대로여야 하는데 {len(stored)}건"
    assert any("dccon" in (c.get("memo") or "") for c in stored), \
        "디시콘 댓글이 저장 단계에서 사라졌다 — 처리 규칙이 저장에 샜다"
    # 반면 후보(처리 결과)에서는 빠져 있어야 한다 — 규칙 자체는 그대로 산다.
    texts = [c.text for c in cands if c.meta["type"] == "comment"]
    assert len(texts) == 2, f"텍스트 댓글만 후보여야 하는데 {texts}"
    print("✓ 저장은 무가공(디시콘 포함) · 처리는 규칙대로 제외 OK")


def test_html_stored_verbatim():
    """광고 `<script>` 까지 포함한 HTML 원문 그대로 — 파싱 산출물이 아니다."""
    root = _root()
    src = _source(root)
    try:
        src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()
    item = rs.latest_items(KIND, NO, root=root)
    post = next(i for i in item if i["type"] == "post")
    assert post["html"] == POST_HTML and "<script>" in post["html"]
    assert post["url"] == PURL and post["thread_no"] == NO
    print("✓ 글 HTML 무가공 저장 OK")


# ---------------------------------------------------------------- ③ 재처리 = 라이브
def _key(r):
    """비교 키 — 하류가 실제로 읽는 것만(객체 동일성이 아니라 계약을 본다)."""
    return (r.text, r.url, r.platform, r.posted_at, r.raw_title, r.meta)


def test_reprocess_matches_live_exactly():
    """디스크 재처리가 라이브 수집과 **같은 RawReview** 를 낸다.

    두 경로가 `_build_candidates` 한 벌을 공유하지 않으면 `meta` 가 갈리고, 그 순간
    `extract.thread_key`(스레드 문맥)·`source_links.build_source_ref`(원문 링크)·
    `index.post_columns`(작성자) 가 소스마다 다른 값을 받는다 — 화면엔 안 보이는 실패다.
    """
    root = _root()
    src = _source(root)
    try:
        live = src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()
    # 재처리 수집기는 HTTP 를 아예 못 쓰게 만든다(아래 ④ 와 같은 장치).
    reader = dc.DCInsideSource(gallery_id="amos", comment_pages=1, raw_root=root)
    again = list(reader.collect_from_raw([], limit=100, target=None))
    assert [_key(r) for r in live] == [_key(r) for r in again], \
        "라이브와 재처리 결과가 다르다 — 두 경로가 같은 함수를 안 쓰고 있다"
    assert len(live) == 3, f"글 1 + 텍스트 댓글 2 = 3건이어야 하는데 {len(live)}건"
    # 하류 계약 몇 개는 이름으로 못 박는다(리팩터링 때 조용히 빠지는 값들).
    post = again[0]
    assert post.meta["type"] == "post" and post.meta["nick"] == "슬붕이"
    assert post.meta["keyword"] == "빠코볼", "저장 당시 닿은 앵커가 보존돼야 한다"
    cmt = again[1]
    assert cmt.meta["comment_no"] == "1091268" and cmt.meta["parent_no"] == NO
    assert cmt.meta["ordinal"] == 0 and cmt.url.endswith("#cmt")
    print("✓ 재처리 == 라이브(3건 · meta 계약 동일) OK")


def test_later_capture_wins_on_edit():
    """같은 스레드를 다시 받으면 **나중 캡처**로 재처리된다(수정·댓글 추가 반영)."""
    root = _root()
    edited = POST_HTML.replace("잔여감 없음", "잔여감 없음 (추가) 한 달 뒤에도 그대로임")
    for html in (POST_HTML, edited):
        src = _source(root, html=html)
        try:
            src._candidates_for(PURL, "빠코볼")
        finally:
            _restore()
    files = sorted(rs.run_dir(KIND, NO, root=root).glob("*.json"))
    assert len(files) == 2, f"append-only 여야 하는데 {len(files)}개"
    reader = dc.DCInsideSource(gallery_id="amos", comment_pages=1, raw_root=root)
    out = list(reader.collect_from_raw([], limit=100, target=None))
    assert "한 달 뒤에도" in out[0].text, "최신 캡처가 옛 캡처에 밀렸다"
    print("✓ append-only + 최신 캡처 우선 OK")


# ---------------------------------------------------------------- ④ HTTP 0회 · 선택
def test_reprocess_makes_no_http_call():
    """재처리 경로는 네트워크를 **부르지 않는다**. 부르면 즉시 실패한다."""
    root = _root()
    src = _source(root)
    try:
        src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()

    def explode(*a, **k):
        raise AssertionError("재처리가 HTTP 를 호출했다")

    reader = dc.DCInsideSource(gallery_id="amos", comment_pages=1, raw_root=root)
    reader.s = _FakeSession()
    dc.get = explode
    try:
        out = list(reader.collect_from_raw([], limit=100, target=None))
    finally:
        _restore()
    assert len(out) == 3
    assert reader.s.posts == [], "댓글 AJAX 도 안 나가야 한다"
    print("✓ 재처리 HTTP 0회(글·댓글 모두) OK")


def test_selection_by_anchor_and_thread():
    """선택은 두 가지 — 저장 당시 닿은 **앵커**(키워드)와 **글번호**."""
    root = _root()
    other_no, other_url = "199999", "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=199999"
    src = _source(root)
    try:
        src._candidates_for(PURL, "빠코볼")
        src._candidates_for(other_url, "감크숲")
    finally:
        _restore()
    reader = dc.DCInsideSource(gallery_id="amos", comment_pages=1, raw_root=root)
    assert {k for k, *_ in reader._raw_threads([])} == {NO, other_no}, "빈 필터는 전량이다"
    assert {k for k, *_ in reader._raw_threads(["빠코볼"])} == {NO}
    assert {k for k, *_ in reader._raw_threads(["감크숲"])} == {other_no}
    assert {k for k, *_ in reader._raw_threads([], threads=[other_no])} == {other_no}
    assert list(reader._raw_threads(["없는앵커"])) == [], "안 닿은 앵커는 0건이어야 한다"
    print("✓ 앵커/글번호 선택 OK")


def test_zero_text_comment_page_is_still_stored():
    """텍스트 댓글이 0인 페이지도 저장한다 — '안 받았다'와 '받았는데 비었다'는 다른 사실이다."""
    root = _root()
    empty = {"comments": [{"no": "1", "parent": NO, "name": "ㅇㅇ",
                           "memo": '<img class="written_dccon">', "reg_date": "2026-07-27 21:40"}]}
    src = _source(root, payload=empty)
    try:
        cands = src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()
    doc = json.loads(sorted(rs.run_dir(KIND, NO, root=root).glob("*.json"))[0]
                     .read_text(encoding="utf-8"))
    assert [i["type"] for i in doc["items"]] == ["post", "comments"], \
        "텍스트 0건이라고 페이지를 안 남기면 재요청 여부를 판단할 근거가 사라진다"
    assert len(cands) == 1, "후보는 글 1건뿐이어야 한다"
    print("✓ 텍스트 0건 댓글 페이지도 저장 OK")


def test_save_raw_can_be_disabled_for_tests_only():
    """`save_raw=False` 는 꺼지되 처리 결과는 같다(스모크·테스트용 스위치)."""
    root = _root()
    src = _source(root)
    src.save_raw = False
    try:
        out = src._candidates_for(PURL, "빠코볼")
    finally:
        _restore()
    assert len(out) == 3
    assert not rs.run_dir(KIND, NO, root=root).exists()
    print("✓ save_raw=False 는 디스크 미접촉(결과는 동일) OK")


if __name__ == "__main__":
    test_raw_saved_before_processing()
    test_envelope_shape()
    test_comment_payload_is_stored_unparsed()
    test_html_stored_verbatim()
    test_reprocess_matches_live_exactly()
    test_later_capture_wins_on_edit()
    test_reprocess_makes_no_http_call()
    test_selection_by_anchor_and_thread()
    test_zero_text_comment_page_is_still_stored()
    test_save_raw_can_be_disabled_for_tests_only()
    print("\n모든 디시 원문 저장소 테스트 통과 (네트워크·LLM·DB 미접촉)")
