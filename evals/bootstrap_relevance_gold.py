# -*- coding: utf-8 -*-
"""
US-006 Step 4 — 골드셋 부트스트랩(초안 라벨 자동 생성 → 사용자 교정 대상).

입력(실텍스트만, D5):
- 디시: data/dcinside_sample_raw.json (Step 4a 시드)
- 인스타: data/apify_profile_murmur_raw.json (latestPosts 캡션, 실데이터)

출력: evals/gold/relevance_gold.json
- 항목: {text, platform, type, collected_for{market,slime}, label{keep, kind, why, draft:true}}
- 초안 라벨은 휴리스틱 — **사용자 교정 전제**(label.draft=true 로 표시).
- 경계 필수(AC3): 실제 KEEP 텍스트를 무관 타깃과 짝지어 쿼리 조건부 네거티브 생성(실텍스트, 타깃만 교체).

⚠️ **일회성 부트스트랩.** 사용자 라벨 교정 이후에는 재실행하지 말 것 —
   교정본(relevance_gold.json)을 덮어써 버린다. 재수집이 필요하면 새 경로로 산출 후 병합.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # 직접 실행 시 repo 루트

from slime_rag.config import ROOT
from slime_rag.sources.base import is_low_quality

DC = ROOT / "data" / "dcinside_sample_raw.json"
IG = ROOT / "data" / "apify_profile_murmur_raw.json"
OUT = ROOT / "evals" / "gold" / "relevance_gold.json"

# --- 휴리스틱 신호(초안용, 사용자 교정 대상) ---
NEWS_RE = re.compile(r"이전\s*다음|\d+\s*/\s*20\b")          # 디시 실시간 뉴스 위젯 블리드
DELETED_RE = re.compile(r"삭제(하였|되었|됨)|삭제된|해당 댓글은")
META_RE = re.compile(r"완장|갤매|주딱|파딱|공지|닥눈삼|가이드|마이너 갤러리|암갤|클린암갤|금칙어|차단|아이피|제보")
RESALE_RE = re.compile(r"양도|팝니다|삽니다|판매글|택포|쿨거|네고|거래")
Q_RE = re.compile(r"\?|살까|뭐\s*살|고민|추천|ㅊㅊ|어때|어떰|뭐가|해주라|없나|나을까")
# 제품/후기성 어휘(온토픽 슬라임 신호)
PROD_RE = re.compile(r"퐁말|디폼|비즈|질감|촉감|향|말랑|쫀득|쨍한|파스텔|신상|버터|슬라임|텍스처|퍼즐|톤|베이스|글리터|점토|크림|스무디|빙수|쉐이크|우유")
MARKETS = ["머머", "봄", "빈짱", "웨이즈", "진통제", "늪지"]

DEFAULT_TARGET = {"market": None, "slime": "슬라임"}


def draft_label_dc(text: str) -> dict:
    t = text.strip()
    if NEWS_RE.search(t):
        return {"keep": False, "kind": "chitchat", "why": "news-bleed(디시 실시간 위젯)", "draft": True}
    if META_RE.search(t):
        return {"keep": False, "kind": "chitchat", "why": "갤 운영/메타 잡담", "draft": True}
    if RESALE_RE.search(t):
        return {"keep": False, "kind": "resale", "why": "양도/거래", "draft": True}
    if PROD_RE.search(t):
        kind = "question" if Q_RE.search(t) else "review"
        return {"keep": True, "kind": kind, "why": "제품/후기 어휘", "draft": True}
    # 제품 신호 없고 짧은 잡담
    return {"keep": False, "kind": "chitchat", "why": "제품 무관 잡담(추정)", "draft": True}


# 검색 쿼리 → 수집 타깃(앵커). 제품 쿼리는 제품 슬라임, 마켓 쿼리는 마켓, _feed 는 보편 네거티브.
PRODUCT_QUERIES = {"허니푸냥이": {"market": "봄", "slime": "허니푸냥이"},
                   "푸냥이": {"market": "봄", "slime": "허니푸냥이"}}
MARKET_QUERIES = {"봄슬라임": "봄", "봄": "봄", "머머": "머머", "빈짱": "빈짱", "웨이즈": "웨이즈"}


def detect_target_dc(text: str, query: str = "") -> dict:
    """수집 쿼리 우선(쿼리 조건부 앵커). 없으면 본문 내 마켓 언급, 그것도 없으면 일반 슬라임."""
    if query in PRODUCT_QUERIES:
        return dict(PRODUCT_QUERIES[query])
    if query in MARKET_QUERIES:
        return {"market": MARKET_QUERIES[query], "slime": "슬라임"}
    for mk in MARKETS:
        if mk in text:
            return {"market": mk, "slime": "슬라임"}
    return dict(DEFAULT_TARGET)


def load_dc_items() -> list[dict]:
    d = json.loads(DC.read_text(encoding="utf-8"))
    out, seen = [], set()
    for it in d["items"]:
        t = (it.get("text") or "").strip()
        if not t or is_low_quality(t, min_len=6) or DELETED_RE.search(t):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append({"text": t, "platform": "dcinside", "type": it.get("type", "post"),
                    "collected_for": detect_target_dc(t, it.get("query", "")),
                    "label": draft_label_dc(t)})
    return out


def load_ig_items() -> list[dict]:
    d = json.loads(IG.read_text(encoding="utf-8"))
    posts = d["items"][0].get("latestPosts", [])
    out = []
    for p in posts:
        cap = (p.get("caption") or "").strip()
        if not cap or is_low_quality(cap):
            continue
        tags = p.get("hashtags") or re.findall(r"#(\w+)", cap)
        slime = (tags[0] if tags else "슬라임").lstrip("#")
        # 판매공지/랜박판매/출고 등은 후기가 아니라 판매글 → keep 은 온토픽 슬라임이므로 True(관련성 축),
        # kind 는 인스타에서 미사용. 초안 keep=True(실슬라임 캡션), 사용자 교정 대상.
        out.append({"text": cap[:400], "platform": "instagram", "type": "post",
                    "collected_for": {"market": "머머", "slime": slime},
                    "label": {"keep": True, "kind": "review", "why": "실 슬라임 캡션(온토픽)", "draft": True}})
    return out


def boundary_pairs(dc_items: list[dict], ig_items: list[dict]) -> list[dict]:
    """AC3 쿼리 조건부: 실제 KEEP 텍스트를 무관 타깃과 짝지어 네거티브(실텍스트, 타깃만 교체)."""
    pairs = []
    keeps_dc = [x for x in dc_items if x["label"]["keep"]][:3]
    for x in keeps_dc:
        pairs.append({"text": x["text"], "platform": "dcinside", "type": x["type"],
                      "collected_for": {"market": None, "slime": "레몬커드쉘도넛"},
                      "label": {"keep": False, "kind": "chitchat",
                                "why": "쿼리조건부 네거티브(무관 타깃) — 원 텍스트는 다른 제품", "draft": True}})
    keeps_ig = [x for x in ig_items if x["label"]["keep"]][:3]
    for x in keeps_ig:
        pairs.append({"text": x["text"], "platform": "instagram", "type": "post",
                      "collected_for": {"market": None, "slime": "레몬커드쉘도넛"},
                      "label": {"keep": False, "kind": "review",
                                "why": "쿼리조건부 네거티브(무관 타깃) — 원 캡션은 다른 제품", "draft": True}})
    return pairs


def main():
    dc = load_dc_items()
    ig = load_ig_items()
    pairs = boundary_pairs(dc, ig)
    items = dc + ig + pairs
    for i, it in enumerate(items):
        it["id"] = f"{it['platform'][:2]}-{i:03d}"
    n_keep = sum(1 for x in items if x["label"]["keep"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "_doc": ("관련성 골드셋(US-006). 실텍스트만(D5). label.draft=true 는 자동 초안 — 사용자 교정 필요. "
                 "collected_for=수집 타깃(앵커). kind∈{review,question,resale,chitchat}(디시). "
                 "인스타 name-collision 비슬라임 네거티브는 실 IG 데이터 부재로 미포함(향후 실수집 필요)."),
        "counts": {"total": len(items), "keep": n_keep, "drop": len(items) - n_keep,
                   "dcinside": len(dc), "instagram": len(ig), "boundary_pairs": len(pairs)},
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"골드 초안 생성: {len(items)}건 (KEEP {n_keep}/DROP {len(items)-n_keep}) "
          f"| 디시 {len(dc)} 인스타 {len(ig)} 경계쌍 {len(pairs)} → {OUT}")


if __name__ == "__main__":
    main()
