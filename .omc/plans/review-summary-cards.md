# 리뷰 요약 카드 (제품별 스펙 + 소스별/통합 리뷰) — 작업 계획

> 상태: **pending approval** (계획만 — 실행 승인 전 코드 수정 없음)
> 작성: 2026-07-15 · 요청: 제품별 요약을 `제품명, 마켓명, 공식 스펙 인스타 URL, 풀조합, 향, 비즈,
> 인스타 리뷰, 디시 리뷰, 통합 리뷰` 형식으로 · 출력면: **Streamlit UI 카드** (사용자 확정)

## 1. 요구사항 요약

제품 1개당 요약 카드 1장. 필드 소스 매핑:

| 카드 필드 | 데이터 소스 | 현황 |
|---|---|---|
| 제품명 | `specs.product` | ✅ 있음 |
| 마켓명 | `specs.market` | ✅ 있음 |
| 공식 스펙 인스타 URL | **없음** — 신규 `specs.source_url` | ❌ 컬럼·배관 신규 |
| 풀조합 | `specs.base_combo` | ✅ 있음 |
| 향 | `specs.scent` | ✅ 있음 |
| 비즈 | `specs.beads` | ✅ 있음 |
| 인스타 리뷰 | 2층 genuine(instagram) 요약 | ❌ 소스별 LLM 요약 신규 |
| 디시 리뷰 | 2층 genuine(dcinside) 요약 | ❌ 소스별 LLM 요약 신규 |
| 통합 리뷰 | 기존 `SUMMARY_PROMPT` 소스aware 요약 | ✅ 재사용 |

프로젝트 불변 규칙 유지: 홍보성(promo)은 인스타 리뷰에 **절대 합산 금지** — 기존 `promo_view`
분리 표시 그대로. 소스 평균 금지 — 카드가 정확히 "소스별 + 통합" 구조라 규칙과 정합.

## 2. 구현 단계

### Step 1 — 스키마: `specs.source_url` (sql/schema.sql)
- `CREATE TABLE specs`에 `source_url TEXT` 추가 + 멱등 마이그레이션
  `ALTER TABLE specs ADD COLUMN IF NOT EXISTS source_url TEXT;` (기존 컨벤션 `schema.sql:51-55`와 동일).

### Step 2 — 1층 배관: fixture → specs (slime_rag/layer1.py, pipeline.py)
- `layer1.iter_specs` (`layer1.py:78-91`): yield 튜플에 `p.get("source_permalink")` 추가(6→7-튜플).
- `pipeline._upsert_spec` (`pipeline.py:33-48`): `source_url` 파라미터 추가.
  ON CONFLICT 시 `source_url = COALESCE(EXCLUDED.source_url, specs.source_url)` —
  URL 없는 재시드가 기존 URL을 null로 덮지 않게.
- `pipeline.load_specs` (`pipeline.py:51-62`): 7-튜플 언팩해 전달.

### Step 3 — 판매자 자동추출 경로도 URL 캡처 (pipeline.py)
- `ingest_hashtag` 판매자 루프(`pipeline.py:115-137`): `_upsert_spec(..., source_url=sp.url)`.
  Apify RawReview는 이미 `url` 보유(`sources.py:617` `post.get("url")`) — 추가 수집 불필요.

### Step 4 — 소스별 리뷰 요약 (slime_rag/consolidated_view.py)
- 신규 `PER_SOURCE_SUMMARY_PROMPT` 1개(플랫폼·편향라벨 파라미터화, 2~3문장·근거된 속성만·편향 라벨 명시).
- `build_consolidated` (`consolidated_view.py:141-200`) 확장:
  - `view["per_source_summaries"] = {"instagram": str|None, "dcinside": str|None}`.
  - **genuine만** 입력(promo 제외 — 기존 `genuine` 분리 재사용). 해당 플랫폼 후기 0건이면
    LLM 미호출·`None`(비용 0, "후기 없음"은 UI 몫).
  - 입력 페이로드 = 그 플랫폼의 by_source/praised/criticized 부분집합(기존 집계 재사용).
  - 기존 `summary`(통합)·`promo_view`는 무변경 → 회귀 없음.
- LLM 호출은 기존 주입식 `llm_summarize` 경로 그대로(관측성 LEDGER 자동 포함).
  라벨 분리: `consolidated.summary.instagram` / `.dcinside` / 통합은 기존 라벨 유지.

### Step 5 — pipeline 글루 (pipeline.py)
- `consolidated_for` (`pipeline.py:233-250`): specs SELECT에 `source_url` 추가 →
  `official_spec["source_url"]`.
- `list_products` (`pipeline.py:205-213`)에도 `source_url` 포함(카드 목록에서 사용 가능).

### Step 6 — UI 카드 (app/ui.py)
- 종합뷰 탭에 요약 카드 섹션: 요청 순서대로
  `제품명 · 마켓명 · 공식 스펙 URL(링크, null이면 "—") · 풀조합 · 향 · 비즈`
  → `인스타 리뷰 / 디시 리뷰`(해당 소스 0건이면 "이 소스 후기 없음 (n=0)")
  → `통합 리뷰`(양쪽 다 있을 때 갭 라벨 병기, 기존 sentiment_gap 재사용).
- 홍보성은 기존 promo_view 표기 유지(인스타 리뷰와 시각적으로 분리).

### Step 7 — 검증
- 단위: `eval/`에 `test_consolidated_summaries.py` —
  (a) fake summarizer 주입 시 IG/DC 각각 올바른 부분집합만 입력되는지,
  (b) promo 레코드가 per-source 입력에 절대 안 들어가는지,
  (c) 한쪽 소스 0건 → 그 키 `None` + summarizer 미호출,
  (d) `iter_specs` 7-튜플, upsert COALESCE 동작(재시드 시 URL 보존).
- 통합: `python -m slime_rag.pipeline` 라이브 1회(포트 55432) —
  `consolidated_for("빈짱","한글과자한줌")`에 `per_source_summaries.dcinside`가 문자열(골드 1건 존재),
  `instagram=None`.
- UI: 헤드리스 AppTest 경로에 카드 렌더 포함, 예외 0.
- 기존 회귀: `python -m pytest eval/ -q` 전체 통과.

## 3. 사용자 입력 필요 (코드 밖 데이터)
- **fixture 6제품의 `source_permalink` URL** (봄 3·머머 1·빈짱 2) —
  `data/layer1_fixture.json`의 `products[].source_permalink`가 현재 전부 `null`.
  채우기 전까지 카드 URL 칸은 "—"로 표시(코드는 null-safe). 스키마·배관은 URL 없이도 완성 가능.

## 4. 리스크 & 완화
| 리스크 | 완화 |
|---|---|
| 데이터 희소: 디시 골드 1건뿐 → 대부분 제품이 "디시 후기 없음" | 카드에 건수(n) 명시 + "후기 없음" 정직 표기. 데이터 확장은 기존 §8-3 별도 트랙 |
| LLM 비용 카드당 최대 3콜(IG+DC+통합) | 소스 0건이면 미호출·기존 `with_summary` 플래그 유지·LEDGER 라벨 분리로 비용 가시화 |
| 재시드가 기존 source_url을 null로 덮음 | upsert에 `COALESCE(EXCLUDED.source_url, specs.source_url)` |
| 소스별 요약이 편향 평균화로 오독될 위험 | 프롬프트에 편향 라벨 문장 강제 + 통합 리뷰는 기존 소스aware 프롬프트 그대로 |
| 기존 종합뷰 소비자 회귀 | 신규 키 추가만(기존 키 무변경), promo_view·summary 로직 불변, pytest 회귀 게이트 |

## 5. 수용 기준 (테스트 가능)
1. `sql/schema.sql`에 `source_url` 컬럼 + 멱등 ALTER 존재, 기존 DB에서 `pipeline.setup(reset=False)` 무오류.
2. `consolidated_for("빈짱","한글과자한줌")` 반환값에 `official_spec.source_url` 키와
   `per_source_summaries` 딕셔너리가 존재, `dcinside`는 비어있지 않은 문자열, `instagram`은 `None`.
3. promo 레코드만 있는 가짜 입력에서 `per_source_summaries.instagram is None`(promo 미유입 검증).
4. AppTest 헤드리스 실행에서 카드 9개 필드 전부 렌더, 예외 0.
5. `python -m pytest eval/ -q` 전체 통과(기존 테스트 포함).
6. `ingest_hashtag` 경유 신규 스펙 행에 Apify `url`이 `source_url`로 저장(오프라인 목 테스트).
