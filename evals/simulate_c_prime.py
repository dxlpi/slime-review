# -*- coding: utf-8 -*-
"""
C′ 라우팅 시뮬레이션 (WS1 step 0b) — ADR-0007 근거 수치를 리포에서 재현 가능하게 만든다.

질문: 마켓 단위 앵커로 KEEP 을 정하되(정책 C) **랭킹 점수만 제품 단위 코사인으로 쓰면**
      (= C′, keep/rank 분리) 예산 안에 들어오는 '타깃 제품 항목'이 실제로 늘어나는가?

방법(계획 §WS1 step 0b 그대로):
  - 스레드 동일성: 골드에 thread 필드가 없다 → data/dcinside_sample_raw.json 과
    **단조 위치 정렬**로 조인하고 결과를 evals/gold/thread_map.json 에 고정한다.
  - 후보 집합 = **출고된 게이트를 통과한 것**(topic τ + M 드롭 + e_union 드롭).
    D2 판정: e_union 드롭은 의도된 동작이다(relevance.py:301-306). 게이트 앞을 시뮬레이션하면
    C′ 이득이 ~3배 부풀려진다.
  - 정렬 = base.RelevanceGate._rank_key 의미 = (e_bucket, int(bias_hold), score) 내림차순.
    score 만 마켓앵커 코사인 / 제품앵커 코사인으로 바꿔 두 변형을 만든다.
  - 예산 소비 = base.py:225-235 의미(스레드별 정렬 → 스레드 순서대로 그리디, 초과분은 unprocessed).
  - 정답(타깃 제품 항목) = **재라벨 이전** 제품 스코프 label.keep.

실행: source .venv/bin/activate && python evals/simulate_c_prime.py
LLM 호출 없음. 임베딩은 로컬 BGE-M3(index.embed) 재사용.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import slime_rag.relevance as R  # noqa: E402

GOLD = ROOT / "evals" / "gold" / "relevance_gold.json"
RAW = ROOT / "data" / "dcinside_sample_raw.json"
THREAD_MAP = ROOT / "evals" / "gold" / "thread_map.json"

TARGET_MARKET = "봄"
TARGET_SLIME = "허니푸냥이"
PRIMARY_BUDGET = 30          # ingest_dcinside 기본(RELEVANCE_CONF 에 budget 없음 → limit)
SENSITIVITY_BUDGET = 10


# ---------------------------------------------------------------- 입력
def load_items() -> list[dict]:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))["items"]
    return [g for g in gold
            if g["platform"] == "dcinside"
            and (g.get("collected_for") or {}).get("market") == TARGET_MARKET
            and (g.get("collected_for") or {}).get("slime") == TARGET_SLIME]


def build_thread_map(items: list[dict]) -> tuple[dict[str, str], dict]:
    """골드 text → 스레드 URL. 골드는 raw 파일 순서를 보존하므로 **단조 위치 정렬**로 조인한다.

    같은 본문(디시 실시간 뉴스 위젯 블리드)이 여러 스레드에 중복 등장하므로 text→url 사전은
    유일하지 않다. 단조 정렬은 그 중복을 골드 순서로 결정적으로 해소한다.
    """
    raw = json.loads(RAW.read_text(encoding="utf-8"))["items"]
    occ: dict[str, list[int]] = {}
    for idx, r in enumerate(raw):
        occ.setdefault(r["text"], []).append(idx)

    mapping: OrderedDict[str, str] = OrderedDict()
    stats = {"joined": 0, "unjoined": [], "ambiguous_resolved": []}
    cursor = -1
    for g in items:
        cands = [i for i in occ.get(g["text"], []) if i > cursor]
        if not cands:
            stats["unjoined"].append(g["id"])
            continue
        idx = cands[0]
        if len(occ[g["text"]]) > 1:
            stats["ambiguous_resolved"].append(
                {"id": g["id"], "chosen_raw_index": idx,
                 "all_raw_indices": occ[g["text"]]})
        cursor = idx
        mapping[g["id"]] = raw[idx]["url"].split("#")[0]
        stats["joined"] += 1
    return mapping, stats


def write_thread_map(mapping: dict[str, str], titles: dict[str, str]) -> None:
    payload = {
        "_doc": ("골드 id → dcinside 스레드 URL. evals/simulate_c_prime.py 가 "
                 "data/dcinside_sample_raw.json(gitignore, 작성자 로컬)과 단조 위치 정렬로 "
                 "생성한다. 골드에 thread 필드가 없어 ADR-0007 수치를 리포에서 재현하려면 "
                 "이 파일이 필요하다. 라벨은 건드리지 않는다."),
        "source": "data/dcinside_sample_raw.json",
        "join": "monotonic positional alignment (gold order == raw file order)",
        "scope": f"platform=dcinside, collected_for={{market:{TARGET_MARKET}, slime:{TARGET_SLIME}}}",
        "thread_titles": dict(sorted(titles.items())),
        "map": dict(sorted(mapping.items())),
    }
    THREAD_MAP.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


# ---------------------------------------------------------------- 신호 계산
def signals(items: list[dict], conf: dict) -> list[dict]:
    """항목별 topic(마켓앵커/제품앵커) + M/Q/E 신호. 임베딩은 1회 배치."""
    anchor_market = R.build_anchor({"market": TARGET_MARKET, "slime": "슬라임"}, domain=False)
    anchor_product = R.build_anchor({"market": TARGET_MARKET, "slime": TARGET_SLIME}, domain=False)

    flat = [anchor_market, anchor_product]
    spans = []
    per_chunks = []
    for g in items:
        ch = R.chunk(g["text"]) or [g["text"]]
        per_chunks.append(ch)
        start = len(flat)
        flat.extend(ch)
        spans.append((start, len(flat)))

    vecs = np.asarray(R._embed(flat), dtype=float)
    a_mkt, a_prd = vecs[0], vecs[1]

    out = []
    for g, (s, e) in zip(items, spans):
        cv = vecs[s:e]
        mean_vec = R._normalize(cv).mean(axis=0)
        sig = R.mqe_signals(g["text"], mean_vec, conf)
        out.append({
            "id": g["id"],
            "gold_keep": bool(g["label"]["keep"]),          # 재라벨 이전 = 타깃 제품 정답
            "score_market": R._max_cosine(cv, a_mkt),
            "score_product": R._max_cosine(cv, a_prd),
            **sig,
        })
    return out, anchor_market, anchor_product


def shipped_gate(rec: dict, tau: float | None, score_key: str | None) -> tuple[bool, str]:
    """relevance._verdict 의 출고 의미: (topic τ →) M → e_union(합집합 음성) 순서로 드롭.

    tau=None 이면 topic 축을 적용하지 않는다 — **기본 후보 정의**가 이쪽이다.
    이유: 이 시뮬레이션이 바꾸려는 대상이 곧 topic 앵커의 스코프이고, 정책 C 의 τ 는 아직
    보정되지 않았다(WS1 뒷 단계). 제품 앵커로 보정된 τ=0.45 를 마켓 앵커에 그대로 씌우면
    후보가 3건으로 붕괴하는데, 그건 라우팅 결론이 아니라 τ 불일치 아티팩트다.
    (relevance.py:301-306 이 규정하는 드롭 = M · e_union — 그 두 축만 여기서 재현한다.)
    """
    if tau is not None and score_key is not None and rec[score_key] < tau:
        return False, "topic"
    if rec["M"]:
        return False, "meta"
    if not rec["candidate"]:
        return False, "e_union"
    return True, "keep"


# ---------------------------------------------------------------- 예산 소비 (base.py:225-235)
def consume(threads: "OrderedDict[str, list[dict]]", score_key: str, budget: int) -> dict:
    """스레드별 정렬 → 스레드 순서대로 그리디 예산 소비. 초과분 = unprocessed(드롭 아님)."""
    relevant = 0
    emitted, unprocessed = [], []
    for url, recs in threads.items():
        ordered = sorted(recs,
                         key=lambda r: (r["e_bucket"], int(r["bias_hold"]), r[score_key]),
                         reverse=True)
        for r in ordered:
            if relevant >= budget:
                unprocessed.append(r["id"])
                continue
            emitted.append(r["id"])
            relevant += 1
    return {"emitted": emitted, "unprocessed": unprocessed}


def tie_groups(recs: list[dict]) -> list[int]:
    c = Counter((r["e_bucket"], int(r["bias_hold"])) for r in recs)
    return sorted(c.values(), reverse=True)


# ---------------------------------------------------------------- 메인
def main() -> int:
    items = load_items()
    conf = R.RELEVANCE_CONF["dcinside"]
    tau = conf.get("tau_topic", 0.45)

    print("=" * 78)
    print("C′ ROUTING SIMULATION — WS1 step 0b")
    print("=" * 78)
    print(f"gold scope        : dcinside / collected_for={{{TARGET_MARKET}, {TARGET_SLIME}}}")
    print(f"items             : {len(items)}")
    print(f"target-product GT : keep=True {sum(1 for g in items if g['label']['keep'])} "
          f"/ keep=False {sum(1 for g in items if not g['label']['keep'])}")
    print(f"tau_topic(dcinside): {tau}")

    # --- 조인 -------------------------------------------------------------
    mapping, jstats = build_thread_map(items)
    raw = json.loads(RAW.read_text(encoding="utf-8"))["items"]
    titles = {r["url"].split("#")[0]: r.get("parent_title", "") for r in raw}
    thread_order = []
    for g in items:
        u = mapping.get(g["id"])
        if u and u not in thread_order:
            thread_order.append(u)
    sizes = Counter(mapping.values())
    print("\n--- JOIN ---")
    print(f"joined            : {jstats['joined']}/{len(items)}   unjoined={jstats['unjoined']}")
    print(f"threads           : {len(thread_order)}  sizes(desc)={sorted(sizes.values(), reverse=True)}")
    for u in thread_order:
        print(f"    {sizes[u]:>3}  {titles.get(u,'')[:34]:<34} {u.split('no=')[-1]}")
    if jstats["ambiguous_resolved"]:
        print("  중복 본문(뉴스 위젯 블리드) 단조 해소:")
        for a in jstats["ambiguous_resolved"]:
            print(f"    {a['id']}  chosen raw#{a['chosen_raw_index']} of {a['all_raw_indices']}")
    write_thread_map(mapping, {u: titles.get(u, "") for u in thread_order})
    print(f"  → wrote {THREAD_MAP.relative_to(ROOT)}")

    # --- 앵커 코인시던스 제외 규칙 ------------------------------------------
    coincident = [g["id"] for g in items if (g.get("collected_for") or {}).get("slime") == "슬라임"]
    print(f"\nexcluded (collected_for.slime=='슬라임', anchors coincide): {len(coincident)}")

    # --- 신호 -------------------------------------------------------------
    recs, a_mkt, a_prd = signals(items, conf)
    by_id = {r["id"]: r for r in recs}
    print(f"anchor(market)    : '{a_mkt}'")
    print(f"anchor(product)   : '{a_prd}'")

    # --- 후보 집합 3종 -----------------------------------------------------
    def gate(tau_v, key):
        keep, drops = [], Counter()
        for r in recs:
            ok, why = shipped_gate(r, tau_v, key)
            (keep.append(r) if ok else drops.update([why]))
        return keep, dict(drops)

    sets = [
        ("PRIMARY: M + e_union (relevance.py:301-306)", *gate(None, None)),
        ("SENS-A: + topic τ, keep-anchor=PRODUCT (today's shipped scope)",
         *gate(tau, "score_product")),
        ("SENS-B: + topic τ, keep-anchor=MARKET at un-recalibrated τ=%.2f" % tau,
         *gate(tau, "score_market")),
    ]
    print("\n--- CANDIDATE SETS (shipped gate) ---")
    for name, keep, drops in sets:
        print(f"  {len(keep):>3}  {name}   drops={drops}")

    for name, survivors, _ in sets:
        run_variant(name, survivors, mapping, thread_order, by_id)
    return 0


def run_variant(name, survivors, mapping, thread_order, by_id) -> None:
    print("\n" + "=" * 78)
    print(f"CANDIDATE SET :: {name}  (n={len(survivors)})")
    print("=" * 78)
    threads: OrderedDict[str, list[dict]] = OrderedDict((u, []) for u in thread_order)
    for r in survivors:
        threads[mapping[r["id"]]].append(r)

    print("--- POST-GATE CANDIDATES / TIE GROUPS per thread ---")
    print(f"{'thread':<10} {'n':>3} {'gold_keep':>9}  tie-groups (e_bucket,bias_hold)")
    singleton = total_groups = 0
    for u in thread_order:
        rs = threads[u]
        g = tie_groups(rs)
        singleton += sum(1 for x in g if x == 1)
        total_groups += len(g)
        print(f"{u.split('no=')[-1]:<10} {len(rs):>3} {sum(1 for r in rs if r['gold_keep']):>9}  {g}")
    print(f"tie groups: {total_groups} total, {singleton} singletons"
          + (f" ({singleton/total_groups:.0%})" if total_groups else ""))

    # 앵커 교체가 **순서를 바꾸기라도 하는가**(예산과 무관한 구조적 질문).
    reordered = same = 0
    for u in thread_order:
        rs = threads[u]
        if not rs:
            continue
        om = [r["id"] for r in sorted(rs, key=lambda r: (r["e_bucket"], int(r["bias_hold"]),
                                                         r["score_market"]), reverse=True)]
        op = [r["id"] for r in sorted(rs, key=lambda r: (r["e_bucket"], int(r["bias_hold"]),
                                                         r["score_product"]), reverse=True)]
        (same, reordered) = (same + 1, reordered) if om == op else (same, reordered + 1)
    print(f"threads whose within-thread order CHANGES under the anchor swap: "
          f"{reordered}/{reordered + same}")

    n_cand = len(survivors)
    for budget in (PRIMARY_BUDGET, SENSITIVITY_BUDGET):
        binding = n_cand > budget
        print(f"\n--- BUDGET {budget} ({'BINDING' if binding else 'NON-BINDING'}; "
              f"candidates={n_cand}) ---")
        res = {}
        for variant, key in (("market-anchor rank", "score_market"),
                             ("product-anchor rank", "score_product")):
            out = consume(threads, key, budget)
            hits = sum(1 for i in out["emitted"] if by_id[i]["gold_keep"])
            res[key] = out
            print(f"  {variant:<20} emitted={len(out['emitted']):>3} "
                  f"target-product-within-budget={hits:>3} "
                  f"unprocessed={len(out['unprocessed'])}")
        per_thread_ok = True
        for u in thread_order:
            ids = {r["id"] for r in threads[u]}
            m = sum(1 for i in res["score_market"]["emitted"] if i in ids and by_id[i]["gold_keep"])
            p = sum(1 for i in res["score_product"]["emitted"] if i in ids and by_id[i]["gold_keep"])
            if p != m:
                per_thread_ok = per_thread_ok and p >= m
                print(f"    thread {u.split('no=')[-1]}: product={p} vs market={m}"
                      f"  {'✓' if p > m else '✗'}")
        em, ep = set(res["score_market"]["emitted"]), set(res["score_product"]["emitted"])
        print(f"  emitted-set difference: |market\\product|={len(em - ep)} "
              f"|product\\market|={len(ep - em)}  "
              f"{sorted(em ^ ep) if (em ^ ep) else '(identical sets)'}")
        m_tot = sum(1 for i in res["score_market"]["emitted"] if by_id[i]["gold_keep"])
        p_tot = sum(1 for i in res["score_product"]["emitted"] if by_id[i]["gold_keep"])
        ratio = (p_tot / m_tot) if m_tot else float("inf")
        print(f"  ratio(product/market) = {ratio:.3f}   per-thread never-fewer = {per_thread_ok}")
        if not binding:
            print("  VERDICT: simulation vacuous — budget non-binding on this gold")
        else:
            print(f"  VERDICT: {'BUILD C′' if (ratio >= 1.5 and per_thread_ok) else 'DO NOT BUILD C′'}"
                  f" (rule: ratio ≥ 1.5 AND never fewer per thread)")

    # 예산 전 구간 스윕 — "이 예산에서만 무이득"이 아니라 "어떤 예산에서도 무이득"인지.
    diff_set, diff_hits, passing = [], [], []
    best = (0.0, None)
    for b in range(1, n_cand + 1):
        om = consume(threads, "score_market", b)["emitted"]
        op = consume(threads, "score_product", b)["emitted"]
        if set(om) != set(op):
            diff_set.append(b)
        hm = sum(1 for i in om if by_id[i]["gold_keep"])
        hp = sum(1 for i in op if by_id[i]["gold_keep"])
        ok = True
        for u in thread_order:
            ids = {r["id"] for r in threads[u]}
            if (sum(1 for i in op if i in ids and by_id[i]["gold_keep"])
                    < sum(1 for i in om if i in ids and by_id[i]["gold_keep"])):
                ok = False
        if hm != hp:
            diff_hits.append((b, hm, hp, "product+" if hp > hm else "market+"))
        if hm and hp / hm >= 1.5 and ok:
            passing.append((b, hm, hp))
        if hm and hp / hm > best[0]:
            best = (hp / hm, b)
    print(f"\n--- BUDGET SWEEP 1..{n_cand} (decision rule at EVERY binding budget) ---")
    print(f"  budgets where emitted SET differs         : {diff_set or 'none'}")
    print(f"  budgets where target-product COUNT differs : {diff_hits or 'none'}")
    print(f"  budgets satisfying rule (≥1.5× AND never fewer/thread): {passing or 'none'}")
    print(f"  max ratio(product/market) over all budgets : {best[0]:.3f} (at budget {best[1]})")


if __name__ == "__main__":
    raise SystemExit(main())
