# -*- coding: utf-8 -*-
"""
US-006 Step 4a — 디시 아모스갤 소량 시드 수집(1회, 관련성 게이트 이전 raw).

책임 수집(D9): 기존 Throttle(2s) + max_pages 상한 + 원문 미재배포(스니펫만, 200자 컷).
목적: 잡담/양도/랜박/후기가 자연히 섞인 경계 예시 확보 → 골드셋 부트스트랩 후보.
출력: data/dcinside_sample_raw.json (gitignore 대상 — 스니펫만 골드셋에 남김).
"""
from __future__ import annotations
import json, re, sys
from urllib.parse import urlencode
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 직접 실행 시 repo 루트

from slime_rag.sources.dcinside import DCInsideSource
from slime_rag.sources.base import robots_allowed, get
from slime_rag.config import ROOT

SNIPPET = 200          # 원문 미재배포 — 스니펫만
MAX_POSTS = 34         # 책임 수집 상한
MAX_ITEMS = 130        # posts + comments 총 상한
CMT_PER_POST = 8       # 글당 댓글 상한(한 글이 예산을 독식하지 않게)
OUT = ROOT / "data" / "dcinside_sample_raw.json"

dc = DCInsideSource(gallery_id="amos", comment_pages=1)
BASE, BOARD, GID = dc.BASE, dc.board, dc.gid


def feed_url(page: int) -> str:
    """검색 없는 최근 피드(잡담/양도/랜박이 섞임) — 경계 예시 확보에 유리."""
    return f"{BASE}/{BOARD}/lists/?{urlencode({'id': GID, 'page': page})}"


def search_url(keyword: str, page: int) -> str:
    return dc._list_url(keyword, page)


def snip(t: str) -> str:
    t = (t or "").strip()
    return t[:SNIPPET]


def harvest(list_urls: list[str], items: list[dict], seen: set, kw: str = ""):
    ua = dc.s.headers["User-Agent"]
    posts_done = 0
    for lu in list_urls:
        if posts_done >= MAX_POSTS or len(items) >= MAX_ITEMS:
            break
        if not robots_allowed(lu, ua):
            print("robots 비허용 스킵:", lu); continue
        html = get(dc.s, lu, dc.throttle)
        if not html:
            continue
        for purl in dc._parse_list(html):
            if posts_done >= MAX_POSTS or len(items) >= MAX_ITEMS:
                break
            if purl in seen:
                continue
            seen.add(purl)
            phtml = get(dc.s, purl, dc.throttle)
            if not phtml:
                continue
            title, body, date, pmeta = dc._parse_post(phtml)
            text = dc._clean(f"{title or ''}\n{body or ''}")
            posts_done += 1
            if len(text.strip()) >= 4:
                items.append({"text": snip(text), "platform": "dcinside", "type": "post",
                              "url": purl, "posted_at": date, "parent_title": title, "query": kw})
            # 댓글(후기/잡담 다수) — 글당 상한으로 한 글의 독식 방지
            token = dc._extract_token(phtml)
            m = re.search(r"[?&]no=(\d+)", purl)
            if token and m:
                added = 0
                for c in dc._fetch_comments(m.group(1), purl, token):
                    if len(items) >= MAX_ITEMS or added >= CMT_PER_POST:
                        break
                    ct = dc._clean(c["text"])
                    if len(ct.strip()) >= 3:
                        items.append({"text": snip(ct), "platform": "dcinside", "type": "comment",
                                      "url": f"{purl}#cmt", "posted_at": c.get("date"),
                                      "parent_title": title, "query": kw})
    return items


def main():
    items: list[dict] = []
    seen: set = set()
    # 1) 제품/마켓 검색 먼저(온토픽 후기 확보 — 쿼리 조건부 KEEP 앵커). 글당 댓글 상한으로 다양성 확보.
    queries = ["허니푸냥이", "푸냥이", "봄슬라임", "머머", "빈짱", "웨이즈"]
    for q in queries:
        if len(items) >= MAX_ITEMS:
            break
        harvest([search_url(q, 1)], items, seen, kw=q)
    # 2) 최근 피드 1페이지(뉴스 블리드·잡담·양도 = 보편 네거티브 보강)
    harvest([feed_url(1)], items, seen, kw="_feed")
    OUT.write_text(json.dumps(
        {"_note": "US-006 Step 4a 디시 시드(raw, 스니펫 200자 컷). 골드셋 부트스트랩 후보. gitignore 대상.",
         "gallery": GID, "count": len(items), "items": items},
        ensure_ascii=False, indent=2), encoding="utf-8")
    n_post = sum(1 for it in items if it["type"] == "post")
    print(f"수집 완료: {len(items)}건 (글 {n_post} / 댓글 {len(items)-n_post}) → {OUT}")


if __name__ == "__main__":
    main()
