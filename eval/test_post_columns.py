# -*- coding: utf-8 -*-
"""원문·작성 메타 컬럼 매핑 오프라인 테스트 (ADR-0013) — `index.post_columns` 순수 함수.

DB·네트워크·LLM·모델 미사용(무비용) → CI 게이트 대상.

**왜 이 테스트가 있나**: 2026-08-06 실수집에서 이 매핑이 두 번 **조용히** 비었다.
둘 다 예외 없이 NULL 만 남겨서 카운트를 세보기 전엔 안 보였다.
  1) 디시 `.gall_count` 는 `'조회 428'` 처럼 **라벨이 붙어** 오는데 숫자 파서가 통째로 버렸다
     (`.up_num` 은 순수 숫자라 추천만 들어와 결손이 더 안 보였다).
  2) 댓글 수집기가 작성자를 `name` 으로 담는데 매퍼는 `nick` 만 봐서, 댓글만 작성자가 비었다.
화면(디자인)이 조회·댓글·추천·작성자·작성일을 다 쓰므로 빈 채로 나가면 카드가 무너진다.

실행:  python -m eval.test_post_columns   (repo 루트에서)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag.index import _int, _ts, post_columns          # noqa: E402
from slime_rag.sources.base import RawReview                 # noqa: E402


def test_int_strips_labels_and_separators():
    """라벨·천단위쉼표를 벗기고, 숫자가 없으면 None. `0` 과 '모름'은 구분한다."""
    cases = {
        "조회 428": 428, "댓글 4": 4, "1,234": 1234, "12,345,678": 12345678,
        "0": 0, 15: 15, "-3": -3,
        "ㅇㅇ": None, "": None, None: None, "추천": None,
    }
    bad = {k: (_int(k), v) for k, v in cases.items() if _int(k) != v}
    assert not bad, f"숫자 파싱 불일치(입력: (실제, 기대)): {bad}"
    # 0 을 None 으로 접으면 '0회'와 '모름'이 같아진다 — 회귀 방지로 못박는다.
    assert _int("0") == 0 and _int("없음") is None, "0 과 모름이 구분되지 않는다"
    print("✓ 숫자: 라벨('조회 428')·쉼표 제거 · 0/모름 구분 OK")


def test_ts_rejects_yearless():
    """연도 없는 디시 목록 표기('08.06','14:23')는 **버린다** — 틀린 날짜가 빈 날짜보다 나쁘다."""
    assert _ts("2026-08-06 14:23:45").startswith("2026-08-06T14:23:45")
    assert _ts("2026.07.29 23:52:42").startswith("2026-07-29T23:52:42")
    assert _ts("2026-07-14T03:21:00.000Z") is not None, "인스타 ISO8601(Z) 파싱 실패"
    for junk in ("08.06", "14:23", "", None, "어제"):
        assert _ts(junk) is None, f"연도 없는 값이 통과했다: {junk!r}"
    print("✓ 작성일: 전체 타임스탬프만 수용 · 연도 없는 표기 폐기 OK")


def test_dcinside_post_and_comment():
    """디시 글/댓글 두 경로 모두 작성자가 잡힌다 — 댓글은 `name`, 글은 `nick`."""
    post = RawReview(
        text="제목\n본문", url="https://gall.dcinside.com/x?no=1", platform="dcinside",
        posted_at="2026-07-29 23:52:42", raw_title="제목",
        meta={"nick": "아갤러", "views": "조회 429", "comment_count": "댓글 4",
              "recommend_up": "13", "recommend_down": "0"},
    )
    got = post_columns(post)
    assert got["author"] == "아갤러"
    assert (got["views"], got["comment_count"], got["votes_up"]) == (429, 4, 13)
    assert got["title"] == "제목" and got["body"] == "제목\n본문"

    comment = RawReview(
        text="말차뭉치 - 머드팩 느낌", url="https://gall.dcinside.com/x?no=1#cmt",
        platform="dcinside", posted_at="2026-07-29 23:59:00",
        meta={"type": "comment", "nick": "ㅇㅇ", "parent_title": "제목", "comment_no": "77"},
    )
    got = post_columns(comment)
    assert got["author"] == "ㅇㅇ", "댓글 작성자 결손(수집기 키가 `name`→`nick` 으로 실리는지 확인)"
    assert got["title"] == "제목", "댓글은 부모 글 제목을 물려받아야 카드에 제목이 뜬다"
    # 조회/추천은 **글 단위** 지표라 댓글엔 없다 — 0 이 아니라 None 이어야 한다.
    assert got["views"] is None and got["votes_up"] is None
    print("✓ 디시: 글·댓글 작성자 · 라벨 카운트 · 댓글은 글단위 지표 None OK")


def test_instagram_maps_owner_and_counts():
    ig = RawReview(
        text="캡션 전문 #슬라임", url="https://instagram.com/p/AbC/", platform="instagram",
        posted_at="2026-07-14T03:21:00.000Z",
        meta={"owner_username": "someone", "likes": 321, "comments": 8, "shortcode": "AbC"},
    )
    got = post_columns(ig)
    assert got["author"] == "someone", "인스타 계정명 결손"
    assert (got["likes"], got["comment_count"]) == (321, 8)
    assert got["title"] is None, "인스타에는 제목이 없다 — 없는 걸 지어내면 안 된다"
    assert got["views"] is None and got["votes_up"] is None
    print("✓ 인스타: owner_username · likes/comments · 제목 없음 OK")


def test_missing_raw_yields_all_null():
    """RawReview 가 없는 경로(골드 시드)는 전 컬럼 NULL — 예외로 죽지 않는다."""
    got = post_columns(RawReview(text="", url="", platform="x", meta={}))
    assert got["author"] is None and got["views"] is None and got["posted_at"] is None
    print("✓ 메타 없는 입력: 전 컬럼 None · 예외 없음 OK")


if __name__ == "__main__":
    test_int_strips_labels_and_separators()
    test_ts_rejects_yearless()
    test_dcinside_post_and_comment()
    test_instagram_maps_owner_and_counts()
    test_missing_raw_yields_all_null()
    print("\n원문·작성 메타 매핑 오프라인 테스트 통과 ✅")
