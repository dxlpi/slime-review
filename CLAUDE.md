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
- Layer 1 is **no longer fixture-only** (2026-08-07). `business_discovery` is still App-Review-blocked
  ([ADR-0003](docs/adr/0003-ig-businessdiscovery-fixture.md)), but `pipeline.ingest_seller_profiles`
  scrapes the market's **own feed** via Apify `instagram-profile-scraper` — the same seller→`extract_spec`
  path `ingest_hashtag` uses, sharing one gate (`_specs_from_seller_post`). Two live runs:
  23→69행 (4→10 markets), then **69→101행 (10→12 markets)** across all 14 KB markets for
  $0.0224 Apify + $0.1726 LLM (89 calls). The fixture remains the seed for markets that path
  can't reach. **Sampling caveat:** the profile actor returns only the **~12 most recent** posts and has
  no `resultsLimit`, so old products need repeat runs over time — but unlike the hashtag path it is
  **not rank-biased** (whole feed, not a top-N subset). It collects **seller posts only**; user reviews
  live on other accounts and stay the hashtag path's job.
  **That 12-post ceiling is now bypassable** — see the raw-first collection entry below.
  ⚠️ **The second run moved Layer 1 coverage but not the review-side vocabulary gap** — of the 84
  product names on review rows, the 76 with no seller-side counterpart stayed at 76. The window is
  recent-only, so what arrives is new releases while the review corpus is about older products.
  Layer 1 is therefore **structurally incomplete**: "absent from `specs`" never means "not a product".
- **Phantom product names are repaired** (2026-08-07). The review branch had no product gate, so the
  extractor lifted caption **spec lines** (풀조합 `아마존 우드 점토`, 향료 `코코넛과자향`) as products —
  46 of 80 IG rows, while the actually-hashtagged product got **0 rows on those same posts**.
  `extract.resolve_product_name` is now the single rule (backfill + ingest share it), enforced in code:
  ① a caption hashtag is kept even if absent from `specs` · ② sole candidate replaces · ③ exactly one
  Layer-1 match breaks a tie · ④ otherwise hold. Backfill: 10 renamed, 5 folded, 2 held, **0 LLM calls**
  (captions were already in `reviews.body`; re-embedding is local BGE-M3).
  ⚠️ Two guards exist because both directions broke in testing: a name the same post already claims is
  **left untouched** (`keep_distinct` — renaming it erased `빠코폼`, a real second product in a
  comparison post; then nulling it was worse), and `GENERIC_TAGS` gained the Korean community tags
  (`슬라임리뷰` was missing, so a phantom nearly became a *different* phantom). Gate:
  `eval/test_product_repair.py`. Residual: personal tags (`#꼼픽`·`#숭슬지나`) are real hashtags that
  no hashtag rule can exclude — only a per-market product registry can, and all 14 KB markets are
  still `products: []`.
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
- **Collection is incremental and summaries refresh only on change** (2026-08-07,
  the source plan `incremental-collection-and-stale-summaries.md` is author-local and not part of
  this repo). Four things landed:
  · **The dcinside comment key was run-dependent and the constraint above never bound it.** `post_id`
    ended in the run's `enumerate` position, so one extra collected item shifted every later comment
    into a *new* row. `pipeline.dc_post_id` now builds it from `comment_no` (the id dcinside itself
    gives, already stored in `source_ref`) and a migration in [sql/schema.sql](sql/schema.sql) rewrote
    the 19 existing rows in place (verified: old-format 0 · row count unchanged · URLs intact).
    A comment with no id is **skipped and counted** (`skipped_no_comment_no`) — never filled from
    `ordinal`, which would reintroduce the same instability.
  · **A seen-pieces cut runs before the first paid step** on all three ingest paths
    (`index.existing_post_ids`). For the hashtag path that means **before `bias.partition`**, not
    before extraction — the first LLM there is the promo cascade, so cutting at extraction pays the
    verdict calls anyway. `ingest_seller_profiles` cuts on the post URL (`specs.source_permalink`) and
    keeps `skip_seen=False` as the forced re-extract path, since a seller may edit a caption.
    Savings are reported as `llm_calls_saved_by_dedup`, kept **separate** from the promo gate's
    `llm_calls_saved`; on dcinside it counts *batches* removed (`extract.count_thread_batches`),
    because that path is one call per thread batch, not per piece.
  · **A watermark cuts the dcinside list before the detail request** (`min_thread_no`, HTTP savings),
    set to `max(thread_no) - WATERMARK_MARGIN` **for that collection anchor** — not the gallery-wide
    max, which would give a first-time product the watermark another product had already pushed up
    and truncate its whole history invisibly. No rows for the anchor → no watermark → full sweep,
    reported as `watermark_anchors`. The margin exists because collection is keyword search, so an
    older thread can match late. **This saves new *posts* only**: new comments hang off old threads,
    so re-visiting is an explicit `revisit_threads` argument (fetched directly by thread number, since
    the search list may not return it), never auto-selected. `gate_unprocessed` is surfaced because
    the relevance budget is spent during collection, i.e. before the seen-pieces cut can help.
  · An already-indexed **post** stays in its extraction batch as *context* when its thread has new
    comments (`context_posts`), and is skipped at index time. Dropping it would silently cost the
    incremental path the sibling-context attribution and market inheritance that batching exists for;
    the batch runs either way, so this adds input tokens, not calls. Thread identity for this — and
    for `group_threads` — comes from **`extract.thread_key` alone**: `parent_no` for comments, the
    URL's `no=` for posts, because a post's meta has no thread number at all. Keying posts off
    `meta["thread_no"]` makes them `None`, which both misses every intended case and turns the
    comparison set into a wildcard that drags dead threads into paid batches.
  · `pipeline.refresh_stale_summaries(dry_run=True)` lists what to regenerate **for free** (measured:
    0 LLM calls, 4 targets, ~$0.10). Staleness = ungenerated / evidence grew by `min_delta` / model
    changed / payload predates ADR-0015. Counts come from `consolidated_for(with_summary=False)` and
    `order_view_for(with_summary=False)`, never from SQL — `n_reviews` is rows but `n_orders` is
    **folded pieces**, and recounting would inflate the order axis. Targets are enumerated from
    `specs`, which doubles as the filter that keeps paid summaries off ghost products.
  Known hole, left deliberately: a piece whose extraction yields `reviews: []` leaves no row, so it is
  re-extracted every run. The tombstone ledger that would fix it is out of scope at ~19 threads/day.
- **Collection is raw-first, and the product registry is derived for free** (2026-08-07). Two
  problems, one change. ① Apify responses were **never persisted** — actor → `RawReview` → LLM → DB
  was one pass, so a wrong extraction rule meant re-buying the scrape (which is exactly what the
  phantom-product repair cost). ② The 14 markets' `products: []` is empty in the KB and fillable only
  from the 4-handle fixture, so the residual phantom tags (`#꼼픽`·`#숭슬지나`) had no possible fix.
  · **`slime_rag/rawstore.py`** puts a disk layer between the paid step and every processing step:
    `data/raw/<kind>/<key>/<utc>.json`, one file per run, **append-only** (a later, shallower run
    can never destroy an earlier capture — that property *is* the rollback story). The envelope
    records actor/requested/`scraped_at`/`usage_total_usd`, matching the `_note`/`actor`/`items[]`
    convention the old hand-made snapshots already used. Gitignored (ADR-0013).
    ⚠️ Merge order is the envelope's `scraped_at`, **not** the filename — that broke in development:
    a `-2` collision suffix sorts *before* the plain name (`-` < `.`), so the newest capture lost.
  · **`pipeline.collect_seller_feeds`** walks each market's feed N deep via a different actor
    (`apify/instagram-scraper`, `directUrls`=profile URL — it takes `resultsLimit`, which
    `instagram-profile-scraper` does not). **One actor call per handle**, saved inside `_run` on
    return, so a failure at market 9 cannot lose markets 1–8 of a paid sweep. `newer_than="auto"`
    is a **per-market** watermark (`rawstore.newest_timestamp`) for the same reason the dcinside one
    is per-anchor. `hit_limit` marks a feed that may have been truncated — no silent cap.
    **No LLM, no DB.** `dry_run=True` default; 14 × 200 has a $7.56 ceiling.
  · **`ingest_seller_profiles(from_raw=True)`** re-extracts off disk — LLM cost only, Apify $0.
    Mapping goes through the same `_post_to_seller_review` the collector uses; unpacking the dict
    separately would split `meta` shapes and feed `bias.partition` different values per source.
  · **`pipeline.derive_product_registry`** (LLM 0회) turns the deep feed into per-market product
    candidates, reusing `extract.product_hashtags` + `_tag_exclusions` verbatim and adding only
    aggregation. The new signal is **frequency**: a personal tag sits on nearly every post, a product
    tag on a handful — uncomputable from a 12-post window, and the only thing that can separate
    `#꼼픽` from `#빠코볼`. High-coverage tags go to `market_tag_candidates` for **human** promotion,
    never auto-excluded (over-exclusion deletes real products and is invisible on screen).
    Output is `data/product_registry.json` — names/counts/dates/permalinks, **no caption text**, so
    it commits. **Don't write it into KB `products[]`**: that field holds Layer-1 spec objects and
    name-only entries are exactly the all-null shape `_specs_from_seller_post` drops as thin.
  Both collectors stay: the cheap ~12 window for a daily top-up, the deep sweep for the product list.
  **Not yet run against live data** — the sweep is paid and awaits a go.
- Still to do: **deployment** (hard gate #1 — `web/` as a static site + `api/` as a service; nothing
  else blocks it) · turn on the post-meta sort axes now that the columns exist — `e930471` added
  `body`/`title`/`author`/`posted_at`/`likes`/`views`/`comment_count`/`votes_up`, and `list_reviews`
  already returns them, but `pipeline.REVIEW_SORTS` still offers 수집순 only and rows indexed before
  that commit keep NULLs and **a plain re-run will not fill them** — indexing is idempotent by
  DB constraint (`UNIQUE(source, post_id, product)` + `ON CONFLICT DO NOTHING`, 2026-08-07), so a
  re-collect skips the row rather than refreshing it; filling those columns needs an explicit backfill ·
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
python -m eval.test_index_meta && python -m eval.test_layer1_collection # 색인 멱등성 · 1층 수집 누적성
python -m eval.test_incremental_collection # 증분 수집(안정 키 · 추출 전 컷 · 워터마크 · 변경분 요약)
python -m eval.test_product_repair                             # 제품명 귀속 복구(유령 vs 진짜 제품)
python -m eval.test_rawstore && python -m eval.test_product_registry  # 원문 저장소 · 제품 후보 유도
python -c "from slime_rag import pipeline as p; print(p.collect_seller_feeds())"        # 1층 피드 전량 수집(dry_run 기본 · $0)
python -c "from slime_rag import rawstore; print(rawstore.manifest())"                  # 원문 저장소 현황(무과금)
python -c "from slime_rag import pipeline as p; print(p.derive_product_registry())"     # 제품 후보 레지스트리(LLM 0회)
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
