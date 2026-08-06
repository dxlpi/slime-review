# -*- coding: utf-8 -*-
"""
원문 링크 정책 — 식별자(`reviews.source_ref`) → 원문 URL / 임베드 URL / 근거 목록 그룹핑.

왜 별도 모듈인가: "화면에 뜬 근거는 원문으로 클릭 도달 가능해야 한다"는 정책이고,
`app/ui.py` 는 표시 전용이어야 한다. DB·네트워크·Streamlit 을 일절 import 하지 않는
순수 함수만 두어, 무비용 CI 오프라인 게이트가 정책을 통째로 커버한다.

왜 렌더된 URL(TEXT)이 아니라 식별자(JSONB)를 저장하나: 디시 댓글 앵커 형식이
미확인(아래)이라 URL 을 구워두면 앵커 판단이 바뀔 때 재수집 없이 복구할 수 없다.
`relevance_meta` 가 문장이 아니라 구조화 판정을 저장하는 것과 같은 이유.

`source_ref` 모양(없는 키는 생략): {"platform","url","thread_no","comment_no","shortcode"}

⚠️ 디시 댓글 앵커 — 2026-08-06 라이브 확인 결과
  · 댓글 고유 id 는 확보된다(댓글 JSON 의 `no`, 스레드번호는 `parent`).
  · 그러나 게시물 HTML 에 per-comment 앵커가 **없다**(댓글은 AJAX 로 클라이언트가 그린다 —
    서버가 준 HTML 에 댓글 id 문자열이 아예 없고 `comment_li`/`#comment` 앵커도 0건).
    비동기로 삽입된 노드에는 브라우저가 프래그먼트 점프를 다시 걸어주지 않는다.
  → 안정 앵커가 없으므로 **앵커를 붙이지 않는다**(가짜 앵커 금지 — 틀린 링크는 링크 없음보다 나쁘다).
    댓글도 스레드 URL 로 간다. `comment_no` 는 `source_ref` 에 남아, 나중에 앵커가 확인되면
    재수집 없이 켤 수 있다 — 이 컬럼이 산 것은 배송된 앵커가 아니라 그 옵션 가치다.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# 인스타 게시물 경로 → shortcode. /p/(사진·캐러셀) /reel/(릴스) /tv/(IGTV) 셋만 임베드 대상.
_IG_PATH_RE = re.compile(r"^/(?:p|reel|tv)/([A-Za-z0-9_-]+)/?$")
# 디시 게시물 URL 의 글번호.
_DC_NO_RE = re.compile(r"[?&]no=(\d+)")

_HTTP_SCHEMES = ("http", "https")


def _clean_url(url) -> str | None:
    """저장된 url 문자열 → 표시·이동 가능한 URL. 형태가 어긋나면 None(링크 없음으로 degrade).

    프래그먼트는 통째로 벗긴다. 수집기가 모든 댓글 URL 에 굽는 `#cmt` 는 그 자리에
    아무것도 없는 **가짜 앵커**라서(위 라이브 확인) 그대로 내보내면 클릭이 아무 데도
    도달하지 않는다. 앵커는 확인된 것만, 이 모듈이 조립한다 — 저장된 url 은 항상 맨몸이다.
    """
    if not isinstance(url, str):
        return None
    url = url.strip().split("#", 1)[0]
    if not url:
        return None
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in _HTTP_SCHEMES or not p.netloc:
        return None
    return url


def permalink(source_ref: dict | None) -> str | None:
    """식별자 → 원문 URL. 없거나 형태가 어긋나면 None — 호출부는 '링크 없음'으로 degrade."""
    if not isinstance(source_ref, dict):
        return None
    return _clean_url(source_ref.get("url"))


def is_comment(source_ref: dict | None) -> bool:
    """이 조각이 댓글인가. 댓글 id(`comment_no`)나 그 폴백(`ordinal`)이 있으면 댓글.

    `ordinal`(스레드 내 순번)은 댓글 id 를 못 잡은 경우의 폴백 필드다. 이름을 분리해 둔 건
    앵커 조립에 절대 쓰이지 않게 하기 위함 — 순번은 식별자지 주소가 아니다.
    """
    if not isinstance(source_ref, dict):
        return False
    return source_ref.get("comment_no") is not None or source_ref.get("ordinal") is not None


def evidence_group_key(source_ref: dict | None) -> tuple | None:
    """조각 식별자 튜플 — 근거 목록의 중복 제거 키. 링크 불가면 None.

    `source_ref` 는 조각 단위 속성이라 제품별 팬아웃 행마다 복제돼 있다. 같은 조각의
    복제 행들을 여기서 하나로 접는다. 그룹핑(표시)은 렌더된 URL 로 하지만(앵커가 없어
    한 스레드가 한 줄로 묶인다), 중복 제거는 **식별자**로 해야 같은 스레드의 서로 다른
    댓글 20개가 1건으로 소실되지 않는다.
    """
    url = permalink(source_ref)
    if not url:
        return None
    platform = source_ref.get("platform") or "unknown"
    piece = source_ref.get("comment_no")
    if piece is None:
        piece = source_ref.get("ordinal")
    if piece is None:
        piece = source_ref.get("shortcode")
    return (platform, url, piece if piece is None else str(piece))


# ---------------------------------------------------------------- 식별자 조립(적재 경로)
def build_source_ref(platform: str, url: str | None, meta: dict | None = None) -> dict | None:
    """수집 결과(url + meta) → `source_ref`. 링크 불가면 None(무음 갭 금지: 호출부가 센다).

    · dcinside 게시물: thread_no 를 URL 의 `no=` 에서 파싱한다 — 게시물 RawReview 의 meta 엔
      `parent_no` 가 없다(댓글에만 있다). 여기가 두 경로의 유일한 차이다.
    · dcinside 댓글: meta 의 comment_no(댓글 JSON `no`) + thread_no(=parent_no).
      comment_no 가 없으면 순번을 `ordinal` 로 받아 둔다 — 이름을 분리해 앵커 조립에 안 쓰이게.
    · instagram: shortcode. 없어도 url 만으로 링크는 성립한다(shortCode 널 구멍 방어).
    없는 키는 넣지 않는다 — `{...: None}` 이 쌓이면 식별자 비교가 지저분해진다.
    """
    clean = _clean_url(url)
    if not clean:
        return None
    meta = meta or {}
    ref: dict = {"platform": platform, "url": clean}
    if platform == "dcinside":
        thread_no = meta.get("thread_no") or meta.get("parent_no")
        if not thread_no:
            m = _DC_NO_RE.search(clean)
            thread_no = m.group(1) if m else None
        if thread_no:
            ref["thread_no"] = str(thread_no)
        if meta.get("comment_no") is not None:
            ref["comment_no"] = str(meta["comment_no"])
        elif meta.get("type") == "comment" and meta.get("ordinal") is not None:
            ref["ordinal"] = int(meta["ordinal"])
    elif platform == "instagram":
        if meta.get("shortcode"):
            ref["shortcode"] = str(meta["shortcode"])
    return ref


# ---------------------------------------------------------------- 임베드 게이트(1층 스펙 전용)
def embed_url(spec: dict | None) -> str | None:
    """1층 공식 스펙 → 판매자 인스타 게시물 임베드 URL. 게이트 전부 통과해야 non-None.

    ① `source_permalink` 존재 — layer1 폴백 제거 후(ADR-0009) '있으면 무조건 정확'이라
       null 이냐 아니냐만 보면 된다. ② 호스트가 instagram.com. ③ shortcode 파싱 성공.
    하나라도 실패하면 None → 호출부는 텍스트 링크만 그린다(fail-closed).

    임베드 대상은 **판매자 본인 게시물(1층 공식 스펙)뿐**이다. 유저 후기 미디어는 어떤
    경로로도 임베드하지 않는다. 디시는 임베드 엔드포인트가 없고 CDN 핫링크는 무재배포
    원칙 위반이라 텍스트 링크 카드로만 간다.

    ⚠️ `/p/{shortcode}/embed` 는 **문서화되지 않은** 엔드포인트다(공식 oEmbed API 는
    App Review 벽 — ADR-0003 과 같은 벽). 예고 없이 깨질 수 있다. 2026-08-06 확인:
    HTTP 200, `X-Frame-Options` 없음, CSP 에 `frame-ancestors` 없음 → 서버측 프레이밍 차단 없음.
    """
    if not isinstance(spec, dict):
        return None
    url = _clean_url(spec.get("source_permalink"))
    if not url:
        return None
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        return None
    m = _IG_PATH_RE.match(p.path if p.path.startswith("/") else "/" + p.path)
    if not m:
        return None
    return f"https://www.instagram.com/p/{m.group(1)}/embed"


# ---------------------------------------------------------------- 근거 원문 목록(요약 탭)
def group_evidence_sources(records: list[dict]) -> list[dict]:
    """근거 레코드 → 원문 링크 목록(표시용). 링크 없는 레코드는 조용히 빠진다(호출부가 캡션으로 고지).

    중복 제거는 **식별자**(`evidence_group_key`)로, 표시 그룹핑은 **렌더된 URL** 로 한다.
    앵커가 없어 한 스레드의 게시물 행과 댓글 행이 같은 URL 로 묶이므로, 라벨을 '댓글 N건'
    이라고만 쓰면 안 된다 — 그 그룹은 댓글만이 아니다. 라벨도 귀속 정확성의 일부다.

    반환 항목: {"url", "platform", "n_pieces", "n_posts", "n_comments", "label"}.
    입력 순서(=검색·집계 순서)를 그대로 유지한다.
    """
    groups: dict[str, dict] = {}
    seen: set[tuple] = set()
    for rec in records or []:
        ref = (rec or {}).get("source_ref")
        key = evidence_group_key(ref)
        if key is None or key in seen:
            continue                                   # 팬아웃 복제 행 → 한 조각으로 접는다
        seen.add(key)
        url = key[1]
        g = groups.setdefault(url, {"url": url, "platform": ref.get("platform") or "unknown",
                                    "n_pieces": 0, "n_posts": 0, "n_comments": 0})
        g["n_pieces"] += 1
        if is_comment(ref):
            g["n_comments"] += 1
        else:
            g["n_posts"] += 1
    out = []
    for g in groups.values():
        out.append({**g, "label": _group_label(g)})
    return out


def _group_label(g: dict) -> str:
    """그룹 구성 라벨 — 실제 구성대로. 글만/댓글만/섞임을 뭉뚱그리지 않는다."""
    parts = []
    if g["n_posts"]:
        parts.append(f"글 {g['n_posts']}건")
    if g["n_comments"]:
        parts.append(f"댓글 {g['n_comments']}건")
    return " + ".join(parts) or f"조각 {g['n_pieces']}건"


# ---------------------------------------------------------------- 표시 문구(정직성 캡션)
# evidence 는 원문 인용이 아니라 구조화 필드의 렌더링(index.render_review)이다.
# 링크를 다는 순간 사용자는 근거 문구를 원문에서 찾으려 든다 — 못 찾는 게 정상임을 먼저 밝힌다.
EVIDENCE_NOT_QUOTE_CAPTION = (
    "근거 문구는 원문 인용이 아니라 추출된 항목의 구조화 요약입니다 — 원문은 링크로 확인하세요."
)

# 후기 건수(행수)와 근거 목록 건수가 다른 두 가지 이유를 **둘 다** 밝힌다.
# 하나만 쓰면 오도한다: 팬아웃만 쓰면 댓글 묶임이 숨고, 묶임만 쓰면 팬아웃이 숨는다.
EVIDENCE_COUNT_CAPTION = (
    "후기 건수는 제품별 행 수라 한 글이 여러 건으로 세어질 수 있고, 근거 목록은 원문 조각 수입니다. "
    "디시는 댓글 앵커가 없어 같은 스레드의 글·댓글이 한 줄로 묶입니다."
)

# 제3자 iframe 은 열람자 브라우저에 인스타 쿠키를 심고, 브라우저가 서드파티 프레임을 차단하면
# 빈 박스가 되는데 서버에선 감지할 수 없다. 그래서 임베드 아래 텍스트 링크를 항상 남긴다.
EMBED_PRIVACY_CAPTION = (
    "판매자 공식 게시물 임베드 — 인스타그램이 직접 제공(원문 미재배포). "
    "제3자 프레임이라 열람자 브라우저에 인스타 쿠키가 설정되며, 브라우저·확장 프로그램이 "
    "프레임을 차단하면 빈 칸으로 보일 수 있습니다(아래 링크로 원문 확인)."
)
