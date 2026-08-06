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
- **Structural decisions**: [docs/adr/](docs/adr/) (embeddings, source bias, IG fixture, promo cascade, review unit, M/Q/E axes, collected_for target policy, value→shipping section, source links & owner media, market logos, six-criteria summary & search page, frontend removal)
- **Per-module detail**: [slime_rag](slime_rag/CLAUDE.md) · [sql](sql/CLAUDE.md) ·
  [eval](eval/CLAUDE.md) (unit tests) · [evals](evals/CLAUDE.md) (pass-rate)
- **Build record & productivity evidence**: [BUILD_LOG.md](BUILD_LOG.md) · **stack rationale**: [README.md](README.md)

## Evaluation hard gates (must be met)
1. Deployed demo + repository + technical documentation
2. **Evidence of AI coding-tool productivity** ([BUILD_LOG.md](BUILD_LOG.md): key prompts / AI-generated vs human-edited / time)
3. **Observability** (logging, metrics, cost, failure tracing — every LLM call goes through `slime_rag/llm_ops.py` alone)

## Current status & what's left
- **The frontend is `web/` — Vite + React + TS, and it is not wired to the backend yet.** The
  Streamlit UI was deleted on 2026-08-06 ([ADR-0012](docs/adr/0012-remove-streamlit-frontend.md)) and
  the screen was rebuilt by **porting the design HTML verbatim**: `web/src/screens/SlimeSearch.tsx`
  is `Slime Search.dc.html` with the inline styles carried over unchanged, and `DCLogic` mapped to
  `useState`. Measured against the original mockup it is **pixel-identical** (0.025% differing pixels
  at an 8px offset — the mockup never reset the browser's default `body` margin; we do).
  It renders **placeholder data** (`web/src/data/mock.ts`, the design's own `자리` strings).
  · KDS tokens are copied byte-for-byte into `web/src/styles/kds/`; the mint accent is isolated in
    `web/src/styles/slime-accent.css` (KDS default is blue — never edit the token folder to change it).
  · Six KDS components are cut from the design bundle into `web/src/components/kds/` — **do not edit
    them**, see that folder's README for what was changed and the two known token collisions.
  · The backend was **not touched** by any of this: every display decision already lived behind
    `pipeline` / `consolidated_view` / `source_links`.
  **Next dependency: an HTTP API.** There is none today — the deleted UI called Python functions
  directly. Deployment (hard gate #1) needs `web/` + an API service.
- Phases 0–5 (collection → extraction → linking → index → search → consolidated view) are **verified
  end-to-end against live data**. Phase 6 was rebuilt from zero on 2026-08-06 and is **layout-complete,
  data-empty**.
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
  CI-gated) → a "원문 보기" link wherever a review is rendered. The **policy layer survived the frontend
  deletion intact** and stays CI-gated (`eval/test_source_links.py`) — the new screen only has to call
  `permalink` / `embed_url` / `group_evidence_sources`. DC comment **anchors do not exist**
  (verified live 2026-08-06 — comments are AJAX-rendered), so comment links resolve to the thread URL
  and the collector's `#cmt` is stripped; `comment_no` is preserved as option value only.
  The seller-media embed policy is fail-closed and every fixture `source_permalink` is still null —
  so even once a screen exists it renders nothing until tranche 2 URLs land.
- **Market logos** ([ADR-0010](docs/adr/0010-market-logo-assets.md)): shipped, CI-gated, and
  `data/market_logos/` is now **populated** (13 markets) — markets without a file degrade to a monogram chip.
- **Seller texture description is now a first-class Layer 1 field** (2026-08-06): `specs.official_texture`
  — the seller's own "what it feels like" prose, summarized to 1–2 sentences by `extract_spec`.
  Layer 1 was dropping it ("주관 감상 무시"), so the spec card's **질감** row showed only the
  `slime_type` enum (`폼볼`). The rule is now "ignore **buyer** evaluation", not all prose.
  It renders on the 1층 spec card only and is **barred from every summary prompt** — seller copy is
  structurally always positive, so leaking it in would stack another layer of Instagram bias
  (gated by `eval/test_consolidated_sections.py`). All 16 fixture products are seeded from their captions.
  The card's four rows are now 풀 조합 / 향 / **종류** / 질감 — 종류 is `slime_type`, 질감 is
  `official_texture` alone with **no fallback** to `slime_type` (that fallback was the original bug),
  and `beads` is **not rendered** (user decision 2026-08-06: on embedded-grain products the two rows
  read as the same word). `beads` is untouched in the DB and still ships in `/api/page`.
- **Everything the user reads is `~해요` (Toss-style), and the word is 출처, never 소스** (user
  decision 2026-08-06). Review summaries take it from one shared constant `consolidated_view.TONE`
  (four prompts + `search._ANSWER_SYSTEM` pull from it, CI-gated by
  `eval/test_consolidated_sections.py::test_tone_rule_reaches_every_summary_prompt`); the Layer-1
  `official_texture` takes it from the extraction prompt instead, so the extraction layer keeps no
  dependency on the view layer. **Tone changes the ending, never the verdict** — softening a negative
  review erases source bias, which is the one thing this project exists to show.
- **What survived ADR-0011** now that its screen is gone: the **six criteria**
  (texture/scent/sound/longevity/CS/shipping) live in `consolidated_view.CRITERIA`, one list shared by
  the schema and the three summary prompts — that contract is backend and is still gated by
  `eval/test_consolidated_sections.py`. `search.answer` still exists with no consumer. Sentiment gap,
  scent divergence, the supporter bucket, and the evidence-source list are all still **computed** in
  `consolidated_view.py`; whether the new screen renders them is an open design question, not a build one.
- Still to do: **the HTTP API layer** (blocks both real data and deployment) · decide what to do about
  three fields the design wants but the DB lacks — like/view/vote counts, IG account name, authored date
  (`reviews` has no such columns; the old UI showed 수집일 instead) · the ADR-0007 re-ruling (above) ·
  expand the entity-linking gold set
  ([evals/gold/](evals/gold/)) · product alias dictionary (`data/product_aliases.json`) ·
  toxicity filter criteria · **two user inputs for the link feature**: the gold record's amos thread
  URL (`eval/layer2_gold.json` → `source.url`, the only thing between here and a link visible in the
  deployed demo) and the six fixture product IG permalinks (`data/layer1_fixture.json`).

## Frequently used commands
```bash
source .venv/bin/activate                 # always from the repo root (DB port 55432)
docker compose up -d                      # pgvector + schema init
python -m slime_rag.pipeline              # end-to-end glue (no UI — this is how you see data now)
python -m eval.test_bias && python -m eval.test_apify_source && python -m eval.test_relevance_gate   # offline tests
python -m eval.test_consolidated_sections # 6기준 요약 계약 (CRITERIA 공유)
python -m eval.test_source_links && python -m eval.test_post_columns   # 링크 정책 · 원문 메타 매핑
python -m eval.test_extract_hearsay && python -m eval.test_extract_thread   # extraction hardening / batching
python evals/check_gold_integrity.py && python evals/calibrate_relevance.py --report   # gold + 3-axis gates
python -m evals.run --min 1.0             # evaluation pass-rate gate
cd web && npm install && npm run dev      # frontend (http://127.0.0.1:5173, 목 데이터)
cd web && npm run build && npm run lint   # 타입체크·번들·린트
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
- **Responsible collection**: robots, delays, page caps. Source text is governed by
  **processing vs publication** ([ADR-0013](docs/adr/0013-processing-vs-publication.md)), not by a
  blanket ban: storing full text in the DB and feeding it to the LLM are **allowed**; what ships to a
  browser is a **server-side excerpt plus a link**. A CSS `line-clamp` is not an excerpt — the full
  text already reached the client — so the API must do the cutting, and "더보기" goes to the origin.
  Links and the seller-media embed are **references, not copies** — only addresses are stored, bytes stay
  on the origin ([ADR-0009](docs/adr/0009-source-links-and-owner-media.md)).
  Embed seller (Layer 1) posts only; never user-review media. A wrong link is worse than no link —
  if the identifier is missing, render text with no link.
  Community review cards carry a **server-cut excerpt of the post body** — ADR-0013 reversed ADR-0011's
  snippet-only card, but only as far as an excerpt: never the full text.
  **Collected bytes stay out of git — with one exception**: the market's own IG profile avatar is committed
  ([ADR-0010](docs/adr/0010-market-logo-assets.md)) — it is an identifying mark, not the reviewed work,
  and IG serves no non-expiring avatar URL. Bounded to 1 per market · 320px · own account only ·
  link-back always. Deleting the file reverts it (monogram fallback) — keep that property.
- Determinism comes from structured outputs (strict), with one retry on parse failure.
  ⚠️ Do not send `temperature` for GPT-5-family models.
