# -*- coding: utf-8 -*-
"""
원문 링크 정책 오프라인 테스트 — `slime_rag.source_links` 순수 함수 전 분기.

DB·네트워크·LLM·streamlit 미사용(무비용) → CI 게이트 대상. 화면에 링크·임베드가
**어떤 조건에서** 나오는가(정책)는 전부 여기서 잡히고, 그게 실제로 배선됐는지(렌더)는
`eval/test_ui_render.py`(로컬 게이트)가 잡는다.

검증:
  - permalink(): 형태 불량 degrade · `#cmt` 가짜 앵커 제거
  - build_source_ref(): 디시 게시물(URL 파싱) / 디시 댓글(id) / 인스타(shortcode) / 링크불가
  - evidence_group_key(): 한 스레드 댓글들이 전량 distinct
  - embed_url(): 임베드 게이트 전 분기(fail-closed)
  - group_evidence_sources(): 팬아웃 중복 제거 + URL 그룹핑 + 구성 라벨
  - _parse_comments(): 댓글 고유 id 보존
  - 무재배포: 식별자에 원문 텍스트가 안 섞인다
  - 신뢰 역효과 방지: 골드 evidence 가 원문에서 회수 가능하다

실행:  python -m eval.test_source_links   (repo 루트에서)
"""
from __future__ import annotations

from slime_rag import source_links as sl

_THREAD = "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=201513"


# ---------------------------------------------------------------- permalink
def test_permalink_branches():
    assert sl.permalink(None) is None, "None → 링크 없음"
    assert sl.permalink("문자열") is None, "dict 아님 → 링크 없음"
    assert sl.permalink({}) is None, "url 없음 → 링크 없음"
    assert sl.permalink({"url": None}) is None
    assert sl.permalink({"url": ""}) is None
    assert sl.permalink({"url": "javascript:alert(1)"}) is None, "http(s) 아닌 스킴 통과"
    assert sl.permalink({"url": "/board/view/?no=1"}) is None, "상대경로 통과"
    assert sl.permalink({"url": _THREAD}) == _THREAD

    # 수집기가 모든 댓글 URL 에 굽는 `#cmt` 는 아무 데도 도달하지 않는 가짜 앵커 → 제거.
    assert sl.permalink({"url": f"{_THREAD}#cmt"}) == _THREAD, "#cmt 가짜 앵커가 남았다"
    print("✓ permalink: 형태 불량 degrade · #cmt 제거 OK")


# ---------------------------------------------------------------- 식별자 조립
def test_build_source_ref():
    # 디시 게시물: meta 에 parent_no 가 없다 → thread_no 를 URL 에서 파싱해야 한다.
    post = sl.build_source_ref("dcinside", _THREAD, {"type": "post", "gallery": "amos"})
    assert post == {"platform": "dcinside", "url": _THREAD, "thread_no": "201513"}, post

    # 디시 댓글: url 은 `#cmt` 가 붙은 채 들어오지만 저장되는 건 맨몸 URL.
    cmt = sl.build_source_ref("dcinside", f"{_THREAD}#cmt",
                              {"type": "comment", "parent_no": "201513",
                               "thread_no": "201513", "comment_no": "1088714", "ordinal": 0})
    assert cmt["url"] == _THREAD, "댓글 url 에 #cmt 가 남았다"
    assert cmt["comment_no"] == "1088714"
    assert "ordinal" not in cmt, "comment_no 가 있는데 ordinal 폴백이 같이 들어갔다"

    # 댓글 id 를 못 잡은 경우: 순번은 `ordinal` 로만 — 앵커 조립에 절대 쓰이지 않게 이름 분리.
    noid = sl.build_source_ref("dcinside", f"{_THREAD}#cmt",
                               {"type": "comment", "parent_no": "201513", "ordinal": 3})
    assert noid.get("ordinal") == 3 and "comment_no" not in noid, noid

    # 인스타: shortcode 있으면 싣고, 없어도(shortCode 널 구멍) url 만으로 링크는 성립.
    ig = sl.build_source_ref("instagram", "https://www.instagram.com/p/ABC123/",
                             {"shortcode": "ABC123"})
    assert ig == {"platform": "instagram", "url": "https://www.instagram.com/p/ABC123/",
                  "shortcode": "ABC123"}, ig
    ig2 = sl.build_source_ref("instagram", "https://www.instagram.com/p/ABC123/", {})
    assert "shortcode" not in ig2 and sl.permalink(ig2), "shortcode 없다고 링크까지 죽었다"

    # 링크 불가 → None (호출부가 '식별자 없는 행'으로 센다 — 무음 갭 금지)
    assert sl.build_source_ref("dcinside", None) is None
    assert sl.build_source_ref("dcinside", "") is None
    print("✓ build_source_ref: 게시물/댓글/순번폴백/인스타/링크불가 OK")


# ---------------------------------------------------------------- 식별자 distinct
def test_thread_comments_distinct():
    """한 스레드의 댓글들은 렌더 URL 이 같아도 **식별자는 전량 distinct** 해야 한다(AC2).

    같지 않으면 근거 목록에서 댓글 20개가 1건으로 소실된다.
    """
    metas = [{"type": "comment", "parent_no": "201513", "comment_no": str(1088714 + i),
              "ordinal": i} for i in range(20)]
    refs = [sl.build_source_ref("dcinside", f"{_THREAD}#cmt", m) for m in metas]
    keys = [sl.evidence_group_key(r) for r in refs]
    assert all(k is not None for k in keys), "링크 가능한 댓글인데 식별자가 None"
    assert len(set(keys)) == 20, f"식별자 distinct {len(set(keys))}개 (기대 20)"
    # 앵커가 없으므로 렌더된 URL 은 전부 같다 — 그래서 표시는 한 줄로 묶는다(§3.6b).
    assert len({sl.permalink(r) for r in refs}) == 1, "앵커가 없는데 URL 이 갈렸다"

    # 게시물 행과 댓글 행은 서로 다른 식별자여야 한다(둘 다 스레드 URL 로 렌더되더라도).
    post = sl.build_source_ref("dcinside", _THREAD, {"type": "post"})
    assert sl.evidence_group_key(post) not in keys, "게시물이 댓글 식별자와 충돌"
    assert sl.evidence_group_key(None) is None and sl.evidence_group_key({}) is None
    print("✓ 식별자: 한 스레드 댓글 20건 전량 distinct · 게시물↔댓글 미충돌 OK")


# ---------------------------------------------------------------- 임베드 게이트
def test_embed_url_gate():
    ok = "https://www.instagram.com/p/ABC123/"
    assert sl.embed_url({"source_permalink": ok}) == "https://www.instagram.com/p/ABC123/embed"
    assert sl.embed_url({"source_permalink": "https://instagram.com/reel/XY_9-z"}) == \
        "https://www.instagram.com/p/XY_9-z/embed", "릴스 경로 미지원"

    # fail-closed: 하나라도 어긋나면 None → 호출부는 텍스트 링크만
    assert sl.embed_url(None) is None
    assert sl.embed_url({}) is None, "source_permalink 없음 → 임베드 없음"
    assert sl.embed_url({"source_permalink": None}) is None
    assert sl.embed_url({"source_permalink": "https://gall.dcinside.com/board/view/?no=1"}) is None, \
        "디시가 임베드 게이트를 통과했다"
    assert sl.embed_url({"source_permalink": "https://instagram.com.evil.example/p/ABC/"}) is None, \
        "호스트 접미사 위장이 통과했다"
    assert sl.embed_url({"source_permalink": "https://www.instagram.com/someuser/"}) is None, \
        "프로필 URL 이 게시물로 통과했다"
    assert sl.embed_url({"source_permalink": "https://www.instagram.com/p/"}) is None, \
        "shortcode 없는 경로가 통과했다"
    print("✓ embed_url: 인스타 게시물만 통과 · 디시/프로필/호스트위장/무 shortcode 차단 OK")


# ---------------------------------------------------------------- 근거 목록 그룹핑
def test_group_evidence_sources():
    post = sl.build_source_ref("dcinside", _THREAD, {"type": "post"})
    c1 = sl.build_source_ref("dcinside", f"{_THREAD}#cmt",
                             {"type": "comment", "parent_no": "201513", "comment_no": "1"})
    c2 = sl.build_source_ref("dcinside", f"{_THREAD}#cmt",
                             {"type": "comment", "parent_no": "201513", "comment_no": "2"})
    ig = sl.build_source_ref("instagram", "https://www.instagram.com/p/ABC123/",
                             {"shortcode": "ABC123"})
    # 같은 조각이 제품별 팬아웃으로 2행 복제된 상황 + 링크 없는 행 하나
    records = [{"source_ref": post}, {"source_ref": post},      # 팬아웃 복제 → 1건으로 접힘
               {"source_ref": c1}, {"source_ref": c2},
               {"source_ref": ig}, {"source_ref": None}]
    groups = sl.group_evidence_sources(records)
    assert len(groups) == 2, f"URL 그룹 {len(groups)}개 (기대 2: 디시 스레드 + 인스타)"

    dc = next(g for g in groups if g["platform"] == "dcinside")
    assert dc["n_pieces"] == 3, f"팬아웃 중복 제거 실패: {dc}"
    assert dc["n_posts"] == 1 and dc["n_comments"] == 2, dc
    # 앵커가 없어 게시물 행과 댓글 행이 같은 URL 로 묶인다 → '댓글 N건'이라고만 쓰면 거짓말.
    assert dc["label"] == "글 1건 + 댓글 2건", dc["label"]
    assert dc["url"] == _THREAD

    igg = next(g for g in groups if g["platform"] == "instagram")
    assert igg["label"] == "글 1건", igg["label"]

    assert sl.group_evidence_sources([]) == []
    assert sl.group_evidence_sources([{"source_ref": None}]) == [], "링크 없는 행이 목록에 남았다"
    print("✓ 근거 목록: 팬아웃 중복 제거 · URL 그룹핑 · 구성 라벨(글+댓글) OK")


# ---------------------------------------------------------------- 수집기 id 보존
def test_parse_comments_keeps_id():
    """댓글 고유 id(`no`)가 파싱에서 버려지지 않는다 — 버리면 재수집 없이 복구 불가."""
    from slime_rag.sources import DCInsideSource
    src = DCInsideSource(gallery_id="amos")
    payload = {"comments": [
        {"memo": "이거 존잼", "no": "1088714", "parent": "201513", "name": "ㅇㅇ",
         "ip": "1.2", "reg_date": "06.13 02:39:41"},
        {"memo": "", "no": "1088715"},                       # 빈 댓글 → 제외
        {"memo": '<img src="dccon.png">', "no": "1088716"},   # 디시콘(텍스트 없음) → 제외
    ]}
    out = src._parse_comments(payload)
    assert len(out) == 1, f"텍스트 댓글만 남아야 하는데 {len(out)}건"
    assert out[0]["no"] == "1088714", "댓글 고유 id 가 파싱에서 버려졌다"
    assert out[0]["text"] == "이거 존잼"
    print("✓ _parse_comments: 댓글 고유 id 보존 · 빈/디시콘 댓글 제외 OK")


# ---------------------------------------------------------------- 캡션 계약
def test_captions_state_both_reasons():
    """건수 캡션은 팬아웃과 댓글 그룹핑 **둘 다**를 사유로 밝혀야 한다 — 하나만 쓰면 오도한다."""
    cap = sl.EVIDENCE_COUNT_CAPTION
    assert "행" in cap and "조각" in cap, "행수 vs 조각수 구분이 캡션에 없다"
    assert "댓글" in cap, "댓글 그룹핑 사유가 캡션에 없다"
    assert "인용" in sl.EVIDENCE_NOT_QUOTE_CAPTION, "evidence≠인용 고지가 없다"
    assert "쿠키" in sl.EMBED_PRIVACY_CAPTION, "제3자 쿠키 고지가 없다"
    print("✓ 캡션: 인용 아님 · 건수 두 사유 · 임베드 프라이버시 OK")


# ---------------------------------------------------------------- 무재배포 / 신뢰 역효과
def test_source_ref_stores_addresses_only():
    """식별자에 원문 텍스트가 섞이지 않는다 — 저장하는 건 주소지 내용이 아니다(무재배포).

    수집 meta 엔 본문·닉네임·제목이 다 들어 있어서, 조립 함수가 meta 를 통째로 흘리면
    조용히 원문이 DB 에 실린다. 화이트리스트로 짓는지 여기서 못박는다.
    """
    ref = sl.build_source_ref("dcinside", f"{_THREAD}#cmt", {
        "type": "comment", "parent_no": "201513", "comment_no": "1088714",
        # 아래는 전부 새면 안 되는 것들
        "text": "본문 원문이 통째로", "parent_title": "글 제목", "nick": "작성자",
        "ip": "1.2", "keyword": "한줌", "toxic": False,
    })
    assert set(ref) <= {"platform", "url", "thread_no", "comment_no", "ordinal", "shortcode"}, \
        f"주소 아닌 키가 식별자에 실렸다: {sorted(set(ref) - {'platform','url','thread_no','comment_no','ordinal','shortcode'})}"
    assert not any("원문" in str(v) or "제목" in str(v) for v in ref.values()), "원문 텍스트 유출"
    print("✓ 무재배포: 식별자는 주소 필드만 · 원문/닉/제목 미유출 OK")


def test_gold_evidence_recoverable_from_input():
    """시드 레코드의 필드별 evidence 가 원문의 부분문자열이다(공백 정규화 후).

    링크를 다는 순간 사용자는 근거 문구를 원문에서 찾으려 든다. 못 찾으면 신뢰가 되레
    무너진다 — 하필 데모의 유일한 링크에서. 그래서 회수 가능성을 게이트로 건다.
    ‼️ `reviews.evidence` 컬럼이 아니라 `attributes[f]["evidence"]` 다 — 전자는
    `render_review()` 가 합성한 문자열이라 원리적으로 원문에 없다.
    """
    import json
    from pathlib import Path
    from slime_rag.consolidated_view import ATTR_FIELDS

    root = Path(__file__).resolve().parent.parent
    gold = json.loads((root / "eval" / "layer2_gold.json").read_text(encoding="utf-8"))
    norm = lambda s: " ".join((s or "").split())
    misses, checked = [], 0
    for rec in gold["records"]:
        src = norm(rec["input"])
        blocks = [(r.get("mentioned_product"), f, r.get(f))
                  for r in rec["expected"].get("reviews", []) for f in ATTR_FIELDS]
        blocks.append(("(주문단위)", "shipping_cs", rec["expected"].get("shipping_cs")))
        for prod, f, blk in blocks:
            if not (isinstance(blk, dict) and blk.get("evidence")):
                continue
            checked += 1
            if norm(blk["evidence"]) not in src:
                misses.append(f"{rec['id']} {prod}/{f}: {blk['evidence']!r}")
    assert checked, "검사할 evidence 가 하나도 없다 — 골드가 비었나?"
    assert not misses, "원문에서 회수 불가한 evidence:\n  " + "\n  ".join(misses)
    print(f"✓ evidence 회수 가능성: {checked}/{checked} 이 원문의 부분문자열 OK")


if __name__ == "__main__":
    test_permalink_branches()
    test_build_source_ref()
    test_thread_comments_distinct()
    test_embed_url_gate()
    test_group_evidence_sources()
    test_parse_comments_keeps_id()
    test_captions_state_both_reasons()
    test_source_ref_stores_addresses_only()
    test_gold_evidence_recoverable_from_input()
    print("\n원문 링크 정책 오프라인 테스트 통과 ✅")
