# -*- coding: utf-8 -*-
"""dcinside(amos) 추출 결과 사람 검수용 덤프 — 무과금·읽기전용(LLM 0 · HTTP 0).

조각(piece) 단위로 원문 + 추출행 + 판단 재료(텍스트가 명명한 마켓 / 초성 런 후보 /
제품명의 1층·레지스트리 소유)를 한 줄 JSON 으로 낸다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slime_rag import rawstore
from slime_rag.db import connect
from slime_rag.linking import KB, load_kb, split_market_prefix
from slime_rag.sources.dcinside import DCInsideSource

_JAMO_RUN_RE = re.compile(r"[ㄱ-ㅎ]+")

kb = load_kb()


def _strip(s):
    return "".join((s or "").split())


forms = sorted({
    _strip(f)
    for m in kb.markets
    for f in KB._surface_forms(m)
    if len(_strip(f)) >= 2
})


def dump_market_hints(text: str):
    """텍스트가 '유일하게' 지목하는 마켓 + 모호한 초성 런(후보 2개 이상).

    ⚠️ **`linking.markets_in_text` 와 다른 개념이다 — 이름을 겹치게 두지 말 것.**
    저쪽이 정본이고(모호 토큰 정책 · 미등재 오버레이 · KB 미등재 토큰 구분), 이건 덤프에
    판단 재료를 싣기 위한 보조다. 감사의 D3 105 / D4 3 숫자를 실제로 만들어 낸 함수는
    `review_dcinside_extraction.scan_markets` 이고 그게 `linking.markets_in_text` 의 정본
    의미론이다. 같은 이름을 쓰면 다음 사람이 둘 중 아무거나 고친다.
    """
    unique, ambiguous = set(), {}
    if not text:
        return sorted(unique), ambiguous
    low = text.lower()
    for form in forms:
        if form.lower() not in low:
            continue
        cands, _c, _h = kb.resolve_market(form)
        if len(cands) == 1:
            unique.add(cands[0]["market_word"])
        elif len(cands) > 1:
            ambiguous[form] = [c["market_word"] for c in cands]
    for run in set(_JAMO_RUN_RE.findall(text)):
        cands, _c, _h = kb.resolve_market(run)
        if len(cands) == 1:
            unique.add(cands[0]["market_word"])
        elif len(cands) > 1:
            ambiguous[run] = [c["market_word"] for c in cands]
    return sorted(unique), ambiguous


# ---- 제품명 → 마켓 소유(1층 specs / 레지스트리) ----
spec_owner = defaultdict(set)
registry_owner = defaultdict(set)

reg = json.load(open(ROOT / "data/product_registry.json"))
for mw, mkt in reg["markets"].items():
    for cand in mkt.get("products", []) or []:
        name = cand.get("name") if isinstance(cand, dict) else cand
        if name:
            registry_owner[name].add(mw)

conn = connect()
cur = conn.cursor()
cur.execute("SELECT market, product FROM specs WHERE product IS NOT NULL")
for m, p in cur.fetchall():
    spec_owner[p].add(m)

# ---- 원문(디스크) 에서 스레드 OP 텍스트 ----
src = DCInsideSource()
op_text = {}
for tno in rawstore.iter_keys("dc_thread"):
    items = rawstore.latest_items("dc_thread", tno)
    for it in items:
        if it.get("type") == "post" and it.get("html"):
            title, body, date, _pm = src._parse_post(it["html"])
            op_text[str(tno)] = {
                "title": title, "body": src._clean(body or ""), "date": date,
                "keyword": (it.get("requested") or {}).get("keyword"),
            }
            break

# ---- 추출행 ----
cur.execute(
    """SELECT id, post_id, market, market_confidence, product, spec_id,
              slime_type, scent_sentiment, texture_sentiment, sound_sentiment,
              overall_sentiment, attributes, evidence, review_class,
              relevance_meta, source_ref, title, author, posted_at, body
         FROM reviews WHERE source='amos' ORDER BY id"""
)
cols = [d[0] for d in cur.description]
rows = [dict(zip(cols, r)) for r in cur.fetchall()]

pieces = {}
for r in rows:
    pid = r["post_id"]
    sref = r["source_ref"] or {}
    tno = sref.get("thread_no")
    if not tno and pid:
        m = re.search(r"no=(\d+)", pid)
        tno = m.group(1) if m else None
    key = pid
    p = pieces.setdefault(key, {
        "post_id": pid,
        "thread_no": tno,
        "comment_no": sref.get("comment_no"),
        "kind": "comment" if sref.get("comment_no") else "post",
        "title": r["title"],
        "author": r["author"],
        "posted_at": str(r["posted_at"]) if r["posted_at"] else None,
        "text": r["body"],
        "rows": [],
    })
    attrs = r["attributes"] or {}
    rm = r["relevance_meta"] or {}
    p["rows"].append({
        "id": r["id"],
        "market": r["market"],
        "market_confidence": r["market_confidence"],
        "product": r["product"],
        "mentioned_product": attrs.get("mentioned_product"),
        "mentioned_market": attrs.get("mentioned_market"),
        "spec_id": r["spec_id"],
        "slime_type": r["slime_type"],
        "sent": {
            "scent": r["scent_sentiment"], "texture": r["texture_sentiment"],
            "sound": r["sound_sentiment"], "overall": r["overall_sentiment"],
        },
        "evidence": r["evidence"],
        "review_class": r["review_class"],
        "relevance": {k: rm.get(k) for k in ("M", "Q", "E", "target", "topic_score",
                                             "e_probe", "below_tau", "low_e", "axis")},
        "attrs": attrs,
    })

out = []
for key, p in pieces.items():
    tno = p["thread_no"]
    op = op_text.get(str(tno)) if tno else None
    p["op"] = op
    text = p["text"] or ""
    ctx = text if p["kind"] == "post" else f"{(op or {}).get('title','')}\n{(op or {}).get('body','')}\n{text}"
    uniq, amb = dump_market_hints(text)
    cuniq, camb = dump_market_hints(ctx)
    p["signals"] = {
        "markets_in_piece": uniq,
        "ambiguous_in_piece": amb,
        "markets_in_thread_ctx": cuniq,
        "ambiguous_in_thread_ctx": camb,
    }
    for row in p["rows"]:
        prod = row["product"] or row["mentioned_product"]
        if prod:
            hint, rem = split_market_prefix(prod, kb)
            row["prefix_check"] = {"market_hint": hint, "remainder": rem}
            row["owner_spec"] = sorted(spec_owner.get(prod, []))
            row["owner_registry"] = sorted(registry_owner.get(prod, []))
    out.append(p)


def sort_key(p):
    try:
        t = int(p["thread_no"])
    except (TypeError, ValueError):
        t = 0
    try:
        c = int(p["comment_no"] or 0)
    except (TypeError, ValueError):
        c = 0
    return (t, 0 if p["kind"] == "post" else 1, c)


out.sort(key=sort_key)
dest = Path(sys.argv[1] if len(sys.argv) > 1 else "dc_pieces.jsonl")
with dest.open("w", encoding="utf-8") as fh:
    for p in out:
        fh.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
print(f"pieces={len(out)} rows={sum(len(p['rows']) for p in out)} -> {dest}")
print("threads with op text:", len(op_text))
