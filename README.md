# Slime Review RAG

An **evidence-grounded RAG assistant** over Korean slime marketplaces that answers with citations by
combining **official product specs (Layer 1) with user reviews (Layer 2)**. Source bias
(Instagram skews positive / DCInside skews negative) is never averaged away — it is surfaced
transparently **per source, plus the gap between them**.

## Layout

```
slime_rag/              core RAG package → slime_rag/CLAUDE.md
  sources/              collection layer, split per source → slime_rag/sources/CLAUDE.md
  relevance.py          relevance gate — embedding filter: is this about our target?  [Phase 1]
  extract.py            extraction runner — unstructured → structured JSON            [Phase 2]
  linking.py            entity linking — initials/aliases → KB, with abstain          [Phase 3]
  index.py / search.py  embedding & indexing / hybrid search & grounded answers       [Phase 4]
  consolidated_view.py  consolidated view + source-bias aggregation (per source, gap, scent mismatch)
  llm_ops.py            observability + LLM call wrapper (logging, cost, retries)
  config.py             single source of truth for .env
app/ui.py               Streamlit UI → app/CLAUDE.md                                  [Phase 6]
sql/schema.sql          pgvector schema → sql/CLAUDE.md
eval/                   offline unit tests + gold sets → eval/CLAUDE.md
evals/                  pass-rate evaluation harness → evals/CLAUDE.md
data/                   KB (marketplace registry + initials/aliases) + fixtures
prompts/                Layer 1 / Layer 2 extraction prompt specs
```

Design map: [ARCHITECTURE.md](ARCHITECTURE.md) · domain rules: [MEMORY.md](MEMORY.md) ·
decision records: [docs/adr/](docs/adr/).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in OPENAI_API_KEY

# Phases 2–3 (LLM key only)
python -m slime_rag.extract   # one DCInside review → Layer 2 extraction (split per product)
python -m slime_rag.linking   # entity-linking self-test (ㅂㅉ→빈짱; abstain on collision)

# Phase 4 (local pgvector + BGE-M3)
docker compose up -d          # start pgvector, apply schema.sql automatically
python -m slime_rag.db        # connection & schema smoke test
python -m slime_rag.index     # embed and load gold reviews (downloads BGE-M3 on first run)
python -m slime_rag.search    # hybrid search + grounded answers
```

## Stack — decisions and rationale

| Area | Choice | Rationale (in brief) |
|---|---|---|
| LLM | **OpenAI** (behind `llm_ops`) | Extraction = gpt-5.4-mini (cheap, fast); judgment = gpt-5.4 (harder reasoning). Per-task tiering, and the vendor stays swappable — an Anthropic→OpenAI switch touches `llm_ops` only |
| Embeddings | **BGE-M3** | Korean-friendly, emits dense *and* sparse from one model → hybrid retrieval without a second model. Runs locally (zero per-call cost) |
| Vector store | **pgvector** | Lets Layer 1 specs **join** Layer 2 reviews, with metadata filters (market / type / attribute) expressed in SQL |
| Korean keyword search | **kiwipiepy + BM25** | Postgres FTS has no Korean tokenizer → morphological tokenization then BM25, fused with vector results via RRF |
| UI | **Streamlit** | Python-native; chat + filters + consolidated view come together fast |
| Deployment | **Render** (alt: Fly.io) | Managed Postgres (pgvector) and the web service in one place |

> Note: GPT-5-family models are reasoning models, so `temperature` may be ignored or restricted.
> Determinism for extraction JSON therefore comes from **structured outputs
> (`response_format` json_schema, strict)** — not from a low temperature.

## Principles

- **Responsible collection**: respect robots, delay between requests, cap pages, never redistribute
  source text (snippets only).
- **Verifiable against the original**: every displayed piece of evidence links back to its source post.
  Links and the seller-media embed are **references, not copies** — only addresses are stored and the
  bytes are served by the origin, so nothing is downloaded or re-hosted
  ([ADR-0009](docs/adr/0009-source-links-and-owner-media.md)). Evidence text is a structured rendering,
  not a quote — the UI says so next to the link.

> **Deployment note — third-party iframe.** Official-spec cards embed the seller's own Instagram post
> via `instagram.com/p/<shortcode>/embed`. That is a third-party frame: it sets Instagram cookies in the
> viewer's browser, and a browser or extension that blocks third-party frames leaves an empty box the
> server cannot detect. A text link is therefore always kept below the embed, and the card carries a
> caption saying both things. The endpoint is undocumented (the official oEmbed **API** needs App
> Review — the same wall as ADR-0003) and may break without notice; when it does, the link remains.
- **Grounded output**: unmentioned → `null`, per-field evidence snippets, and stated (by the author)
  kept distinct from inferred (by the model).
- **Bias made visible**: no averaging — per source, plus the gap. Label it rather than correct it.
- **Observability built in**: every external call goes through `llm_ops` → logging, cost, retries.

## Development setup (quality gates)

Keeps context-document path integrity and evaluation pass-rate free of regressions.

```bash
git config core.hooksPath .githooks      # enable pre-push hook (path check + eval gate)
python .github/scripts/validate_context_paths.py   # context path integrity (0 hallucinated paths)
python -m evals.run --min 1.0            # entity-linking pass-rate gate
```

The same checks run at no cost on every push and PR via
[.github/workflows/ci.yml](.github/workflows/ci.yml).

For the build history see [BUILD_LOG.md](BUILD_LOG.md); for the compass see [CLAUDE.md](CLAUDE.md).
