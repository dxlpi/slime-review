# -*- coding: utf-8 -*-
"""계측기 A — `pipeline.dc_attribution_target` 를 적재분 전량에 **리플레이**한다.

무과금·읽기전용(LLM 0회 · HTTP 0회 · commit 없음).

왜 필요한가: 감사는 2026-08-10 시점의 DB 를 읽고 D5a/D5b/D7 51행을 지목했는데, 그 뒤
`b1d828e`(접두 분리 순서)·`42b24e4` 가 랜딩했다. 그래서 그 51행 중 **일부는 이미 현행
코드가 잡는다**. 이 분할이 없으면 Phase 4 는 '이미 고쳐진 것을 다시 고치는 계획'이 되고,
새 어휘의 크기를 잘못 잡는다(어휘를 필요 이상으로 넓히면 진짜 제품명이 지워지는데 그
손실은 화면에 안 보인다).

⚠️ 이 리플레이는 **제품 축 전용**이다. 마켓 축은 원리적으로 리플레이가 불가능하다 —
  `link()` 의 첫 입력인 `mentioned_market` 이 `attributes` 에 저장돼 있지 않기 때문이다
  (실측 amos `attributes` 키: firsthand_evidence, longevity, overall, sound, texture,
  scent, mentioned_product, shipping_cs, value). 마켓 축의 유일한 측정치는 사람이 만든
  감사 문서다(계획 §3.5).

실행:
    OPENAI_API_KEY="" python evals/replay_dc_attribution_target.py \
        --gold evals/gold/dc_attribution_gold.json \
        --out  evals/results/dc_target_replay.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slime_rag import linking, pipeline          # noqa: E402
from slime_rag.db import connect                 # noqa: E402

# 이 계측기가 가르는 결함군 — 제품명 위생(어휘 게이트) 소관인 것만이다.
NAME_CODES = ("D5a", "D5b", "D5c", "D7")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", type=Path, default=ROOT / "evals/gold/dc_attribution_gold.json")
    ap.add_argument("--out", type=Path, default=ROOT / "evals/results/dc_target_replay.json")
    ap.add_argument("--source", default="amos")
    args = ap.parse_args()

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    coded: dict[str, list[str]] = {}
    for e in gold["entries"]:
        coded.setdefault(e["key"], []).extend(e["codes"])

    kb = linking.load_kb()
    # ⚠️ 재료는 **`repair_dc_attribution` 이 실제로 넘기는 것과 같아야** 한다. 안 넘기면
    #   어휘 게이트의 수식 갈래가 꺼져(페일세이프) 리플레이가 실제 백필보다 적게 잡고,
    #   그 차이가 '아직 안 고쳐졌다'로 읽힌다 — 예상치가 실제를 예고 못 하는 그 실패다.
    known = pipeline.known_product_names()
    unregistered = linking.load_unregistered_market_tokens()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, post_id, market, product, attributes FROM reviews "
            "WHERE source=%s ORDER BY id", (args.source,)).fetchall()

    by_reason: Counter = Counter()
    already, needs_new = [], []
    for rid, post_id, market, product, attrs in rows:
        target, why, new_market, new_conf = pipeline.dc_attribution_target(
            product, market, kb, known_products=known, unregistered=unregistered)
        by_reason[why] += 1
        key = f"{post_id}␟{(attrs or {}).get('mentioned_product') or ''}"
        codes = [c for c in coded.get(key, ()) if c in NAME_CODES]
        if not codes:
            continue
        rec = {"id": rid, "key": key, "product": product, "codes": sorted(set(codes)),
               "target": target, "why": why, "new_market": new_market}
        # '현행 코드가 이미 잡는다' = 이름이 비워지거나 접두가 떨어져 나간 것.
        # 그 판정은 `why` 하나로 읽힌다 — `unchanged` 면 현행 규칙이 이 이름을 통과시킨다.
        (already if why != "unchanged" else needs_new).append(rec)

    # 이름 단위로도 낸다 — 어휘 파일을 짜는 사람이 읽는 단위가 행이 아니라 **이름**이다.
    def _names(recs):
        c = Counter(r["product"] for r in recs if r["product"])
        return [{"name": n, "rows": k} for n, k in c.most_common()]

    out = {
        "_note": ("계측기 A(계획 §3.4) — 제품 축 리플레이. LLM 0회·읽기전용. "
                  "마켓 축은 attributes 에 mentioned_market 이 없어 리플레이 불가."),
        "source": args.source,
        "scanned_rows": len(rows),
        "by_reason": dict(sorted(by_reason.items())),
        "name_defect_rows": len(already) + len(needs_new),
        "already_handled_rows": len(already),
        "needs_new_vocab_rows": len(needs_new),
        "already_handled_names": _names(already),
        "needs_new_vocab_names": _names(needs_new),
        "already_handled": already,
        "needs_new_vocab": needs_new,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in
                      ("scanned_rows", "name_defect_rows", "already_handled_rows",
                       "needs_new_vocab_rows")}, ensure_ascii=False))
    print("새 어휘가 필요한 이름:",
          ", ".join(n["name"] for n in out["needs_new_vocab_names"]))
    print("->", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
