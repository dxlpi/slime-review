# -*- coding: utf-8 -*-
"""
슬라임 RAG — 수집 레이어 (플러그인 구조)

설계 의도:
- Source 인터페이스 하나로 디시/인스타/(나중에)네이버·유튜브를 동일하게 다룬다.
- 각 구현체는 RawReview(원시 후기)만 내보내고, 추출/개체연결/집계는 하류 단계가 담당.
- '책임 있는 수집': robots 확인 + 요청 간 딜레이 + 페이지 상한 + 재시도 + 원문 미재배포(필요 스니펫만 하류에서).

주의:
- 디시 갤러리 id와 DOM 셀렉터는 라이브 사이트 기준으로 반드시 검증/조정해야 함(아래 [ADJUST] 표시).
- 네트워크 차단 환경에선 실행 불가 — 로컬에서 키/셀렉터 넣고 돌릴 것.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional
from urllib.parse import urlencode, urljoin, urlparse
import urllib.robotparser as robotparser
import time, re, logging

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("sources")


# ---------------------------------------------------------------- 공통 모델
@dataclass
class RawReview:
    text: str                      # 정제된 본문(+제목)
    url: str
    platform: str                  # 'dcinside' | 'instagram' | ...
    posted_at: Optional[str] = None
    raw_title: Optional[str] = None
    meta: dict = field(default_factory=dict)   # 키워드/갤러리 등 추적용


# ---------------------------------------------------------------- 인터페이스
class Source(ABC):
    """모든 수집기는 이 인터페이스를 구현. 나중에 네이버/유튜브도 여기에 추가만 하면 됨."""
    platform: str

    @abstractmethod
    def collect(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        """키워드(마켓명/제품명/초성)별로 원시 후기를 yield."""
        ...


# ---------------------------------------------------------------- 책임 수집 유틸
class Throttle:
    """요청 간 최소 간격 보장(서버 예의 + 차단 회피)."""
    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()


def robots_allowed(url: str, user_agent: str) -> bool:
    """robots.txt 준수 여부 확인. 강제력은 없지만 지키는 게 원칙."""
    try:
        parts = urlparse(url)
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception as e:           # robots를 못 읽으면 보수적으로 진행 보류
        log.warning("robots 확인 실패(%s): %s", url, e)
        return False


def get(session: requests.Session, url: str, throttle: Throttle,
        retries: int = 2, timeout: int = 10) -> Optional[str]:
    """딜레이 + 재시도 포함 GET. 실패 시 None."""
    for attempt in range(retries + 1):
        throttle.wait()
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.text
            log.warning("HTTP %s @ %s", r.status_code, url)
        except requests.RequestException as e:
            log.warning("요청 실패(%s) @ %s [%d/%d]", e, url, attempt + 1, retries + 1)
        time.sleep(1.5 * (attempt + 1))
    return None


# ---------------------------------------------------------------- 노이즈/유해 필터
# NOTE: 시드 수준. 운영에선 분류기/LLM로 대체 권장(아래 toxic_via_llm 훅 참고).
_TOXIC_SEED = [
    # 최소한의 시드. 실제 목록은 별도 파일로 관리하고 여기서 로드 권장.
    # (욕설/혐오 표현 패턴)
]
_NOISE_RE = re.compile(r"^(ㅋ+|ㅇㅇ|ㄴㄴ|\.+|\s*)$")

def is_low_quality(text: str, min_len: int = 8) -> bool:
    t = (text or "").strip()
    if len(t) < min_len:
        return True
    if _NOISE_RE.match(t):
        return True
    return False

def has_toxic(text: str) -> bool:
    t = text or ""
    return any(p and p in t for p in _TOXIC_SEED)

def toxic_via_llm(text: str, classify_fn=None) -> bool:
    """운영용: 외부 분류기/LLM 훅. classify_fn(text)->bool 주입."""
    return bool(classify_fn(text)) if classify_fn else has_toxic(text)


# ---------------------------------------------------------------- 디시인사이드
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


# ---------------------------------------------------------------- 인스타그램(스텁)
class InstagramSource(Source):
    """
    1층 공식 스펙 + 2층 유저 후기 캡션(긍정편향).
    Graph API 설계:
      - business_discovery(아는 마켓 핸들) → 마켓 공식 게시물 캡션/이미지 (1층)
      - ig_hashtag_search → recent_media/top_media 캡션 (유저 후기, username 없음)

    [데이터 소싱 결정 2026-06-29] business_discovery 는 앱 개발 모드에서도 advanced access
    (=App Review·비즈 인증·수주 심사)를 요구한다 → 포트폴리오 데모 범위 밖. 그래서 라이브 호출 대신
    큐레이션 fixture(`data/layer1_fixture.json`)를 같은 인터페이스로 주입한다. 인터페이스는 그대로 두어
    토큰만 확보되면 `_live_business_discovery` 로 전환 가능(설계는 코드에 남김).

    fixture_path 를 주면 fixture 모드, ig_user_id+access_token 만 주면 라이브 모드.
    """
    platform = "instagram"

    def __init__(self, access_token: str | None = None, ig_user_id: str | None = None,
                 min_interval: float = 1.0, fixture_path: Optional[str] = None):
        self.token = access_token
        self.ig_user_id = ig_user_id
        self.throttle = Throttle(min_interval)
        self.s = requests.Session()
        self.fixture_path = fixture_path   # set → fixture 모드(라이브 API 미호출)

    # -------- 1층: 마켓 공식 게시물 (라이브/픽스처 동일 반환형) --------
    def business_discovery(self, handle: str) -> list[dict]:
        """핸들의 공식 게시물 목록을 business_discovery 응답(media.data) 모양으로 반환.
        fixture_path 가 있으면 큐레이션 스냅샷에서, 없으면 Graph API 라이브."""
        if self.fixture_path:
            from .layer1 import load_fixture
            fx = load_fixture(self.fixture_path)
            return fx.get(handle, {}).get("posts", [])
        return self._live_business_discovery(handle)

    def _live_business_discovery(self, handle: str) -> list[dict]:
        # GET /{ig_user_id}?fields=business_discovery.username({handle}){media{caption,permalink,timestamp,media_type}}
        # ⚠️ 대상 핸들이 공개 비즈/크리에이터 계정이어야 하고, 앱이 advanced access(App Review)여야 200.
        raise NotImplementedError(
            "business_discovery 라이브는 App Review 통과 후 가능 — 데모는 fixture_path 사용")

    def collect(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        """fixture 모드: 마켓 공식 게시물 캡션을 1층 RawReview 로 yield(공식 스펙=official_spec).
        keywords 를 주면 그 핸들만, 없으면 fixture 전체. (2층 hashtag 경로는 _collect_hashtag 참고)"""
        if not self.fixture_path:
            raise NotImplementedError("Graph API 토큰/App Review 연결 후 구현 — 데모는 fixture_path 사용")
        from .layer1 import load_fixture, iter_official_posts
        fx = load_fixture(self.fixture_path)
        handles = [k for k in keywords if k in fx] or None
        n = 0
        for handle, post in iter_official_posts(fx, handles):
            if n >= limit:
                break
            yield RawReview(
                text=post["caption"],
                url=post.get("permalink", ""),
                platform=self.platform,
                posted_at=post.get("timestamp"),
                meta={"kind": "official_spec", "handle": handle,
                      "media_type": post.get("media_type"), "post_id": post.get("id")},
            )
            n += 1

    def _collect_hashtag(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        # [TODO·선택] GET /ig_hashtag_search → hashtag_id → /{hid}/recent_media?fields=caption,permalink,timestamp
        #             2층 '긍정편향 소스' 데모용. username 없음, 7일/30태그 한계. 현재 미구현.
        # 라이브 스크래퍼 티어 구현은 아래 ApifyHashtagSource 참고(App Review 없이 동작).
        raise NotImplementedError("ig_hashtag_search(2층 유저후기)는 선택 — 미구현. 라이브는 ApifyHashtagSource 사용")


# ---------------------------------------------------------------- 인스타그램 해시태그(Apify 스크래퍼)
class ApifyHashtagSource(Source):
    """
    2층 긍정편향 유저후기 — 인스타 해시태그 캡션을 Apify 스크래퍼로 수집.

    [소싱 결정] Graph API `ig_hashtag_search` 는 advanced access(App Review·비즈 인증)를 요구해
    포트폴리오 데모 범위 밖(business_discovery 와 동일 벽). 대신 서드파티 스크래퍼
    `apify/instagram-hashtag-scraper`(공개 데이터만)로 라이브 캡션을 얻는다. 공식 API 아님을
    투명하게 라벨링: 모든 RawReview.meta 에 source="apify", scraped=True 를 실어 official_spec 과
    혼동을 차단한다. platform 은 "instagram"(하류 소스편향 집계에서 긍정편향 IG 소스로 취급).

    비용/한계(2026-07-14 확인): $1.90/1000건, 해시태그당 ~30건 상한, 무료 티어는 API 호출 불가
    (유료 엔트리 플랜 필요). 그래서 폭(큐레이션 태그 다수)으로 recall, 깊이는 포기. token 미설정 시
    조용히 스킵(collect_all 이 빈 결과 처리 — DCInside/NotImplementedError 스킵과 동일한 회복력).

    오프라인 테스트: `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑을 무비용 검증한다
    (data/apify_hashtag_sample.json, eval/test_apify_source.py).
    """
    platform = "instagram"
    COST_PER_1000 = 1.90   # USD, pay-per-result (2026-07-14 확인)

    def __init__(self, token: str | None = None, hashtags: list[str] | None = None,
                 actor: str = "apify/instagram-hashtag-scraper",
                 results_per_hashtag: int = 30, classify_fn=None,
                 hashtags_path: Optional[str] = None):
        self.token = token
        self.actor = actor
        self.results_per_hashtag = results_per_hashtag
        self.classify_fn = classify_fn
        # 큐레이션 태그: 명시 인자 > hashtags_path 파일 > config 기본 경로
        self.hashtags = hashtags if hashtags is not None else self._load_hashtags(hashtags_path)

    # -------- 큐레이션 해시태그 로드 --------
    @staticmethod
    def _load_hashtags(path: Optional[str] = None) -> list[str]:
        """data/ig_hashtags.json → 평탄화된 태그 리스트('#' 없이). '_' 접두 키는 무시."""
        from .config import settings
        p = path or settings.ig_hashtags_path
        try:
            import json
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:                       # 파일 없거나 깨지면 빈 목록(collect 이 keywords 로 보강)
            log.warning("해시태그 목록 로드 실패(%s): %s", p, e)
            return []
        tags: list[str] = []                          # global 폐기 — by_market 만 광역 태그로 사용
        for mk, mtags in (raw.get("by_market", {}) or {}).items():
            if mk.startswith("_"):
                continue
            tags.extend(mtags or [])
        # 순서 유지 중복 제거
        seen, out = set(), []
        for t in tags:
            t = (t or "").lstrip("#").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    @staticmethod
    def _is_choseong_only(s: str) -> bool:
        """전부 한글 호환 자모(ㅂ, ㅁㅁ 등)인가 — 초성 단독 검색어 판별.
        '봄'(완성형 음절, U+AC00~)은 False, 'ㅂ'(U+3131~)은 True."""
        return bool(s) and all("㄰" <= c <= "㆏" for c in s)

    def _resolve_hashtags(self, keywords: list[str]) -> list[str]:
        """검색어가 있으면 '제품명 그대로'만 검색한다(마켓명·'슬라임' 등 광역어 미부착).
        검색어가 없을 때만 큐레이션 마켓 태그(by_market)로 광역 수집한다. 순서 유지 중복 제거.

        예) _resolve_hashtags(["레몬커드쉘도넛"]) → ["레몬커드쉘도넛"]
            머머슬라임·슬라임 같은 마켓/광역 태그는 붙이지 않는다(제품 단위 정밀검색).
        """
        seen, out = set(), []
        for kw in (keywords or []):
            kw = (kw or "").strip()
            if not kw or self._is_choseong_only(kw):   # 초성 단독('ㅂ')은 과광범위 → 제외
                continue
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
        if out:                                        # 특정 제품 검색: 제품명만
            return out
        return list(self.hashtags)                     # 검색어 없음: 큐레이션 마켓 태그로 광역 수집

    # -------- 네트워크 경계(테스트 주입점) --------
    def _run(self, hashtags: list[str]) -> list[dict]:
        """Apify actor 실행 → 데이터셋 아이템(raw dict) 리스트. 실패/토큰없음이면 []."""
        if not self.token:
            log.info("APIFY_TOKEN 미설정 → 해시태그 수집 스킵")
            return []
        if not hashtags:
            log.info("해시태그 목록 비어있음 → 스킵")
            return []
        try:
            from apify_client import ApifyClient
        except ImportError:
            log.warning("apify-client 미설치 → 해시태그 수집 스킵 (pip install apify-client)")
            return []
        try:
            client = ApifyClient(self.token)
            run_input = {"hashtags": hashtags, "resultsType": "posts",
                         "resultsLimit": self.results_per_hashtag}
            run = client.actor(self.actor).call(run_input=run_input)
            # v3: .call() 은 pydantic Run 모델(run.default_dataset_id).
            # v1: dict({"defaultDatasetId": ...}). 둘 다 지원.
            ds_id = getattr(run, "default_dataset_id", None)
            if ds_id is None and isinstance(run, dict):
                ds_id = run.get("defaultDatasetId")
            if not ds_id:
                log.warning("Apify run 에 dataset id 없음: %r", run)
                return []
            return list(client.dataset(ds_id).iterate_items())
        except Exception as e:                       # 네트워크/플랜/쿼터 실패 → 회복력 있게 스킵
            log.warning("Apify 수집 실패: %s", e)
            return []

    # -------- 아이템 → RawReview --------
    def _to_review(self, item: dict, hashtag: str | None = None) -> Optional[RawReview]:
        caption = (item.get("caption") or "").strip()
        if not caption or is_low_quality(caption):
            return None
        tags = item.get("hashtags") or []
        tag = hashtag or (tags[0] if tags else None)
        url = item.get("url") or (
            f"https://www.instagram.com/p/{item['shortCode']}/" if item.get("shortCode") else "")
        # 홍보성(대가/무상 제공) 라벨 — 캡션 자족(KB-무관). 판매자 라우팅은 KB 가 필요해
        # bias.partition(수집 후 패스)에서 확정한다. 여기선 review_class 초안만.
        from .bias import detect_promo
        is_promo, promo_marker = detect_promo(caption)
        return RawReview(
            text=caption,
            url=url,
            platform=self.platform,
            posted_at=item.get("timestamp"),
            meta={
                "kind": "hashtag_caption",
                "source": "apify",
                "scraped": True,
                "hashtag": tag,
                "hashtags": tags,
                "owner_username": item.get("ownerUsername"),
                "likes": item.get("likesCount"),
                "comments": item.get("commentsCount"),
                "shortcode": item.get("shortCode"),
                "toxic": toxic_via_llm(caption, self.classify_fn),
                "review_class": "promo" if is_promo else "genuine",
                "promo_marker": promo_marker,
            },
        )

    def collect(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        hashtags = self._resolve_hashtags(keywords)
        items = self._run(hashtags)
        # 관측성: 요청 태그 수 / 반환 건수 / 예상비용(무음 상한 금지)
        cost = len(items) / 1000 * self.COST_PER_1000
        log.info("Apify 해시태그 %d개 요청 → %d건 반환 (예상비용 $%.4f)",
                 len(hashtags), len(items), cost)
        seen, emitted = set(), 0
        for item in items:
            if emitted >= limit:
                return
            key = item.get("shortCode") or item.get("url")
            if key and key in seen:                  # shortCode 중복 접기
                continue
            if key:
                seen.add(key)
            review = self._to_review(item)
            if review is None:                       # 빈/저품질 캡션 드롭
                continue
            yield review
            emitted += 1
        log.info("Apify 해시태그: %d건 방출(중복/저품질 제외)", emitted)


class InstagramProfileSource(Source):
    """1층 공식 스펙 수집 — 마켓 '본인' 계정 피드를 username 으로 스크랩(Apify profile scraper).

    [소싱 결정 2026-07-15] Graph API business_discovery(handle) 는 App Review 벽으로 막혀
    1층을 fixture 로 대체했지만(memo instagram-businessdiscovery-blocked), Apify
    instagram-profile-scraper 는 '핸들의 공개 게시물'을 App Review 없이 가져온다 —
    business_discovery 의 스크래핑 대체물. 수집물은 owner_username==핸들 이라 bias.partition 이
    판매자로 라우팅 → extract.extract_spec → specs(기존 ingest_hashtag 판매자 경로 재사용).
    공식 API 아님을 투명 라벨링: meta.source="apify", scraped=True.

    비용/한계: $1.60/1000 '프로필'(결과 아님), 프로필당 최신 ~12게시물, resultsLimit 파라미터
    없음(액터가 최신분만). token 미설정/실패 시 조용히 스킵(collect_all 회복력).
    오프라인 테스트: `_run` 이 유일한 네트워크 경계 → 샘플 주입으로 매핑 검증 가능.
    """
    platform = "instagram"
    COST_PER_1000 = 1.60   # USD, per profile (2026-07-15 확인, 무료 플랜)

    def __init__(self, token: str | None = None,
                 actor: str = "apify/instagram-profile-scraper", classify_fn=None):
        self.token = token
        self.actor = actor
        self.classify_fn = classify_fn

    # -------- 네트워크 경계(테스트 주입점) --------
    def _run(self, usernames: list[str]) -> list[dict]:
        """Apify profile actor 실행 → 데이터셋 아이템(raw dict). 실패/토큰없음이면 []."""
        if not self.token:
            log.info("APIFY_TOKEN 미설정 → 프로필 수집 스킵")
            return []
        if not usernames:
            log.info("username 목록 비어있음 → 스킵")
            return []
        try:
            from apify_client import ApifyClient
        except ImportError:
            log.warning("apify-client 미설치 → 프로필 수집 스킵 (pip install apify-client)")
            return []
        try:
            client = ApifyClient(self.token)
            run = client.actor(self.actor).call(run_input={"usernames": usernames})
            ds_id = getattr(run, "default_dataset_id", None)
            if ds_id is None and isinstance(run, dict):
                ds_id = run.get("defaultDatasetId")
            if not ds_id:
                log.warning("Apify run 에 dataset id 없음: %r", run)
                return []
            return list(client.dataset(ds_id).iterate_items())
        except Exception as e:                       # 네트워크/플랜/쿼터 실패 → 회복력 있게 스킵
            log.warning("Apify 프로필 수집 실패: %s", e)
            return []

    @staticmethod
    def _iter_posts(item: dict):
        """액터 출력 스키마 유연 대응: 프로필 객체(latestPosts[]) 또는 평탄 포스트 아이템 양쪽.
        (post_dict, owner_username) 을 yield. owner 는 post > 프로필 순으로 해석."""
        posts = item.get("latestPosts")
        if isinstance(posts, list) and posts:
            owner = item.get("username") or item.get("ownerUsername")
            for p in posts:
                yield p, (p.get("ownerUsername") or owner)
        elif item.get("caption") is not None or item.get("shortCode"):
            yield item, item.get("ownerUsername")

    # -------- 아이템 → RawReview --------
    def _to_review(self, post: dict, owner: str | None) -> Optional[RawReview]:
        caption = (post.get("caption") or "").strip()
        if not caption or is_low_quality(caption):
            return None
        url = post.get("url") or (
            f"https://www.instagram.com/p/{post['shortCode']}/" if post.get("shortCode") else "")
        # review_class 는 달지 않는다 — 판매자 글(1층). bias.partition 이 owner→seller 로 라우팅.
        return RawReview(
            text=caption,
            url=url,
            platform=self.platform,
            posted_at=post.get("timestamp"),
            meta={
                "kind": "profile_post",
                "source": "apify",
                "scraped": True,
                "owner_username": owner,
                "hashtags": post.get("hashtags") or [],
                "likes": post.get("likesCount"),
                "comments": post.get("commentsCount"),
                "shortcode": post.get("shortCode"),
                "toxic": toxic_via_llm(caption, self.classify_fn),
            },
        )

    def collect(self, keywords: list[str], limit: int = 100) -> Iterator[RawReview]:
        """keywords = 마켓 핸들(username). '@' 접두 허용. 프로필 최신 게시물을 RawReview 로 방출."""
        usernames = [k.lstrip("@").strip() for k in (keywords or []) if k and k.strip()]
        items = self._run(usernames)
        cost = len(usernames) / 1000 * self.COST_PER_1000
        log.info("Apify 프로필 %d개 요청 → %d 아이템 반환 (예상비용 $%.4f)",
                 len(usernames), len(items), cost)
        seen, emitted = set(), 0
        for item in items:
            for post, owner in self._iter_posts(item):
                if emitted >= limit:
                    return
                key = post.get("shortCode") or post.get("url")
                if key and key in seen:              # shortCode 중복 접기
                    continue
                if key:
                    seen.add(key)
                review = self._to_review(post, owner)
                if review is None:                   # 빈/저품질 캡션 드롭
                    continue
                yield review
                emitted += 1
        log.info("Apify 프로필: %d건 방출(중복/저품질 제외)", emitted)


# ---------------------------------------------------------------- 검색어 확장
def expand_queries(product: str,
                   *,
                   aliases: list[str] | None = None,
                   market_word: str | None = None,
                   include_market_only: bool = True) -> list[str]:
    """
    제품 검색을 '유도리 있게' 확장한다 — 디시 제목 표기가 제각각이라(풀네임/약칭/
    마켓초성+제품/마켓초성 단독) 한 키워드로는 recall 이 샌다.

    예) expand_queries("허니푸냥이", aliases=["푸냥이"], market_word="봄")
        → ["허니푸냥이", "푸냥이", "ㅂ 허니푸냥이", "ㅂ 푸냥이", "ㅂ"]

    우선순위(앞일수록 먼저 검색 = limit 우선 소진): 구체적 제품 → 마켓초성+제품 →
    마켓초성 단독(가장 광범위·노이즈 많음, 맨 뒤). `collect()` 가 URL 로 키워드 간
    중복을 제거하므로 변형이 겹쳐도 안전하다.
    'ㅂ' 같은 단독 마켓 검색이 과하면 include_market_only=False 로 끈다.
    """
    names = [product] + [a for a in (aliases or []) if a and a != product]
    out: list[str] = []

    def add(t: str) -> None:
        t = " ".join(t.split())                 # 공백 정규화
        if t and t not in out:
            out.append(t)

    for n in names:                             # 1) 제품 풀네임 + 약칭
        add(n)

    if market_word:
        from .linking import choseong           # '봄' → 'ㅂ' (마켓 표기 환원)
        cho = choseong(market_word)
        if cho:
            for n in names:                     # 2) 마켓초성 + 제품 ("ㅂ 허니푸냥이")
                add(f"{cho} {n}")
            if include_market_only:             # 3) 마켓초성 단독 (광범위 recall, 맨 뒤)
                add(cho)
    return out


# ---------------------------------------------------------------- 오케스트레이션
def collect_all(sources: list[Source], keywords: list[str], per_source_limit: int = 100) -> list[RawReview]:
    """여러 소스를 동일 인터페이스로 수집. 나중에 네이버/유튜브 추가 시 리스트에만 넣으면 됨."""
    out: list[RawReview] = []
    for src in sources:
        try:
            out.extend(src.collect(keywords, limit=per_source_limit))
        except NotImplementedError:
            log.info("%s: 미구현 — 스킵", src.platform)
        except Exception as e:
            log.exception("%s 수집 실패: %s", src.platform, e)
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    from .config import settings

    # 인자로 해시태그를 주면 그것들로 애드혹 인스타 검색(디시 데모는 스킵).
    #   예) python -m slime_rag.sources 머머슬라임 레몬커드쉘도넛
    # 인자가 없으면 기본 데모(디시 수집 + #슬라임후기 스모크).
    cli_tags = [t.lstrip("#") for t in sys.argv[1:] if t.strip()]

    if cli_tags:
        if not settings.apify_token:
            print("[apify] APIFY_TOKEN 미설정 → 해시태그 검색 불가. "
                  "APIFY_TOKEN=... 를 앞에 붙이거나 .env 에 설정하라.")
            sys.exit(1)
        print(f"[apify] 해시태그 검색: {', '.join('#'+t for t in cli_tags)}")
        ig = ApifyHashtagSource(token=settings.apify_token, hashtags=cli_tags,
                                results_per_hashtag=settings.apify_results_per_hashtag)
        ig_reviews = collect_all([ig], keywords=[], per_source_limit=30)
        print(f"[apify] 수집 {len(ig_reviews)}건\n")
        # 편향 태깅: 판매자(KB 핸들 일치) / 홍보성(대가·무상 제공) / 실사용 을 즉시 표시.
        # 홍보성은 LLM 의미 판정(키가 있으면) — '서포터' 인용문 오탐/표현 미탐을 피한다.
        import os
        from .bias import classify, seller_index, make_gated_llm_promo_detector
        from .linking import load_kb
        sellers = seller_index(load_kb())
        promo_detector = None
        if os.getenv("OPENAI_API_KEY"):
            from .llm_ops import LLM
            promo_detector = make_gated_llm_promo_detector(LLM(), settings.model_extract)
            print("[bias] 홍보성 판정: 게이트(recall)→LLM(verdict) 캐스케이드 "
                  "— 순수 실사용은 LLM 없이 단락\n")
        else:
            print("[bias] 홍보성 판정: 키워드 seed(폴백) — OPENAI_API_KEY 없음\n")
        for r in ig_reviews:
            cap = " ".join(r.text.split())        # 개행 접어 한 줄로
            v = classify(r, sellers, promo_detector=promo_detector)
            if v.category == "seller":
                tag = f"[판매자·{v.market}→1층]"
            elif v.category == "promo":
                tag = f"[홍보성·{v.marker}]"
            else:
                tag = "[실사용]"
            print(f"{tag} @{r.meta.get('owner_username')}  #{r.meta.get('hashtag')}"
                  f"  ♥{r.meta.get('likes')}  {r.url}\n  {cap[:120]}")
        if not ig_reviews:                        # 반환은 됐는데 0건이면 필드명/커버리지 진단
            raw = ig._run(cli_tags)
            if raw:
                print("[apify] ⚠️ 반환은 됐으나 0건 매핑 — 첫 아이템 키:", sorted(raw[0].keys()))
            else:
                print("[apify] 해당 해시태그에 공개 게시물이 없거나 스크랩 0건.")
        sys.exit(0)

    # --- 기본 데모 ---
    # 디시: 라이브 검증(2026-06-17) 본문+댓글 수집·댓글 AJAX 정상.
    # 주의: 너무 구체적인 제품명("빈짱 연유스무디")은 그 갤에 글이 없어 0건이 정상.
    dc = DCInsideSource(gallery_id="amos")
    reviews = collect_all([dc], keywords=["슬라임"], per_source_limit=20)
    print(f"[dcinside] 수집 {len(reviews)}건")
    for r in reviews[:3]:
        print(r.platform, r.url, r.text[:60])

    # 라이브 스모크(게이트): APIFY_TOKEN 있을 때만 #슬라임후기 1회 수집.
    if settings.apify_token:
        print("\n[apify] 라이브 해시태그 스모크 테스트")
        ig = ApifyHashtagSource(token=settings.apify_token,
                                hashtags=["슬라임후기"],   # 단일 태그로 저비용 확인
                                results_per_hashtag=settings.apify_results_per_hashtag)
        ig_reviews = collect_all([ig], keywords=[], per_source_limit=10)
        print(f"[apify] 수집 {len(ig_reviews)}건")
        for r in ig_reviews[:3]:
            print(r.meta.get("owner_username"), r.meta.get("hashtag"), "|", r.text[:60])
    else:
        print("\n[apify] APIFY_TOKEN 미설정 → 라이브 스모크 스킵 "
              "(사용법: python -m slime_rag.sources 머머슬라임 레몬커드쉘도넛)")
