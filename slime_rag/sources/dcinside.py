# -*- coding: utf-8 -*-
"""디시인사이드 아모스 갤 수집 (2층 백본) — 본문 + 댓글(AJAX).

[ADJUST] GALLERY_ID, 검색 URL 파라미터, 셀렉터는 라이브 사이트로 검증할 것.
댓글은 e_s_n_o 토큰 자동추출 후 AJAX(`_GALLTYPE_=M`) — 2026-06-17 라이브 검증 완료.
댓글 식별자 — 2026-08-06 라이브 검증: 고유 id 키는 `no`, 스레드번호는 `parent`(대댓글 부모는 `c_no`).
**점프 앵커는 없음**: 댓글이 AJAX 렌더라 서버 HTML 에 댓글 id·`comment_li`·`#comment` 가 전무 →
원문 링크는 앵커 없이 스레드 URL 로 간다(`source_links` 참조). id 는 옵션으로 보존만.

[결정 2026-08-09] **이 경로도 가공 전에 원문을 남긴다**(`rawstore`, kind `dc_thread`).
오래 인스타 경로만 저장소를 가졌다 — 디시는 액터가 아니라 직접 HTTP 라 '당장 나가는 돈'이
없어서 밀렸다. 그런데 이 경로의 유료 단계는 수집이 아니라 **추출**이고, 저장이 없으면
추출 규칙을 고칠 때마다 HTTP 부터 다시 밟아야 한다(인스타가 `from_raw=True` 로 건너뛰는 그 단계).
게다가 갤러리는 변한다 — 글이 지워지고 댓글이 붙는다. 그래서 '공짜니 다시 받으면 된다'가
성립하지 않는다: 지워진 글은 재수집으로 못 되찾는다.
저장 단위는 **스레드 하나**이고 저장 시점은 **응답 직후**다(`_candidates_for` 안) — 값이 나가는
단위가 스레드이므로, 키워드 루프 끝에 몰아 저장하면 중간에 죽을 때 이미 받은 스레드가 통째로 날아간다.
"""

from __future__ import annotations
from typing import Iterator, Optional
from pathlib import Path
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
                 gall_type: str = "M",             # [ADJUST] 마이너갤=M (라이브 검증)
                 raw_kind: str = "dc_thread",
                 save_raw: bool = True,
                 raw_root: Optional[Path] = None):
        self.gid = gallery_id
        self.board = "mgallery/board" if is_minor else "board"
        self.throttle = Throttle(min_interval)
        self.classify_fn = classify_fn
        self.include_comments = include_comments
        self.comment_pages = comment_pages
        self.gall_type = gall_type
        self.raw_kind = raw_kind
        # `save_raw=False` 는 테스트·스모크용이다. 라이브 수집에서 끄지 말 것 — 지워진 글은
        # 재수집으로 못 되찾으므로, 안 남긴 스레드는 영구 유실이다.
        self.save_raw = save_raw
        self.raw_root = raw_root          # 테스트가 임시 디렉터리를 주입하는 자리(기본 settings.raw_dir)
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

    def _fetch_comment_payloads(self, no: str, referer: str, token: str) -> list[dict]:
        """댓글 AJAX 응답을 **파싱 전 JSON 그대로** 페이지 순서로 돌려준다.

        `_parse_comments` 를 여기서 적용하지 않는 이유가 `rawstore` 의 존재 이유와 같다 —
        디시콘/빈 댓글 제외는 **처리 규칙**이고, 규칙이 바뀌면 원문에서 다시 뽑아야 한다.
        파싱한 결과만 남기면 그 재처리가 다시 HTTP 부터 시작된다.

        ⚠️ 텍스트 댓글이 0인 페이지도 **저장은 하고** 거기서 페이징을 끊는다. 저장을 건너뛰면
          '그 페이지를 안 받았다'와 '받았는데 디시콘뿐이었다'가 구분되지 않는다 — 봉투가
          0건 런도 남기는 것과 같은 이유다.
        """
        out, headers = [], {"X-Requested-With": "XMLHttpRequest", "Referer": referer}
        for cpage in range(1, self.comment_pages + 1):
            data = {"id": self.gid, "no": no, "cmt_id": self.gid, "cmt_no": no,
                    "e_s_n_o": token, "comment_page": cpage, "_GALLTYPE_": self.gall_type}
            self.throttle.wait()
            try:
                r = self.s.post(self.COMMENT_URL, data=data, headers=headers, timeout=10)
                payload = r.json()
            except Exception as e:
                log.warning("댓글 로드 실패(no=%s): %s", no, e)
                break
            out.append(payload)
            if not self._parse_comments(payload):
                break
        return out

    def _comments_from_payloads(self, payloads: list[dict]) -> list[dict]:
        """페이지별 원문 JSON → 텍스트 댓글 평탄 목록. 라이브·재처리가 **공유**하는 유일한 경로."""
        out: list[dict] = []
        for payload in payloads:
            out.extend(self._parse_comments(payload))
        return out

    def _fetch_comments(self, no: str, referer: str, token: str) -> list[dict]:
        """수집 + 파싱 한 번에(호환 유지 — `evals/seed_dcinside_relevance.py`). 저장은 안 한다."""
        return self._comments_from_payloads(
            self._fetch_comment_payloads(no, referer, token))

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

    # ---- 원문 스냅샷 ----
    RAW_NOTE = ("디시 스레드 원문 스냅샷 — 파싱 전 HTML + 댓글 AJAX JSON 그대로. "
                "재처리는 이 파일에서 하고 갤러리를 다시 긁지 않는다(slime_rag/sources/dcinside.py).")

    def _save_raw(self, purl: str, kw: str, phtml: str, payloads: list[dict]) -> None:
        """스레드 1건의 원문을 디스크에 남긴다 — **처리 이전, 응답 직후**.

        키는 **글번호**다(인스타 해시태그 경로가 요청 태그를 키로 쓰는 것과 다르다).
        여기선 값이 나가는 단위도, 사이트에서 사라질 수 있는 단위도 스레드이기 때문이다.
        요청 키워드는 봉투의 `requested.keyword` 로 남으므로 '어느 앵커로 닿았나'는 보존된다
        (재처리 선택이 그 값을 읽는다). 대신 '이 키워드는 0건'이라는 사실은 이 저장소가 아니라
        수집 로그·게이트 카운터가 갖는다 — 목록 단계의 사실이라 스레드 봉투에는 담을 자리가 없다.

        ⚠️ 저장 실패로 수집을 중단하지 않는다. 이미 받아 둔 응답은 메모리에서 계속 쓴다
          (인스타 경로와 같은 규칙) — 디스크 문제로 이번 런의 처리까지 잃을 이유는 없다.
        ⚠️ 봉투의 `usage_total_usd` 는 **None** 이다. 이 경로는 액터가 아니라 직접 HTTP 라
          건당 청구가 없다 — 0.0 으로 적으면 '공짜'가 아니라 '측정해서 0'처럼 읽힌다.
        """
        if not self.save_raw:
            return
        from .. import rawstore

        m = self._NO_RE.search(purl)
        if m is None:
            # 글번호 없는 URL 은 `_parse_list` 가 안 내보내지만(숫자 no 만 통과) 재방문 인자로는
            # 들어올 수 있다. 조용히 안 남기지 않는다 — 남기되 키를 분리하고 경고한다.
            log.warning("글번호를 못 읽어 스레드 키를 분리한다: %s", purl)
        no = m.group(1) if m else "_no_thread_no"
        items: list[dict] = [{"id": f"{no}:post", "type": "post", "thread_no": no,
                              "url": purl, "html": phtml}]
        for i, payload in enumerate(payloads, start=1):
            items.append({"id": f"{no}:cmt:{i}", "type": "comments", "thread_no": no,
                          "comment_page": i, "payload": payload})
        try:
            rawstore.save_run(items, actor=f"dcinside:{self.gid}", kind=self.raw_kind, key=no,
                              requested={"url": purl, "keyword": kw, "gallery": self.gid,
                                         "comment_pages": self.comment_pages},
                              usage_usd=None, note=self.RAW_NOTE, root=self.raw_root)
        except OSError as e:
            log.error("원문 저장 실패(no=%s) — 메모리 결과는 계속 사용: %s", no, e)

    def _candidates_for(self, purl: str, kw: str) -> list[RawReview]:
        """글 1건 → 관련성 게이트에 넣을 후보 배치(본문 + 댓글). 상세·댓글 HTTP 가 여기서 난다.

        글 단위로 배치를 잡는 건 상한 도달 시 다음 글의 댓글 요청을 아끼기 위한
        책임 수집(D9) 균형점이다 — 페이지 전체를 모으면 그 절약이 사라진다.

        순서가 규칙이다: **받기 → 저장 → 만들기.** 파싱(`_build_candidates`)이 예외로 죽어도
        방금 받은 원문은 디스크에 남아 있어야 한다 — 저장을 뒤로 미루면 셀렉터 하나 깨진 날
        그 런의 HTTP 가 통째로 헛일이 된다.
        """
        phtml = get(self.s, purl, self.throttle)
        if not phtml:
            return []
        payloads: list[dict] = []
        if self.include_comments:
            token = self._extract_token(phtml)
            m = self._NO_RE.search(purl)
            if token and m:
                payloads = self._fetch_comment_payloads(m.group(1), purl, token)
        self._save_raw(purl, kw, phtml, payloads)
        return self._build_candidates(purl, kw, phtml, payloads)

    def _build_candidates(self, purl: str, kw: str, phtml: str,
                          payloads: list[dict]) -> list[RawReview]:
        """원문(HTML + 댓글 JSON) → `RawReview` 후보. **네트워크를 모른다.**

        라이브 수집과 디스크 재처리가 **이 함수 한 벌**을 공유한다. 재처리 쪽에서 dict 를 따로
        풀면 `meta` 모양이 갈려 하류(`extract.thread_key`·`source_links.build_source_ref`·
        `index.post_columns`)가 소스마다 다른 값을 받는다 — 인스타 재처리가
        `_post_to_seller_review` 를 공유하는 것과 같은 규칙이다.
        """
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
            m = self._NO_RE.search(purl)
            if m:
                for i, c in enumerate(self._comments_from_payloads(payloads)):
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

    # ---- 디스크 재처리 (HTTP 0회) ----
    def _raw_threads(self, keywords: list[str] | None = None,
                     threads: list[str] | None = None) -> Iterator[tuple[str, dict, list[dict], list[str]]]:
        """저장된 스레드를 `(글번호, 글 아이템, 댓글 payload 목록, 닿은 키워드)` 로 돌려준다.

        런 병합 규칙은 `rawstore.latest_items` 와 같다 — 같은 `id` 는 **나중 캡처가 이긴다**.
        재방문으로 댓글이 늘어난 스레드는 최신 캡처가 더 두껍기 때문이다.
        ⚠️ 댓글 페이지는 **id 가 페이지 번호를 포함**한다(`<no>:cmt:<page>`). 나중 런이 더 얕게
          돌면(`comment_pages` 를 줄이면) 옛 런의 뒷페이지가 그대로 남아 섞일 수 있다 —
          유실보다 낫다는 판단이고, 중복 댓글은 하류의 `comment_no` 키(`pipeline.dc_post_id`)와
          `UNIQUE(source, post_id, product)` 가 접는다.
        """
        from .. import rawstore

        want_kw = {k for k in (keywords or []) if k}
        want_no = {str(t) for t in (threads or [])}
        for key in rawstore.iter_keys(self.raw_kind, root=self.raw_root):
            if want_no and key not in want_no:
                continue
            merged: dict[str, dict] = {}
            kws: list[str] = []
            for doc in rawstore.load_runs(self.raw_kind, key, root=self.raw_root):
                kw = (doc.get("requested") or {}).get("keyword")
                if kw and kw not in kws:
                    kws.append(kw)
                for it in (doc.get("items") or []):
                    iid = it.get("id")
                    if iid:
                        merged[iid] = it
            # 키워드 필터는 **닿은 앵커** 기준이다. 저장 키가 글번호라 키워드는 봉투에만 있고,
            # 한 스레드가 여러 키워드로 닿았으면 그중 하나만 겹쳐도 대상이다.
            if want_kw and not (want_kw & set(kws)):
                continue
            post = next((it for it in merged.values() if it.get("type") == "post"), None)
            if not post or not post.get("html"):
                log.warning("원문에 글 HTML 이 없다 — 건너뜀(no=%s)", key)
                continue
            payloads = [it["payload"] for it in
                        sorted((it for it in merged.values()
                                if it.get("type") == "comments" and it.get("payload") is not None),
                               key=lambda x: x.get("comment_page") or 0)]
            yield key, post, payloads, kws

    def collect_from_raw(self, keywords: list[str], limit: int = 100,
                         target: dict | None = None,
                         threads: list[str] | None = None) -> Iterator[RawReview]:
        """`collect` 의 디스크 쌍둥이 — **HTTP 0회**. 게이트·후보 생성은 같은 코드가 돈다.

        `keywords` 는 검색어가 아니라 **필터**다: 그 앵커로 닿아 저장된 스레드만 고른다
        (빈 목록이면 저장소 전량). 관련성 게이트는 로컬 임베딩이라 재적용이 무료이고,
        재적용해야 라이브 런과 같은 후보 집합이 나온다.
        """
        gate = RelevanceGate(self.platform, target, keywords, limit, log)
        self.last_gate = gate
        n = 0
        for key, post, payloads, kws in self._raw_threads(keywords, threads):
            if gate.should_stop():
                gate.finish()
                log.info("원문 재처리: 스레드 %d건 처리 후 게이트 예산 소진", n)
                return
            purl = post.get("url") or f"{self.BASE}/{self.board}/view/?id={self.gid}&no={key}"
            # 키워드는 `meta["keyword"]` 로 하류까지 간다 — 저장 당시 닿은 앵커를 쓴다
            # (여러 개면 첫 번째). 없으면 요청 필터의 첫 값, 그것도 없으면 빈 문자열.
            kw = kws[0] if kws else (keywords[0] if keywords else "")
            n += 1
            yield from gate.filter(self._build_candidates(purl, kw, post["html"], payloads))
        gate.finish()
        log.info("원문 재처리: 스레드 %d건 (HTTP 0회)", n)
