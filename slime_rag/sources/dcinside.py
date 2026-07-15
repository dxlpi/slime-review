# -*- coding: utf-8 -*-
"""디시인사이드 아모스 갤 수집 (2층 백본) — 본문 + 댓글(AJAX).

[ADJUST] GALLERY_ID, 검색 URL 파라미터, 셀렉터는 라이브 사이트로 검증할 것.
댓글은 e_s_n_o 토큰 자동추출 후 AJAX(`_GALLTYPE_=M`) — 2026-06-17 라이브 검증 완료.
"""

from __future__ import annotations
from typing import Iterator
from urllib.parse import urlencode
import re

import requests
from bs4 import BeautifulSoup

from .base import (
    RawReview, Source, Throttle, robots_allowed, get,
    is_low_quality, toxic_via_llm, log,
)


class DCInsideSource(Source):
    """
    아모스 갤 후기 수집 (2층 백본).
    [ADJUST] GALLERY_ID, 검색 URL 파라미터, 셀렉터는 라이브 사이트로 검증할 것.
    """
    platform = "dcinside"
    BASE = "https://gall.dcinside.com"

    def __init__(self, gallery_id: str = "amos",   # 검증됨: 아모스 마이너갤
                 is_minor: bool = True,            # amos = mgallery → True
                 min_interval: float = 2.0,
                 user_agent: str = "slime-rag-research/0.1 (personal portfolio; contact: you@example.com)",
                 classify_fn=None,
                 include_comments: bool = True,    # 이 갤은 후기가 댓글에 많음
                 comment_pages: int = 1,
                 gall_type: str = "M"):            # [ADJUST] 마이너갤=M (라이브 검증)
        self.gid = gallery_id
        self.board = "mgallery/board" if is_minor else "board"
        self.throttle = Throttle(min_interval)
        self.classify_fn = classify_fn
        self.include_comments = include_comments
        self.comment_pages = comment_pages
        self.gall_type = gall_type
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent})

    # --- URL 빌더 ---
    def _list_url(self, keyword: str, page: int) -> str:
        # [ADJUST] 갤 검색 파라미터. 보통 제목+내용 검색은 s_type=search_subject_memo
        q = {"id": self.gid, "page": page,
             "s_type": "search_subject_memo", "s_keyword": keyword}
        return f"{self.BASE}/{self.board}/lists/?{urlencode(q)}"

    # --- 파서 (셀렉터는 [ADJUST] 필수) ---
    def _parse_list(self, html: str) -> list[str]:
        # 검증된 글 링크 패턴: /mgallery/board/view/?id=amos&no=<숫자>&page=..
        view_re = re.compile(r"/board/view/\?id=([^&]+)&(?:amp;)?no=(\d+)")
        soup = BeautifulSoup(html, "html.parser")
        seen, urls = set(), []
        for a in soup.select("a[href*='/board/view/']"):
            m = view_re.search(a.get("href") or "")
            if not m:
                continue
            gid, no = m.group(1), m.group(2)        # no가 숫자인 글만 → 공지(no=notice) 자동 제외
            if gid != self.gid or no in seen:
                continue
            seen.add(no)
            urls.append(f"{self.BASE}/{self.board}/view/?id={gid}&no={no}")
        return urls

    def _parse_post(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        # 제목 ([ADJUST] 미검증 — 목록 제목으로 대체 가능)
        t = soup.select_one(".title_subject")
        title = t.get_text(" ", strip=True) if t else None
        # 본문: .write_div 에서 광고/스크립트 제거 후 텍스트만
        body_el = soup.select_one(".write_div")
        body = ""
        if body_el:
            for junk in body_el.select("script, style, ins, .adsbygoogle"):
                junk.decompose()
            body = body_el.get_text("\n", strip=True)
        # 작성자 헤더(본문용, 댓글 제외): data-loc="view"
        head = soup.select_one(".gall_writer[data-loc='view']")
        date, meta = None, {}
        if head:
            d = head.select_one(".gall_date")
            date = (d.get("title") or d.get_text(strip=True)) if d else None
            meta["nick"] = head.get("data-nick")
            meta["ip"] = head.get("data-ip")
            vc = head.select_one(".gall_count")
            if vc: meta["views"] = vc.get_text(strip=True)
            cc = head.select_one(".gall_comment")
            if cc: meta["comment_count"] = cc.get_text(strip=True)
        up, down = soup.select_one(".up_num"), soup.select_one(".down_num")   # 추천/비추천
        if up:   meta["recommend_up"] = up.get_text(strip=True)
        if down: meta["recommend_down"] = down.get_text(strip=True)
        return title, body, date, meta

    @staticmethod
    def _clean(text: str) -> str:
        t = re.sub(r"\n{3,}", "\n\n", text or "")
        t = re.sub(r"https?://\S+", "", t)          # 링크 제거
        t = re.sub(r"-\s*dc(official)?\s*app.*$", "", t, flags=re.I | re.M)  # 앱 푸터 등
        return t.strip()

    # ---- 댓글 (AJAX) ----
    COMMENT_URL = "https://gall.dcinside.com/board/comment/"   # [ADJUST] 라이브 검증
    _TOKEN_RE = re.compile(r'name=["\']e_s_n_o["\']\s+value=["\']([^"\']+)["\']')
    _NO_RE = re.compile(r"[?&]no=(\d+)")

    def _extract_token(self, html: str):
        m = self._TOKEN_RE.search(html or "")
        return m.group(1) if m else None

    def _parse_comments(self, j: dict) -> list[dict]:
        """댓글 JSON → 텍스트 댓글만. 디시콘/보이스/빈 댓글은 제외."""
        out = []
        for c in (j.get("comments") or []):
            memo = c.get("memo")
            if not memo:
                continue
            txt = BeautifulSoup(memo, "html.parser").get_text(" ", strip=True)
            if not txt:                              # 이미지(디시콘) 댓글 → 텍스트 없음
                continue
            out.append({"text": txt, "name": c.get("name"),
                        "ip": c.get("ip"), "date": c.get("reg_date")})
        return out

    def _fetch_comments(self, no: str, referer: str, token: str) -> list[dict]:
        out, headers = [], {"X-Requested-With": "XMLHttpRequest", "Referer": referer}
        for cpage in range(1, self.comment_pages + 1):
            data = {"id": self.gid, "no": no, "cmt_id": self.gid, "cmt_no": no,
                    "e_s_n_o": token, "comment_page": cpage, "_GALLTYPE_": self.gall_type}
            self.throttle.wait()
            try:
                r = self.s.post(self.COMMENT_URL, data=data, headers=headers, timeout=10)
                page = self._parse_comments(r.json())
            except Exception as e:
                log.warning("댓글 로드 실패(no=%s): %s", no, e)
                break
            if not page:
                break
            out.extend(page)
        return out

    def collect(self, keywords: list[str], limit: int = 100,
                max_pages: int = 5) -> Iterator[RawReview]:
        seen = set()
        emitted = 0
        for kw in keywords:
            for page in range(1, max_pages + 1):
                if emitted >= limit:
                    return
                list_url = self._list_url(kw, page)
                if not robots_allowed(list_url, self.s.headers["User-Agent"]):
                    log.info("robots 비허용 → 스킵: %s", list_url)
                    break
                html = get(self.s, list_url, self.throttle)
                if not html:
                    break
                post_urls = [u for u in self._parse_list(html) if u not in seen]
                if not post_urls:
                    break
                for purl in post_urls:
                    if emitted >= limit:
                        return
                    seen.add(purl)
                    phtml = get(self.s, purl, self.throttle)
                    if not phtml:
                        continue
                    title, body, date, pmeta = self._parse_post(phtml)
                    text = self._clean(f"{title or ''}\n{body or ''}")
                    if is_low_quality(text):
                        continue
                    toxic = toxic_via_llm(text, self.classify_fn)
                    yield RawReview(
                        text=text, url=purl, platform=self.platform,
                        posted_at=date, raw_title=title,
                        meta={"keyword": kw, "gallery": self.gid, "type": "post",
                              "toxic": toxic, **pmeta},
                    )
                    emitted += 1
                    # --- 댓글(후기 다수) ---
                    if self.include_comments and emitted < limit:
                        token = self._extract_token(phtml)
                        m = self._NO_RE.search(purl)
                        if token and m:
                            for c in self._fetch_comments(m.group(1), purl, token):
                                if emitted >= limit:
                                    return
                                ctext = self._clean(c["text"])
                                if is_low_quality(ctext):
                                    continue
                                yield RawReview(
                                    text=ctext, url=f"{purl}#cmt", platform=self.platform,
                                    posted_at=c.get("date"),
                                    meta={"keyword": kw, "gallery": self.gid, "type": "comment",
                                          "parent_no": m.group(1), "parent_title": title,
                                          "ip": c.get("ip"),
                                          "toxic": toxic_via_llm(ctext, self.classify_fn)},
                                )
                                emitted += 1
