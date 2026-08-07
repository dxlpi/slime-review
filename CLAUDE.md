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
- **Structural decisions**: [docs/adr/](docs/adr/) (embeddings, source bias, IG fixture, promo cascade, review unit, M/Q/E axes, collected_for target policy, value→shipping section, source links & owner media, market logos, six-criteria summary & search page, frontend removal, processing vs publication, verdict/minority & badge meta, market-scope order criteria)
- **Per-module detail**: [slime_rag](slime_rag/CLAUDE.md) · [sql](sql/CLAUDE.md) ·
  [eval](eval/CLAUDE.md) (unit tests) · [evals](evals/CLAUDE.md) (pass-rate)
- **Build record & productivity evidence**: [BUILD_LOG.md](BUILD_LOG.md) · **stack rationale**: [README.md](README.md)

## Evaluation hard gates (must be met)
1. Deployed demo + repository + technical documentation
2. **Evidence of AI coding-tool productivity** ([BUILD_LOG.md](BUILD_LOG.md): key prompts / AI-generated vs human-edited / time)
3. **Observability** (logging, metrics, cost, failure tracing — every LLM call goes through `slime_rag/llm_ops.py` alone)

## Current status & what's left
- **The stack runs end to end: `slime_rag` → [`api/`](api/CLAUDE.md) (FastAPI) → [`web/`](web/CLAUDE.md)
  (Vite + React + TS).** The Streamlit UI was deleted on 2026-08-06
  ([ADR-0012](docs/adr/0012-remove-streamlit-frontend.md)) and the screen was rebuilt by porting the
  design HTML verbatim; `mock.ts` is now only the fallback placeholder. The two module guides carry
  the rules that bite — pixel parity, the do-not-edit KDS copies, the thin-API rule, and why a page
  load never calls the LLM. **Read them before touching either module.**
- Phases 0–5 (collection → extraction → linking → index → search → consolidated view) are **verified
  end-to-end against live data**. Phase 6 renders live data through `api/` — what a given page shows
  depends on what has been collected and summarized, not on the wiring.
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
- **Seller texture description is a first-class Layer 1 field** (2026-08-06): `specs.official_texture`.
  Layer 1 ignores **buyer** evaluation, not all prose. The spec card's four rows are
  풀 조합 / 향 / **종류**(`slime_type`) / **질감**(`official_texture` alone — the fallback to
  `slime_type` was the original bug), and `beads` is **not rendered** (user decision 2026-08-06: on
  embedded-grain products the two rows read as the same word) though it stays in the DB and in
  `/api/page`. Why it must never reach a summary prompt: [slime_rag](slime_rag/CLAUDE.md).
- **Everything the user reads is `~해요` (Toss-style), and the word is 출처, never 소스** (user
  decision 2026-08-06). One shared constant `consolidated_view.TONE`, CI-gated so it reaches every
  prompt. **Tone changes the ending, never the verdict** — softening a negative review erases source
  bias, which is the one thing this project exists to show. Mechanics: [slime_rag](slime_rag/CLAUDE.md).
- **Customer service and shipping are market-scoped, not product-scoped**
  ([ADR-0015](docs/adr/0015-market-scope-order-criteria.md), 2026-08-07). `shipping_cs` was always an
  *order*-unit field (ADR-0005); it only sat on product rows because `index_post` **copies** it onto
  every fan-out row. Summarizing it per product attributed one order's complaint to every product
  that post mentioned. Now `CRITERIA` carries a `scope` and the summary splits in two:
  `build_consolidated` (product axis — texture/scent/sound/longevity + pros/cons) and
  `build_order_view` (**market axis, one per market** — CS/shipping, no pros/cons). Fan-out copies are
  folded by source-fragment id in **code** (`_fold_orders`) — the prompt used to *ask the model* not to
  over-count, which is exactly the pattern this repo forbids elsewhere. Storage is one table with two
  row kinds (`review_summaries.product IS NULL` = market axis; the PK became two partial unique
  indexes). The screen still shows six rows — the two market rows are borrowed and labeled
  '이 마켓 전체 주문 N건 기준이에요'.
  ⚠️ Summaries stored before this ADR keep all six criteria on the product row; `api.main._pick`
  falls back to them until `generate_market_summaries(market)` is run (paid).
- **What survived ADR-0011** now that its screen is gone: the **six criteria**
  (texture/scent/sound/longevity/CS/shipping) live in `consolidated_view.CRITERIA`, one list shared by
  the schema and the summary prompts — that contract is backend and is still gated by
  `eval/test_consolidated_sections.py`. Each criterion is now **two slots, `{verdict, minority}`**
  ([ADR-0014](docs/adr/0014-verdict-minority-and-badge-meta.md)) so the majority view and the dissent
  are separated structurally rather than laid side by side in one sentence; the meta that used to sit
  in that sentence (gap, counts, "only one source") is simply gone from the screen — `criterion_stats`
  keeps it as prompt material and provenance only.
  ⚠️ Summaries stored before 2026-08-07 are the old string shape — the API promotes them to `verdict`
  so nothing breaks, but they keep the old prose until `pipeline.generate_summaries` is re-run (paid). `search.answer` still exists with no consumer. Sentiment gap,
  scent divergence, the supporter bucket, and the evidence-source list are all still **computed** in
  `consolidated_view.py`; whether the new screen renders them is an open design question, not a build one.
- Still to do: **deployment** (hard gate #1 — `web/` as a static site + `api/` as a service; nothing
  else blocks it) · turn on the post-meta sort axes now that the columns exist — `e930471` added
  `body`/`title`/`author`/`posted_at`/`likes`/`views`/`comment_count`/`votes_up`, and `list_reviews`
  already returns them, but `pipeline.REVIEW_SORTS` still offers 수집순 only and rows indexed before
  that commit keep NULLs until they are re-collected (ingest skips an existing `post_id`) ·
  the ADR-0007 re-ruling (above) · expand the entity-linking gold set
  ([evals/gold/](evals/gold/)) · **seed the product alias dictionary for the remaining markets** —
  [`data/product_aliases.json`](data/product_aliases.json) ships and is wired
  (`linking.load_product_aliases` → `relevance`), but only one market is filled in ·
  toxicity filter criteria · **two user inputs for the link feature**: the gold record's amos thread
  URL (`eval/layer2_gold.json` → `source.url`, the only thing between here and a link visible in the
  deployed demo) and the six fixture product IG permalinks (`data/layer1_fixture.json`).

## Frequently used commands
```bash
source .venv/bin/activate                 # always from the repo root (DB port 55432)
docker compose up -d                      # pgvector + schema init
python -m slime_rag.pipeline              # end-to-end glue (no UI — this is how you see data now)
python -m eval.test_bias && python -m eval.test_apify_source && python -m eval.test_relevance_gate   # offline tests
python -m eval.test_consolidated_sections # 6기준 요약 계약 (CRITERIA 공유 · 제품/주문 축 분리)
python -m eval.test_source_links && python -m eval.test_post_columns   # 링크 정책 · 원문 메타 매핑
python -m eval.test_extract_hearsay && python -m eval.test_extract_thread   # extraction hardening / batching
python evals/check_gold_integrity.py && python evals/calibrate_relevance.py --report   # gold + 3-axis gates
python -m evals.run --min 1.0             # evaluation pass-rate gate
uvicorn api.main:app --reload --port 8000 # HTTP API (repo 루트에서 · web/ 이 읽는 유일한 창구)
cd web && npm install && npm run dev      # frontend (http://127.0.0.1:5173 · API 없으면 목 데이터로 폴백)
cd web && npm run build && npm run lint   # 타입체크·번들·린트
python .github/scripts/validate_context_paths.py               # context path integrity
python .github/scripts/validate_context_claims.py              # context claim integrity (부재 주장 검증)
```

## Commit messages

English only — subject, body, trailers. Korean appears **only as a quoted literal in backticks**
(real identifiers, UI copy, domain terms with no English equivalent). Never romanize: `슬라임` or
`slime`, never `seullaim` — without that ban one concept scatters and `git log --grep` stops finding it.
Subject is `<type>(<scope>): <description>`, description ≤50 chars.
Full rules and examples: **[docs/commit-convention.md](docs/commit-convention.md)** ·
enforced by [.githooks/commit-msg](.githooks/commit-msg).

## Absolute rules (non-negotiable)
- **Unmentioned → null; never invent.** Cite via per-field evidence snippets (~15 characters) to stay
  clear of copyright.
- **Only `M` (meta/noise) may drop an item.** Questions and low-E items are ranked to the tail, never
  filtered out; anything past the budget is logged as `unprocessed`, not dropped. Negative-sentiment
  items stay in the candidate set regardless of `E` — that is the source-bias hard gate.
  (Known divergence, ruled intentional: the shipped gate also excludes negative-`e_union`
  non-`bias_hold` items from candidacy — D2, [ADR-0007](docs/adr/0007-collected-for-target-policy.md);
  re-ruling it to match this rule verbatim is option 1 there.)
- **Label source bias; never average.** Scent mismatches, source gaps, and **which side of a
  criterion is the majority** come from joins/aggregation (`consolidated_view.py`), not from the LLM.
A summary sentence carries *content only*; counts and gaps are aggregation, and the LLM is not
  asked to narrate them ([ADR-0014](docs/adr/0014-verdict-minority-and-badge-meta.md)).
  Measured: the LLM wrote "향 평가는 인스타에서만 나와요" when the count was **23 : 1**.
  `criterion_stats` feeds the majority/minority split in the prompt and is snapshotted with each
  summary; it is **not rendered** — badges for it were built and then withdrawn (user, 2026-08-07)
  because `verdict`/`minority` already say it in prose. If it ever returns, it carries facts,
  never threshold verdicts ("the gap is large") — that constant was already removed from
  `sentiment_gap` for being sample-size-blind.
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
