# Plan — 후기 편향 태깅 (홍보성 / 판매자) for 해시태그 검색

**Status:** `implemented` (2026-07-15 — 오프라인 테스트 전량 통과 + DB 통합 검증. 라이브 Apify 수집만 게이트)
**Date:** 2026-07-15
**Scope:** Instagram(Apify) 해시태그 후기에 편향 라벨을 붙이고, 요약 단계에서 (1) 홍보성 후기를 실사용 후기와 **분리 요약**, (2) 판매자(마켓 본인) 게시물을 후기에서 제외하되 **1층 공식 스펙으로 자동 추출**한다.

---

## 1. Requirements Summary

사용자 요구(확정):
1. **홍보성 게시물 감지** — 캡션에 `서포터즈 게시물입니다`, `영상 촬영 목적을 위해 무상으로 제공받았습니다`, `단순선물`, `협찬` 등 **대가/무상 제공 문구**가 있으면 "홍보성 후기"로 라벨. → 실사용 후기와 **따로 요약**(드롭 아님, 라벨+분리).
2. **판매자 게시물 감지** — `ownerUsername` 이 KB의 마켓 핸들(예: `from.murmurslime`)과 일치하면 판매자/제작자 글. → **후기 요약에서 제외**. 단, 향료·풀조합(glue)·종류 같은 **공식 스펙 정보는 1층으로 자동 추출**해 활용.

프로젝트 철학 정합성(CLAUDE.md §2·§10): "평균/보정 금지, **라벨링 + 소스별 분리**". 홍보성=드롭이 아니라 **분리 버킷**, 판매자=후기 아님이므로 1층으로 **경로 전환**.

---

## 2. 설계 결정 (Decision Drivers)

| # | 결정 | 근거 |
|---|---|---|
| D1 | 편향 감지를 **별도 모듈 `slime_rag/bias.py`** 로 (relevance.py 와 분리) | relevance = 후기 vs 비후기 축. bias = 후기 중 편향 축 + 판매자 라우팅 축. 서로 다른 관심사. |
| D2 | 홍보성 감지 = **키워드/정규식 결정적 seed** + LLM 폴백 훅(선택) | `_TOXIC_SEED`/`toxic_via_llm` 패턴 재사용. 감사가능·무비용·오프라인 테스트 가능. 정밀도 필요 시 훅 주입. |
| D3 | 판매자 감지 = **KB 핸들 역인덱스**(`handle`+`handles_alt`) vs `owner_username` | 메타데이터 기반, 결정적. linking 이 이미 쓰는 KB 재사용. |
| D4 | 우선순위 **판매자 > 홍보성** | 마켓 본인 계정이 협찬 문구를 써도 그건 판매자 글. 판매자면 1층 라우팅이 우선. |
| D5 | 홍보성 플래그를 **DB 컬럼 `review_class`** 로 승격 (JSONB 안이 아니라) | consolidated_view 가 SQL/레코드 단계에서 genuine↔promo 를 분리 집계해야 함. 메타필터 컬럼 승격 관례(schema.sql) 준수. |
| D6 | 판매자 스펙 추출 = **새 LAYER1 추출 경로**(structured outputs) | 현재 extract.py 는 LAYER2 만. 1층은 fixture/수동뿐 → 판매자 캡션에서 자동 시드하면 1층 커버리지 확장. |

---

## 3. Implementation Steps (파일별)

### Step 1 — `slime_rag/bias.py` (신규) · 편향 감지 코어
- `PROMO_SEED` 상수 + `data/promo_markers.json`(선택) 로더(`_TOXIC_SEED` 패턴). seed 예:
  - `서포터즈`, `무상으로 제공`, `무상 제공`, `무료로 제공`, `제공받아`, `제공받았`, `협찬`, `유료광고`, `유료 광고`, `단순선물`, `단순 선물`, `체험단`, `원고료`, `대가를 받`, `#광고`, `#협찬`, `ppl`
  - ⚠️ `광고` 단독은 오탐(`광고 아님`) → **구문 단위**(`유료광고`,`#광고`)로만. seed 는 config 로 튜닝 가능.
- `detect_promo(text, classify_fn=None) -> tuple[bool, str|None]` — 매칭된 마커 반환. LLM 폴백 훅.
- `seller_index(kb) -> dict[str,str]` — `{owner_username_lower: market_word}` (`handle`+`handles_alt[]`). ※ 구현 시 KB의 실제 핸들 필드명 확인(CLAUDE.md §4: `handle`, `handles_alt`).
- `classify(raw: RawReview, sellers: dict) -> BiasVerdict{category: 'seller'|'promo'|None, marker, market}` — 판매자 먼저(D4), 아니면 홍보성.
- `partition(raws, kb) -> tuple[list[RawReview], list[RawReview]]` — `(seller_posts, user_reviews)`. user_reviews 는 `meta["review_class"]='promo'|'genuine'` 태깅.

### Step 2 — `slime_rag/sources.py` · `_to_review` 에 홍보성 라벨(캡션 자족)
- `ApifyHashtagSource._to_review` 의 `meta` 에 `review_class` 추가: `detect_promo(caption)` → `"promo"|"genuine"`, `promo_marker` 저장.
- 판매자 감지는 KB 의존이라 여기 아님 → Step 1 `partition`(수집 후 패스)에서. (`_to_review` 는 KB-무관 유지 = 기존 결합도 보존.)
- CLI(`__main__`) 출력에 `[홍보성]`/`[판매자]` 태그 표시 → 사용자 터미널 워크플로에서 즉시 확인.

### Step 3 — `slime_rag/extract.py` · LAYER1(판매자→공식 스펙) 추출
- `LAYER1_SCHEMA`(json_schema strict) + `LAYER1_SYSTEM`: 판매자 캡션 → `{product, scent(향료), base_combo(풀조합/glue), slime_type(TYPE_ENUM), evidence}`. **명시된 것만, 미언급 null**(§10). 여러 제품이면 배열.
- `extract_spec(text, llm, model) -> dict` — LAYER2 러너와 대칭.
- market 은 추출이 아니라 **판매자 핸들→market_word**(Step1 `partition` 이 이미 알고 있음)로 주입.

### Step 4 — `sql/schema.sql` + `slime_rag/index.py` · 홍보성 플래그 저장
- schema: `ALTER TABLE reviews ADD COLUMN review_class TEXT NOT NULL DEFAULT 'genuine';` (`CREATE TABLE` 에도 컬럼 추가). 값 `'genuine'|'promo'`.
- `index.index_post(doc, *, source, post_id, review_class='genuine', ...)` — INSERT 에 컬럼 추가. 호출부(pipeline)가 raw.meta 의 `review_class` 를 전달.

### Step 5 — `slime_rag/consolidated_view.py` · 홍보성 분리 요약
- `build_consolidated`: `reviews` 를 `genuine`/`promo` 로 split(레코드의 `review_class`).
- **headline**(by_source·gap·praised·criticized·scent_divergence·summary) = **genuine 만**.
- 새 블록 `promo_view`: `n_promo`, promo-only `per_source_sentiment`, 별도 `promo_summary`(LLM). `SUMMARY_PROMPT` 에 "홍보성 후기는 대가/무상 제공 글이라 긍정 편향 가능 — 실사용과 **분리**해 별도 요약" 지침 추가.
- `_records_for`(pipeline.py): `SELECT` 에 `review_class` 추가 → 레코드에 실어보냄.

### Step 6 — `slime_rag/pipeline.py` · 해시태그 인제스트 글루
- `ingest_hashtag(keywords) -> counts`:
  1. `ApifyHashtagSource.collect` → raws
  2. `bias.partition(raws, kb)` → `(sellers, users)`
  3. sellers → `extract_spec` → `specs` upsert(`load_specs` 의 upsert 재사용, market=핸들매핑)
  4. users → (relevance TODO 게이트는 별개, §8) → `extract_review` → `index_post(review_class=…)`
  5. `join_specs`
- 관측성 로깅: 수집 N, 판매자 M(→스펙), 홍보성 P, 실사용 G.

### Step 7 — `eval/` · 오프라인 테스트(무비용)
- `eval/test_bias.py`:
  - promo seed 각 문구 → True; `광고 아님`/일반 캡션 → False.
  - `seller_index` + `from.murmurslime` → seller(market=머머); 임의 유저 → None.
  - `partition`: 혼합 입력 → sellers/users 정확 분리 + `review_class` 태깅.
  - consolidated split: genuine net 이 promo 를 **불포함**해 계산, `promo_view.n_promo` 정확.
- `eval/test_apify_source.py`: `_to_review` 가 `review_class` 세팅하는지 1케이스 추가(`_run` seam 재사용).
- LAYER1 스키마 strict 유효성(작은 캡션 fixture로 오프라인 구조 검증; LLM 라이브는 게이트).

---

## 4. Acceptance Criteria (testable)

1. `bias.detect_promo("...서포터즈 게시물입니다...")` → `(True, "서포터즈")`; `detect_promo("이 슬라임 광고 아님 내돈내산")` → `(False, None)`.
2. `bias.classify(raw@from.murmurslime, sellers)` → `category="seller", market="머머"`; 일반 유저 홍보 캡션 → `category="promo"`.
3. `partition` 이 판매자 글을 `user_reviews` 에서 제외하고 `seller_posts` 로 분리.
4. `consolidated_for` 결과: `by_source`/`sentiment_gap`/`summary`(headline)에 **홍보성 후기가 반영되지 않음**; `promo_view.n_promo == 홍보성 건수`; `promo_summary` 는 별도 존재.
5. 판매자 캡션 → `specs` 에 `(market, product, scent, base_combo, slime_type)` upsert(멱등).
6. `schema.sql` 재적용 후 `reviews.review_class` 존재, 기본값 `'genuine'`.
7. `eval/test_bias.py` + 갱신된 `test_apify_source.py` **전량 통과**(오프라인·무비용).
8. 기존 파이프라인(gold 색인·`consolidated_for` 데모) **회귀 없음**(promo 없는 데이터는 종전과 동일 출력).

---

## 5. Risks & Mitigations

| 위험 | 완화 |
|---|---|
| **홍보성 오탐/미탐**(키워드 한계, `광고 아님` 류) | 구문 단위 seed + config 파일 튜닝 + `classify_fn` LLM 폴백 훅. seed 는 보수적으로(구문 매칭). 애매하면 홍보성으로 태깅해 **분리 버킷**(드롭 아님)이라 손실 없음. |
| **판매자 핸들 필드명 불확실**(KB 구조) | 구현 첫 스텝에서 `slime_market_kb_demo.json` 실제 필드(`handle`/`handles_alt`) 확인 후 역인덱스. `owner_username` 소문자 정규화 비교. |
| **LAYER1 자동 스펙의 신뢰도**(캡션이 스펙을 다 안 담음) | 미언급 null 원칙(§10) + evidence 스니펫. upsert 라 나중 fixture/수동 시드가 덮어쓸 수 있게 `ON CONFLICT DO UPDATE`. 자동 스펙엔 `source='seller_auto'` 흔적(선택). |
| **DB 컬럼 추가가 기존 행/멱등성 깨뜨림** | `DEFAULT 'genuine'` 로 기존 행 무해. `ALTER ... ADD COLUMN IF NOT EXISTS` 형태로 재적용 안전. |
| **relevance.py 미완**(질문/양도/잡담 혼입) | 이 작업 범위 밖(§8 TODO). bias 는 relevance 와 독립 축이라 병행 가능. 문서에 명시. |
| **Apify 무료 티어 1페이지 상한**(직전 대화의 7건 이슈) | 이 작업과 무관(수집량 문제). 태깅/요약은 수집된 건수에 대해 정상 동작. |

---

## 6. Verification Steps

1. `python -m eval.test_bias` → 전량 통과.
2. `python -m eval.test_apify_source` → 전량 통과(`review_class` 케이스 포함).
3. `python -m slime_rag.sources 레몬커드쉘도넛`(라이브, 저비용) → 터미널에 `[홍보성]`/`[판매자]` 태그 표시 확인.
4. `python -m slime_rag.pipeline`(compose 포트 55432 + .venv) → `ingest_hashtag` 데모 시 판매자→specs / 홍보성 분리 카운트 로깅, `consolidated_for` 에 `promo_view` 출현, 회귀 없음.
5. (선택) 소규모 라이브: 머머 레몬커드쉘도넛 수집 → 판매자 글이 spec 으로, 유저 홍보글이 홍보성 버킷으로 가는지 1회 확인.

---

## 7. Out of Scope (이번 작업 아님)

- `relevance.py` 후기/질문/양도/잡담 분류(§8 TODO, 별도 사용자 기준 필요).
- Apify 유료 플랜/다중 페이지 수집(수집량 이슈, 직전 대화).
- 디시(dcinside) 편향 — 디시는 익명 부정편향이라 홍보성/판매자 개념이 없음(IG 전용 기능). consolidated 는 기존대로 소스별 처리.
- Render 배포(마지막 하드게이트, 별도).

---

## 8. Files Touched

| 파일 | 변경 |
|---|---|
| `slime_rag/bias.py` | **신규** — promo 감지 + seller 역인덱스 + partition |
| `data/promo_markers.json` | **신규(선택)** — 홍보성 마커 config |
| `slime_rag/sources.py` | `_to_review` 에 `review_class`; CLI 태그 표시 |
| `slime_rag/extract.py` | `LAYER1_SCHEMA`/`LAYER1_SYSTEM`/`extract_spec` |
| `sql/schema.sql` | `reviews.review_class` 컬럼 |
| `slime_rag/index.py` | `index_post(review_class=…)` |
| `slime_rag/consolidated_view.py` | genuine/promo split + `promo_view`/`promo_summary` |
| `slime_rag/pipeline.py` | `ingest_hashtag`; `_records_for` 에 `review_class` |
| `eval/test_bias.py` | **신규** 오프라인 테스트 |
| `eval/test_apify_source.py` | `review_class` 케이스 추가 |
| `CLAUDE.md` | §5/§7 편향 태깅 문서화 |
