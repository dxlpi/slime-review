# Plan — Apify Instagram Hashtag Scraper Source

**Status:** `executed + LIVE-VERIFIED 2026-07-14` — All steps landed. Offline: 7/7 tests. Live smoke (`APIFY_TOKEN` injected): `#슬라임후기` → 30 returned, 10 emitted, real Korean captions + provenance. **Correction:** free/limited plan *does* call the API (contra §1.15 "free tier cannot call the API"); it's page-capped (~30/hashtag), not API-blocked. Two bugs fixed during bring-up (choseong filter, v3 `Run` model access).
**Date:** 2026-07-14
**Scope:** Add a live Layer-2 (positive-bias) Instagram source that pulls hashtag-tagged posts via Apify's `apify/instagram-hashtag-scraper` actor, mapped to `RawReview` behind the existing `Source` plugin interface.

---

## 1. Requirements Summary

- **Goal:** Get **live** hashtag-tagged Instagram captions flowing into the pipeline as the Layer-2 *positive-bias* source (`instagram`), complementing the DCInside negative-bias source.
- **Why Apify (decided in prior conversation):** Official Graph API `ig_hashtag_search` requires Advanced Access → App Review + Business Verification ("Instagram Public Content Access"), which is high-risk/slow for an indie aggregator and already blocked `business_discovery`. Apify is the most reliable scraper-tier route; it drops in behind the existing `Source` seam. ToS/narrative caveat is accepted and handled via honest provenance labeling.
- **Non-goals:** No live Graph API integration; no relevance-filter implementation (still a stub, tracked as a downstream dependency); no changes to DCInside/business_discovery paths.

### Key vendor facts (verified 2026-07-14)
- Actor: `apify/instagram-hashtag-scraper`. Cost **$1.90 / 1,000 results** (pay-per-result).
- **~30-results-per-hashtag ceiling** (multiple user reports; mirrors IG's own limit) → throughput must come from **breadth** (many curated hashtags), not depth.
- **Free tier cannot call the API** (UI-only, ~10 items). A **paid entry plan** is required for programmatic use.
- No login/cookies needed; **public data only** (fits scope).
- Korean hashtags: no stated language restriction (accepts any tag string) — but Korean-tag coverage/quality is **unverified** and is a live risk.
- Output fields per post: `id`, `shortCode`, `url`, `caption`, `ownerUsername`, `ownerFullName`, `hashtags`, `mentions`, `timestamp`, `likesCount`, `commentsCount`, `locationName`, media URLs.
- Input schema: `{ hashtags: [str], resultsType: "posts"|"reels", resultsLimit: int }`.

---

## 2. Design Decisions

### D1 — New `ApifyHashtagSource` class (recommended) vs fill `InstagramSource._collect_hashtag`
**Recommend: new class `ApifyHashtagSource(Source)`** in `sources.py`.
- Rationale: Apify is a distinct backend (third-party scraper, its own auth token, ToS caveat, different failure modes) from the Graph-API-native `InstagramSource` (fixture/business_discovery). Keeping them separate preserves the honest "official-API-vs-scraper" distinction and avoids overloading `InstagramSource`.
- `platform = "instagram"` (so downstream source-bias aggregation treats it as the IG positive-bias source), but every `RawReview.meta` carries `kind="hashtag_caption"`, `source="apify"`, and provenance so it's never confused with `official_spec`.
- Leave `InstagramSource._collect_hashtag` stub in place with a comment pointing to `ApifyHashtagSource` as the live implementation; it stays as the Graph-API path if App Review ever passes.

### D2 — Client: official `apify-client` SDK (recommended) vs raw REST
**Recommend: `apify-client`** (add to `requirements.txt`).
- It handles actor run → poll-to-completion → dataset-item pagination in one call (`client.actor(...).call(...)` then `client.dataset(...).iterate_items()`), which is exactly the run-and-fetch flow we need. Raw REST would re-implement polling/pagination by hand.

### D3 — Keyword → hashtag mapping: explicit curated list (recommended) vs auto-hashtagify keywords
**Recommend: explicit curated `hashtags=[...]`** passed to the constructor, with optional derivation from `keywords`.
- The 30/hashtag cap makes hashtag *selection* the main quality lever; Korean slime hashtags are specific (`#슬라임`, `#{market}슬라임`, `#슬라임후기`, market handle tags) and benefit from curation rather than blind `#{keyword}` generation.
- `collect(keywords)` may additionally derive a few `#{kw}슬라임` variants for recall, but the curated list is primary. The curated list lives in a small data file (`data/ig_hashtags.json`) so it is versioned and reviewable, matching the fixture idiom in `layer1.py`.

### D4 — Provenance & cost guardrails
- Every emitted `RawReview.meta` includes: `kind="hashtag_caption"`, `source="apify"`, `hashtag`, `owner_username`, `likes`, `scraped=True`.
- **No silent caps:** the source `log`s per-run item count, the requested vs returned counts per hashtag (surfacing the ~30 ceiling), and an estimated cost (`items/1000 * $1.90`). Mirrors the project's observability convention.

---

## 3. Implementation Steps

### Step 1 — Config (`slime_rag/config.py`)
Add to `Settings` (after the IG Graph fields, ~config.py:44-49):
- `apify_token: str | None = os.getenv("APIFY_TOKEN")`
- `apify_hashtag_actor: str = os.getenv("APIFY_HASHTAG_ACTOR", "apify/instagram-hashtag-scraper")`
- `apify_results_per_hashtag: int = int(os.getenv("APIFY_RESULTS_PER_HASHTAG", "30"))`
- `ig_hashtags_path: Path = DATA_DIR / "ig_hashtags.json"`
Add matching commented keys to `.env` (`APIFY_TOKEN=...`).

### Step 2 — Curated hashtag seed (`data/ig_hashtags.json`)
- Structure: `{ "global": ["슬라임", "슬라임후기", ...], "by_market": { "봄": ["봄슬라임", ...], "빈짱": [...] } }` (mirrors `layer1_fixture.json` market-keyed shape).
- Seed ~10-20 tags spanning the demo markets (봄/머머/빈짱 + global). `_`-prefixed keys ignored (same convention as `layer1.load_fixture`).

### Step 3 — `ApifyHashtagSource` (`slime_rag/sources.py`)
Add a new class after `InstagramSource` (~sources.py:372):
- `__init__(self, token, hashtags=None, actor=..., results_per_hashtag=30, classify_fn=None, hashtags_path=None)`. Load curated hashtags from `hashtags_path` if `hashtags` not given.
- `_run(hashtags: list[str]) -> list[dict]`: call the actor via `apify-client` with `{hashtags, resultsType:"posts", resultsLimit: results_per_hashtag}`, iterate dataset items, return raw dicts. Wrap in try/except → log + return `[]` on failure (so `collect_all` stays resilient like the DCInside path).
- `_to_review(item: dict, hashtag: str) -> RawReview | None`: map fields → `RawReview(text=caption, url=item["url"], platform="instagram", posted_at=timestamp, meta={...})`; return `None` if caption empty or `is_low_quality(caption)`.
- `collect(self, keywords, limit=100) -> Iterator[RawReview]`: resolve hashtags (curated ∪ optional `#{kw}슬라임` derivations filtered by `keywords`), call `_run`, filter dupes by `shortCode`/`url`, apply `is_low_quality` + `toxic_via_llm` (reuse existing sources.py:102-116), yield up to `limit`; `log` counts + cost estimate.
- Raises nothing fatal: if `token` is missing, `log.info` and yield nothing (so `collect_all` skips it gracefully, consistent with the NotImplementedError skip at sources.py:421).

### Step 4 — Dependency (`requirements.txt`)
- Add `apify-client` (pin a known-good version).

### Step 5 — Offline unit test (`eval/` or a `tests/` fixture)
- Capture a small **sample dataset JSON** (~3-5 items, hand-authored or one real pull) at `data/apify_hashtag_sample.json`.
- Test `_to_review` mapping + `collect` filtering against the sample **without hitting the API** (inject sample via a `_run` monkeypatch/seam). This mirrors the project's fixture philosophy and lets mapping be verified with **zero cost / no paid plan**.

### Step 6 — Live smoke test (gated)
- A `__main__` block or script that runs one real hashtag pull **only if `APIFY_TOKEN` is set**, prints item count + cost estimate + first 3 captions. Confirms Korean-tag coverage and real output shape.

### Step 7 — Wire into orchestration (light)
- `ApifyHashtagSource` is usable via existing `collect_all([...], keywords)` (sources.py:415) with no orchestrator change. Optionally add it to the `__main__` demo list in `sources.py` behind a token check.
- **Do not** auto-index yet: the relevance filter (`relevance.py`) is still a stub, so captions should not be blindly pushed to `extract`/`index` in this step. Feeding into the full pipeline is a follow-up gated on relevance criteria (CLAUDE.md §11-C).

### Step 8 — Docs
- `BUILD_LOG.md`: entry documenting the Apify decision, the verified vendor facts, and the scraper-vs-official-API narrative.
- `CLAUDE.md`: update §3 sources table + §8 TODO (InstagramSource hashtag path now has a live scraper-tier implementation via Apify; official Graph path remains App-Review-blocked).

---

## 4. Acceptance Criteria (testable)

1. `from slime_rag.sources import ApifyHashtagSource` imports without error; class implements `Source` (has `platform` + `collect`).
2. With `APIFY_TOKEN` **unset**, `collect_all([ApifyHashtagSource(token=None)], keywords=["봄"])` returns `[]` and logs a skip — **no exception**.
3. Offline test: `ApifyHashtagSource` mapping over `data/apify_hashtag_sample.json` produces `RawReview`s where `text==caption`, `platform=="instagram"`, `url` set, and `meta` contains `kind=="hashtag_caption"`, `source=="apify"`, `hashtag`, `owner_username`. Empty/low-quality captions are dropped.
4. Offline test: duplicate items (same `shortCode`) collapse to one `RawReview`.
5. `collect` respects `limit` (never yields more than `limit`).
6. Config: `settings.apify_token`, `settings.apify_hashtag_actor`, `settings.apify_results_per_hashtag` resolve from env with the documented defaults.
7. Live smoke test (only when `APIFY_TOKEN` set): a single Korean hashtag pull returns ≥1 item and logs a cost estimate; captions are non-empty Korean text. *(Manual/gated — not part of CI.)*
8. `requirements.txt` includes `apify-client`; `pip install -r requirements.txt` in `.venv` succeeds.

---

## 5. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **~30-result/hashtag cap** limits volume | Thin data per tag | Breadth strategy: curated multi-hashtag list (D3); log requested-vs-returned so the cap is visible, not silent |
| **Korean-tag coverage/quality unverified** | Actor may return sparse/irrelevant Korean results | Step 6 live smoke test **before** committing to volume; fall back to broader global tags (`#슬라임후기`) if market tags are thin |
| **Free tier blocks API** | Can't run live at all | Plan states paid entry plan is a prerequisite; offline test (Step 5) proves mapping with zero cost so code lands before payment |
| **ToS / narrative tension** (scraping IG) | Weakens "responsible collection" story | Provenance labeling (`scraped=True`, `source="apify"`); keep official-API-wall as documented interview talking point; do not present as ground truth |
| **Cost overrun** from large runs | Unexpected spend | `results_per_hashtag` capped in config (default 30); per-run cost estimate logged; bounded hashtag list |
| **Actor output schema drift** | Mapping breaks silently | `_to_review` tolerates missing fields (`.get`), returns `None` on missing caption; offline sample test catches shape regressions |
| **Relevance filter still a stub** | Non-review captions would pollute the index if piped downstream | Step 7 explicitly stops at collection; downstream indexing gated on `relevance.py` (CLAUDE.md §11-C) |
| **Rate/plan limits from Apify** | Runs throttled/queued | `_run` wrapped in try/except with logging; resilient `collect_all` skip on failure |

---

## 6. Verification Steps

1. `.venv/bin/python -c "from slime_rag.sources import ApifyHashtagSource"` — import OK.
2. `.venv/bin/python -c "from slime_rag.config import settings; print(settings.apify_hashtag_actor, settings.apify_results_per_hashtag)"` — config defaults resolve.
3. Run offline mapping test against `data/apify_hashtag_sample.json` — criteria 3-5 pass.
4. Token-unset resilience: run `collect_all` with `ApifyHashtagSource(token=None)` — returns `[]`, logs skip, no raise (criterion 2).
5. `pip install -r requirements.txt` in `.venv` — `apify-client` installs.
6. *(Gated, manual)* With a real `APIFY_TOKEN` + paid plan: run Step 6 smoke test on `#슬라임후기` — inspect returned Korean captions + logged cost.

---

## 7. Open Items Requiring User Input

- **Apify account + paid entry plan + `APIFY_TOKEN`** — prerequisite for any live pull (Steps 6+). Code (Steps 1-5) can land and be tested offline without it.
- **Curated hashtag list** — I can draft an initial `data/ig_hashtags.json` from the demo markets, but you may want to refine which tags best surface real Korean slime reviews.

---

*Plan is `pending approval`. No source files have been edited and nothing has been executed. On approval, implementation can proceed (Steps 1-5 are offline/no-cost; Steps 6+ require the Apify token).*
