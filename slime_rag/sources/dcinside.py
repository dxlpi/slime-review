# -*- coding: utf-8 -*-
"""디시인사이드 아모스 갤 수집 (2층 백본) — 본문 + 댓글(AJAX).

[ADJUST] GALLERY_ID, 검색 URL 파라미터, 셀렉터는 라이브 사이트로 검증할 것.
댓글은 e_s_n_o 토큰 자동추출 후 AJAX(`_GALLTYPE_=M`) — 2026-06-17 라이브 검증 완료.
댓글 식별자 — 2026-08-06 라이브 검증: 고유 id 키는 `no`, 스레드번호는 `parent`(대댓글 부모는 `c_no`).
**점프 앵커는 없음**: 댓글이 AJAX 렌더라 서버 HTML 에 댓글 id·`comment_li`·`#comment` 가 전무 →
원문 링크는 앵커 없이 스레드 URL 로 간다(`source_links` 참조). id 는 옵션으로 보존만.
"""

from __future__ import annotations
from typing import Iterator
from urllib.parse import urlencode
import re

import requests
from bs4 import BeautifulSoup

from .base import (
    RawReview, Source, Throttle, RelevanceGate, robots_allowed, get,
    is_low_quality, strip_chrome, toxic_via_llm, log,
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
        self.last_gate = None                 # 마지막 collect 의 관련성 게이트(호출부 관측성)
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
        t = re.sub(r"https?://\S+", "", text or "")   # 링크 제거
        return strip_chrome(t)                        # 뉴스 위젯·앱 푸터·멘션 (base.strip_chrome)

    # ---- 댓글 (AJAX) ----
    COMMENT_URL = "https://gall.dcinside.com/board/comment/"   # [ADJUST] 라이브 검증
    _TOKEN_RE = re.compile(r'name=["\']e_s_n_o["\']\s+value=["\']([^"\']+)["\']')
    _NO_RE = re.compile(r"[?&]no=(\d+)")

    def _above_watermark(self, url: str, min_thread_no: int) -> bool:
        """증분 컷 — 이 글을 상세 조회할 가치가 있는가.

        글번호를 못 읽으면 **통과시킨다**(fail-open). 파싱 실패로 새 글을 조용히 버리는 것보다
        한 번 더 받아 보는 쪽이 낫다 — 어차피 Phase 1 의 조각 단위 컷이 중복을 잡는다.

        ⚠️ 재방문 대상을 위한 예외는 **없다.** 재방문은 키워드 루프 전에 직접 조회해 `seen` 에
          넣으므로 목록 필터가 이미 걸러낸다. 여기 예외 목록을 다시 두면 robots 로 거부한 글이
          그 예외로 살아남아 결국 받아진다 — 판단해 놓고 다른 문으로 들여보내는 꼴이다.
        """
        if (m := self._NO_RE.search(url)) is None:
            return True
        return int(m.group(1)) > min_thread_no

    def _extract_token(self, html: str):
        m = self._TOKEN_RE.search(html or "")
        return m.group(1) if m else None

    def _parse_comments(self, j: dict) -> list[dict]:
        """댓글 JSON → 텍스트 댓글만. 디시콘/보이스/빈 댓글은 제외.

        `no` = 댓글 고유 id (2026-08-06 라이브 확인: 댓글 JSON 키는 `no`, 스레드번호는 `parent`,
        대댓글의 부모 댓글은 `c_no`). 이걸 보존해야 원문 링크가 스레드 안에서 조각을 구분한다 —
        `post_id` 에 들어가는 건 댓글 id 가 아니라 런 전체의 enumerate 위치라 복구 불가.
        ⚠️ 다만 **점프 앵커는 없다**: 댓글은 AJAX 로 클라이언트가 그려서 서버 HTML 에 댓글 id
        문자열도 `comment_li`/`#comment` 앵커도 없다(같은 날 확인). 그래서 `source_links` 는
        앵커를 붙이지 않고 스레드 URL 로 간다 — id 는 나중에 켤 옵션으로 보존만 한다.
        """
        out = []
        for c in (j.get("comments") or []):
            memo = c.get("memo")
            if not memo:
                continue
            txt = BeautifulSoup(memo, "html.parser").get_text(" ", strip=True)
            if not txt:                              # 이미지(디시콘) 댓글 → 텍스트 없음
                continue
            out.append({"text": txt, "name": c.get("name"), "no": c.get("no"),
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
                max_pages: int = 5, target: dict | None = None,
                min_thread_no: int | None = None,
                revisit_threads: list[int] | None = None) -> Iterator[RawReview]:
        """`min_thread_no`: 이 글번호 **이하**는 상세 요청을 보내지 않는다(증분 수집).

        컷이 상세 요청(`get(self.s, purl, ...)`) **앞**에 있어야 HTTP 도 아낀다 — 목록 파싱은
        어차피 한 페이지 값이고, 값이 나가는 건 글마다 도는 상세·댓글 요청이다.
        미지정이면 필터가 통째로 꺼져 기존과 동일하게 돈다(하위호환).

        `revisit_threads`: 워터마크보다 옛 글이라도 **다시 볼 글번호** 목록(선택).
        ⚠️ 워터마크는 **새 글**만 아낀다. 새 댓글은 옛 글에 달리므로 워터마크로는 영영 안 잡힌다
          — 그래서 재방문 대상은 자동 선정하지 않고 호출부가 **명시 인자**로 준다.
          즉 이 경로는 새 댓글 수집의 HTTP 비용을 계속 낸다(의도된 절충).
        ⚠️ 이 글번호들은 **검색 목록을 거치지 않고 직접 조회한다.** 워터마크 예외로만 두면
          그 글이 이번 키워드 검색 결과에 없을 때 영영 안 닿는다 — 이름이 약속하는 '재방문'이
          되려면 URL 을 직접 만들어 받아야 한다.
        """
        # 관련성 게이트(2층, D8): emitted 대신 relevant/examined 카운터. 비관련(뉴스 블리드·
        # 랜박 잡담·오프토픽 댓글)은 yield/카운트 없이 계속 페이징. 상한(D9) 도달 시 조기 종료.
        # target 미주입이면 keywords[0]을 slime 앵커로 폴백(하위호환); 앵커 없으면 패스스루.
        gate = RelevanceGate(self.platform, target, keywords, limit, log)
        # 호출부 관측성 — 예산 초과 미처리 건수를 파이프라인 카운트로 올린다(침묵 절단 금지).
        self.last_gate = gate
        # 중복 제거 — 재방문 목록은 대개 `SELECT thread_no FROM reviews …` 로 만들어져 같은 글이
        # 여러 번 들어온다. 두 번 받으면 상세·댓글 HTTP 가 두 배로 나가고, 하류에서 같은 글의
        # RawReview 가 둘이 되어 스레드 그룹핑이 하나를 덮어쓴다. 조용히 줄이지 않고 **센다**.
        revisit = list(dict.fromkeys(str(n) for n in (revisit_threads or [])))
        if (dupes := len(revisit_threads or []) - len(revisit)):
            log.info("재방문 대상 중복 %d건 제거(%d건 조회)", dupes, len(revisit))
        seen = set()

        # ⚠️ robots 는 재방문 경로에도 적용한다. 목록을 거치던 예전 경로는 페이지마다
        #   `robots_allowed` 를 물었으므로, 상세 URL 로 직행하면서 그 확인을 건너뛰면
        #   **책임 수집 규칙이 조용히 느슨해진다**(`base.get` 은 robots 를 보지 않는다).
        #   판정은 경로 단위라 재방문 URL 전부가 같은 답을 받는다 → **한 번만 묻는다**
        #   (`robots_allowed` 는 캐시가 없어 부를 때마다 robots.txt 를 새로 받는다).
        if revisit and not robots_allowed(
                f"{self.BASE}/{self.board}/view/?id={self.gid}&no={revisit[0]}",
                self.s.headers["User-Agent"]):
            log.info("robots 비허용 → 재방문 %d건 통째로 스킵", len(revisit))
            revisit = []

        # 재방문 먼저 — 검색 목록과 무관하게 닿아야 한다. 페이징 예산을 쓰기 전에 처리해서
        # 명시적으로 지정한 대상이 상한에 밀려 빠지지 않게 한다.
        for no in revisit:
            if gate.should_stop():
                gate.finish()
                return
            purl = f"{self.BASE}/{self.board}/view/?id={self.gid}&no={no}"
            seen.add(purl)
            yield from gate.filter(self._candidates_for(purl, keywords[0] if keywords else ""))

        for kw in keywords:
            for page in range(1, max_pages + 1):
                if gate.should_stop():
                    gate.finish()
                    return
                list_url = self._list_url(kw, page)
                if not robots_allowed(list_url, self.s.headers["User-Agent"]):
                    log.info("robots 비허용 → 스킵: %s", list_url)
                    break
                html = get(self.s, list_url, self.throttle)
                if not html:
                    break
                post_urls = [u for u in self._parse_list(html) if u not in seen]
                if min_thread_no is not None:
                    # 상세 요청 **앞**의 컷 — 여기서 걸러야 글당 상세+댓글 HTTP 가 통째로 빠진다.
                    post_urls = [u for u in post_urls if self._above_watermark(u, min_thread_no)]
                # ⚠️ 이 `break` 는 '검색 결과 없음'이 아니라 **그 키워드의 페이징 종료**다.
                #    목록이 최신순이라 한 페이지가 통째로 워터마크 아래면 뒤 페이지는 더 옛 글뿐이다 —
                #    증분에서 원하는 동작이지, 고쳐야 할 버그가 아니다.
                if not post_urls:
                    break
                for purl in post_urls:
                    if gate.should_stop():
                        gate.finish()
                        return
                    seen.add(purl)
                    yield from gate.filter(self._candidates_for(purl, kw))
        gate.finish()

    def _candidates_for(self, purl: str, kw: str) -> list[RawReview]:
        """글 1건 → 관련성 게이트에 넣을 후보 배치(본문 + 댓글). 상세·댓글 HTTP 가 여기서 난다.

        글 단위로 배치를 잡는 건 상한 도달 시 다음 글의 댓글 요청을 아끼기 위한
        책임 수집(D9) 균형점이다 — 페이지 전체를 모으면 그 절약이 사라진다.
        """
        phtml = get(self.s, purl, self.throttle)
        if not phtml:
            return []
        title, body, date, pmeta = self._parse_post(phtml)
        text = self._clean(f"{title or ''}\n{body or ''}")
        candidates: list[RawReview] = []
        if not is_low_quality(text):
            toxic = toxic_via_llm(text, self.classify_fn)
            candidates.append(RawReview(
                text=text, url=purl, platform=self.platform,
                posted_at=date, raw_title=title,
                meta={"keyword": kw, "gallery": self.gid, "type": "post",
                      "toxic": toxic, **pmeta},
            ))
        # --- 댓글(후기 다수) ---
        if self.include_comments:
            token = self._extract_token(phtml)
            m = self._NO_RE.search(purl)
            if token and m:
                for i, c in enumerate(self._fetch_comments(m.group(1), purl, token)):
                    ctext = self._clean(c["text"])
                    if is_low_quality(ctext):
                        continue
                    candidates.append(RawReview(
                        text=ctext, url=f"{purl}#cmt", platform=self.platform,
                        posted_at=c.get("date"),
                        # comment_no = 댓글 고유 id, thread_no = 글번호. 원문 링크의
                        # 조각 식별자(source_links.build_source_ref)가 이 둘을 쓴다.
                        # ordinal 은 id 를 못 잡았을 때만 채우는 스레드 내 순번 폴백 —
                        # 이름을 분리해 앵커 조립에 절대 안 쓰이게 한다.
                        meta={"keyword": kw, "gallery": self.gid, "type": "comment",
                              "parent_no": m.group(1), "parent_title": title,
                              "thread_no": m.group(1), "comment_no": c.get("no"),
                              "ordinal": i,
                              # 글 경로와 같은 키 이름으로 싣는다 — index.post_columns
                              # 가 `nick` 하나만 보므로, 여기서 이름이 갈리면 댓글만
                              # 작성자가 조용히 NULL 이 된다(2026-08-06 실측 결손).
                              "nick": c.get("name"),
                              "ip": c.get("ip"),
                              "toxic": toxic_via_llm(ctext, self.classify_fn)},
                    ))
        return candidates
