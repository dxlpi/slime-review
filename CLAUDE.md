# CLAUDE.md — Slime Review RAG (compass)

An evidence-grounded RAG that answers with citations by combining **official specs (Layer 1,
structured) with user reviews (Layer 2, unstructured)** across Korean slime marketplaces.
Portfolio for a GenON AI Product Engineer application — a scaled-down GenOS (AI Search + agents +
LLM Ops). **Source bias (Instagram positive / DCInside negative) is a first-class feature, not
something to correct for** — never average it; show it per source, plus the gap.

> This file is a compass. The details live in the documents below — follow only what you need.

## Where to look (map)
- **Overall flow & dependencies**: [ARCHITECTURE.md](ARCHITECTURE.md) (pipeline + mermaid + ripple table)
- **Domain rules & tribal knowledge**: [MEMORY.md](MEMORY.md) (vocabulary, promo detection, Layer 1 rules, entity linking, KB structure)
- **Structural decisions**: [docs/adr/](docs/adr/) (embeddings, source bias, IG fixture, promo cascade, review unit, M/Q/E axes, collected_for target policy, value→shipping section, source links & owner media, market logos)
- **Per-module detail**: [slime_rag](slime_rag/CLAUDE.md) · [app](app/CLAUDE.md) · [sql](sql/CLAUDE.md) ·
  [eval](eval/CLAUDE.md) (unit tests) · [evals](evals/CLAUDE.md) (pass-rate)
- **Build record & productivity evidence**: [BUILD_LOG.md](BUILD_LOG.md) · **stack rationale**: [README.md](README.md)

## Evaluation hard gates (must be met)
1. Deployed demo + repository + technical documentation
2. **Evidence of AI coding-tool productivity** ([BUILD_LOG.md](BUILD_LOG.md): key prompts / AI-generated vs human-edited / time)
3. **Observability** (logging, metrics, cost, failure tracing — every LLM call goes through `slime_rag/llm_ops.py` alone)

## Current status & what's left
- Phases 0–6 **verified end-to-end against live data**. **The only hard gate left is deployment (Render).**
- Layer 1 runs off a fixture (`data/layer1_fixture.json`, 3 markets / 6 products) because IG App Review
  blocks business_discovery — [ADR-0003](docs/adr/0003-ig-businessdiscovery-fixture.md).
- The relevance gate's `kind` axis is resolved: the exclusive 4-way taxonomy is replaced by three
  independent binary axes **M/Q/E** ([ADR-0006](docs/adr/0006-mqe-three-axis-relevance.md) — the
  source plan `kind-axis-resolution.md` is author-local and not part of this repo).
  Only `M` drops and only `E` ranks — `Q` is a pure observation axis, absent from the sort key.
  Extraction now runs **thread-batched** (input was 99.4% fixed prompt).
- The 15 boundary rulings are **user-confirmed** (2026-08-03,
  [evals/gold/boundary_rulings.json](evals/gold/boundary_rulings.json)); the gold's absolute axes are final.
- The `collected_for` target policy is **ruled** — per-platform C (dcinside=market, IG=product,
  [ADR-0007](docs/adr/0007-collected-for-target-policy.md)) — but dcinside **activation is held**:
  the production-faithful eval measured AC4 infeasible at any τ (recall ceiling 0.500 holdout /
  0.516 all-items, from `e_union` candidacy vs relevance-defined keeps; near-zero market-anchor
  separation). ACTIVE scope stays
  `product`; re-activation needs one of the three re-rulings in the ADR. The relevance verdict now
  persists to the DB (`reviews.relevance_meta` JSONB — hard gate #3 failure tracing); the extraction
  batch cap stays 12 by measured cache hard-stop (`Settings.max_thread_sources`,
  [extract.py](slime_rag/extract.py) evidence comment).
- **Source links are shipped** ([ADR-0009](docs/adr/0009-source-links-and-owner-media.md)):
  `reviews.source_ref` (JSONB identifier, not a baked URL) → `slime_rag/source_links.py` (pure policy,
  CI-gated) → chat citations + a summary "근거 원문" list. DC comment **anchors do not exist**
  (verified live 2026-08-06 — comments are AJAX-rendered), so comment links resolve to the thread URL
  and the collector's `#cmt` is stripped; `comment_no` is preserved as option value only.
  The seller-media embed is fail-closed and currently renders **nothing**, because every fixture
  `source_permalink` is null — it switches on the moment tranche 2 URLs land.
- **Market logos** ([ADR-0010](docs/adr/0010-market-logo-assets.md)): the code path is shipped and
  CI-gated, but `data/market_logos/` is **empty** — every market renders a monogram chip until
  `python -m slime_rag.logos` is run once (Apify, ~$0.02, user-triggered).
- Still to do: the ADR-0007 re-ruling (above) · expand the entity-linking gold set
  ([evals/gold/](evals/gold/)) · product alias dictionary (`data/product_aliases.json`) ·
  toxicity filter criteria · **two user inputs for the link feature**: the gold record's amos thread
  URL (`eval/layer2_gold.json` → `source.url`, the only thing between here and a link visible in the
  deployed demo) and the six fixture product IG permalinks (`data/layer1_fixture.json`).

## Frequently used commands
```bash
source .venv/bin/activate                 # always from the repo root (DB port 55432)
docker compose up -d                      # pgvector + schema init
python -m slime_rag.pipeline              # end-to-end glue
streamlit run app/ui.py                   # UI
python -m eval.test_bias && python -m eval.test_apify_source && python -m eval.test_relevance_gate   # offline tests
python -m eval.test_extract_hearsay && python -m eval.test_extract_thread   # extraction hardening / batching
python evals/check_gold_integrity.py && python evals/calibrate_relevance.py --report   # gold + 3-axis gates
python -m evals.run --min 1.0             # evaluation pass-rate gate
python .github/scripts/validate_context_paths.py               # context path integrity
```

## Absolute rules (non-negotiable)
- **Unmentioned → null; never invent.** Cite via per-field evidence snippets (~15 characters) to stay
  clear of copyright.
- **Only `M` (meta/noise) may drop an item.** Questions and low-E items are ranked to the tail, never
  filtered out; anything past the budget is logged as `unprocessed`, not dropped. Negative-sentiment
  items stay in the candidate set regardless of `E` — that is the source-bias hard gate.
  (Known divergence, ruled intentional: the shipped gate also excludes negative-`e_union`
  non-`bias_hold` items from candidacy — D2, [ADR-0007](docs/adr/0007-collected-for-target-policy.md);
  re-ruling it to match this rule verbatim is option 1 there.)
- **Label source bias; never average.** Scent mismatches and source gaps come from joins/aggregation
  (`consolidated_view.py`), not from the LLM.
- **The LLM vendor is a dependency of `llm_ops.py` only.** New sources and models go behind the interface.
- **Responsible collection**: robots, delays, page caps, no redistribution of source text (snippets only).
  Links and the seller-media embed are **references, not copies** — only addresses are stored, bytes stay
  on the origin ([ADR-0009](docs/adr/0009-source-links-and-owner-media.md)).
  Embed seller (Layer 1) posts only; never user-review media. A wrong link is worse than no link —
  if the identifier is missing, render text with no link.
  **One exception, and only one**: the market's own IG profile avatar is downloaded and committed
  ([ADR-0010](docs/adr/0010-market-logo-assets.md)) — it is an identifying mark, not the reviewed work,
  and IG serves no non-expiring avatar URL. Bounded to 1 per market · 320px · own account only ·
  link-back always. Deleting the file reverts it (monogram fallback) — keep that property.
- Determinism comes from structured outputs (strict), with one retry on parse failure.
  ⚠️ Do not send `temperature` for GPT-5-family models.
