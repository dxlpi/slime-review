# Plan — 속성 분리형(향/질감/장단점) 소스별 리뷰 요약 카드

> Status: **pending approval** — 실행 승인 전. 이 문서는 설계·계획만.
> 생성: 2026-07-15 · 대상 포맷: 제품명·마켓명·공식스펙 인스타URL·풀조합·향·비즈(1층) + 인스타/디시/통합 리뷰(2층, 향·질감·장단점 섹션)

## 1. Requirements Summary

제품 단위 "리뷰 카드"를 만든다. 상단은 공식 스펙(1층), 하단은 3개 리뷰 블록(인스타/디시/통합).
각 리뷰 블록은 **향 · 질감 · 장단점(별도 총괄)** 세 섹션으로 나뉜다.

사용자 확정 사항:
- 리뷰 요약은 **향 / 질감** 섹션으로 분리한다.
- **장단점**은 향·질감과 별개의 **총괄 섹션** — 배송·가격·지속력 등 모든 측면의 ➕/➖를 모은다.
- 향·질감·장단점 중 **언급이 없는 섹션은 그냥 비워둔다**(지어내기 금지, "정보 없음" 패딩 금지).
- 통합 리뷰는 **평균 금지** — 소스 일치/불일치를 드러내는 reconciliation.
- 홍보성(promo) 후기는 이 블록 밖(별도 `promo_view`, 현행 유지).

목표 카드 형태:
```
제품: 레몬커드쉘도넛  ·  마켓: 머머  ·  공식 스펙 ↗ instagram.com/p/…
  풀조합: 플레인+쁠루모+플로우+글루올   향: 레몬마들렌향   비즈: 6mm · 퍼즐비즈
──────────────────────────────────────────────
🟣 인스타 리뷰 (긍정 쏠림 · 8건)
  향     레몬향이 상큼하고 진하다는 반응
  질감   말랑하고 텐션 좋다는 평
  장단점 ➕ 향 지속력·발색·가성비   ➖ 배송 지연
🔵 디시 리뷰 (부정 쏠림 · 5건)
  향     인공적인 레몬향이라는 지적
  질감   (빈칸)
  장단점 ➕ 가성비   ➖ 배송 지연·흐름성 강함
🔀 통합 리뷰
  향     양쪽 다 레몬향 인지 · 인스타=호평/디시=인공향 지적 (gap +0.7)
  질감   인스타만 평가(말랑·텐션), 디시 데이터 없음
  장단점 공통 장점=향 존재감 / 디시발 단점=배송·흐름성
```

## 2. 현재 상태 대비 델타 (근거)

| 필드 | 현재 | 필요 작업 |
|---|---|---|
| 제품명·마켓명 | ✅ `consolidated_view.build_consolidated` → `product` | 없음 |
| 풀조합·향·비즈 | ✅ `official_spec` (`pipeline.consolidated_for` `slime_rag/pipeline.py:233`) | 없음 |
| **공식 스펙 인스타 URL** | ⚠️ fixture `products[].source_permalink` 에 존재하나 **DB 유실** — `specs` 컬럼 없음(`sql/schema.sql:9-18`), `layer1.iter_specs` 6-튜플(`slime_rag/layer1.py:90`)이 permalink 미포함 | **컴포넌트 A** 플러밍 |
| 인스타/디시/통합 리뷰 | ⚠️ 단일 `summary` 블롭 1개(`consolidated_view.py:177`) + `promo_view` | **컴포넌트 B** 속성 분리형 재구성 |

이미 존재하는(재사용) 결정적 집계: `per_source_sentiment`(net·건수·편향라벨), `top_points`(소스별 호평/지적 속성), `sentiment_gap`, `scent_divergence`. Layer2 각 속성은 `perceived`/`sentiment`/`evidence` 스니펫 보유(`slime_rag/extract.py:67-117`) → 요약 그라운딩 재료.

## 3. 설계 결정 (ADR 요약)

- **섹션 생성 방식 = 하이브리드**: 결정적 지표(net·건수·편향라벨·호평/지적)는 코드로 산출, 향/질감/장단점 서술은 LLM이 **해당 소스의 evidence 스니펫만** 보고 요약. 미언급 → `null`(LLM 미호출 or null 반환) → UI 빈칸.
- **소스당 LLM 1콜(structured output)**: 소스별로 `{scent, texture, pros[], cons[]}` 를 한 번에 구조화 산출(`llm_ops.complete(schema=...)` 이미 지원, `slime_rag/llm_ops.py:110`). 향/질감/장단점 3섹션을 개별 호출하지 않음 → 소스당 3콜 대신 1콜. 최대 3콜/제품(인스타·디시·통합).
- **통합 = reconciliation**: 두 소스의 구조화 요약 + `sentiment_gap` + `scent_divergence` 를 입력으로 1콜. 속성별 "일치/갈림"을 서술, 절대 평균 금지. 한 소스만 있으면 통합=`null`(단일 소스 라벨).
- **`summary` 필드 대체**: 단일 `summary` → `review_summaries`(instagram/dcinside/integrated) 구조로 교체. `promo_view` 는 무변경.

## 4. Implementation Steps

### 컴포넌트 A — 공식 스펙 인스타 URL 플러밍
A1. `sql/schema.sql`: specs 에 `source_permalink TEXT` 추가 + 멱등 마이그레이션 `ALTER TABLE specs ADD COLUMN IF NOT EXISTS source_permalink TEXT;`(기존 `beads` 마이그레이션 `sql/schema.sql:55` 패턴 그대로).
A2. `slime_rag/layer1.py:90` `iter_specs`: 7-튜플로 `p.get("source_permalink")` 추가. docstring(`:81`) 갱신.
A3. `slime_rag/pipeline.py`: `_upsert_spec`(`:33`)에 `source_permalink` 파라미터 + INSERT/UPDATE 컬럼(`:41-45`) 추가; `setup` 언팩(`:58`) 7-튜플로. `ingest_hashtag` 의 `extract_spec` 경로(`:136`)는 판매자 캡션 추출이라 permalink는 원 raw 게시물 `permalink` 로 전달(있으면).
A4. `slime_rag/pipeline.py`: `list_products`(`:205`)·`consolidated_for`(`:233`) SELECT 에 `source_permalink` 추가 → `official_spec["source_permalink"]` 로 노출.
A5. `data/layer1_fixture.json`: 시드된 6제품 `products[].source_permalink` 실제 게시물 URL로 채움(현재 샘플). **사용자 입력 필요 시 표시**(없으면 posts[].permalink 재사용 or null).

### 컴포넌트 B — 속성 분리형 소스별 요약 (`consolidated_view.py`)
B1. 새 스키마 상수 `SOURCE_REVIEW_SCHEMA`(strict json_schema):
```json
{"scent": string|null, "texture": string|null, "pros": string[], "cons": string[]}
```
B2. 새 프롬프트 `SECTION_PROMPT`(소스별): "이 소스 후기의 evidence 만 근거. 향/질감은 언급 없으면 null. 장단점은 모든 측면(향·질감·배송·가격·지속력 등)의 ➕/➖ 를 pros/cons 배열로. 지어내기 금지."
B3. 새 프롬프트 `INTEGRATED_PROMPT`: 두 소스 구조화 요약 + `sentiment_gap` + `scent_divergence` 입력 → 속성별 일치/갈림 서술, 평균 금지, 한 소스만 있으면 null 신호.
B4. 새 함수 `_source_material(reviews_of_source) -> dict`: 소스 후기에서 속성별 `perceived`/`sentiment`/`evidence` 스니펫을 모아 LLM 입력 payload 구성(결정적, 코드).
B5. `build_consolidated`(`:141`) 개편:
    - `genuine` 을 플랫폼별로 분리(instagram/dcinside).
    - 각 소스: 후기 있으면 `llm_sectionize(SECTION_PROMPT+payload, SOURCE_REVIEW_SCHEMA)` → `{scent,texture,pros,cons}`; 없으면 `None`.
    - 통합: 두 소스 모두 있으면 `llm_sectionize(INTEGRATED_PROMPT+양측요약+gap, SOURCE_REVIEW_SCHEMA)`; 아니면 `None`.
    - 반환 `view["review_summaries"] = {"instagram":…, "dcinside":…, "integrated":…}`; 기존 `view["summary"]` 제거(또는 integrated 로 백필 호환 — 아래 결정).
    - `by_source`/`praised`/`criticized`/`sentiment_gap`/`scent_divergence`/`promo_view` 무변경.
B6. 콜백 시그니처: 현재 `llm_summarize: Callable[[str],str]`(텍스트 전용). structured 지원 위해 `llm_sectionize: Callable[[str, dict], dict]` 파라미터 추가(구프롬프트, 스키마)→dict. `promo_view` 는 기존 `llm_summarize` 유지(텍스트 요약 그대로) 또는 동일 방식 이관은 범위 밖.

### 컴포넌트 C — 파이프라인·UI 연결
C1. `slime_rag/pipeline.py:245-250` `consolidated_for`: `llm_sectionize = lambda prompt, schema: LLM().complete(prompt, model=settings.model_judge, schema=schema, label="consolidated.section")` 주입.
C2. `app/ui.py:80-82` `_render_consolidated`: `view["summary"]` 렌더 → `review_summaries` 3블록 렌더로 교체. 각 블록: 헤더(플랫폼+편향라벨+건수) + 향/질감(빈 값은 섹션 자체 생략) + 장단점(➕ pros / ➖ cons, 둘 다 비면 생략). `official_spec["source_permalink"]` 있으면 스펙 헤더에 링크.
C3. `slime_rag/pipeline.py:271-275` 데모 출력·`consolidated_view.py:203` `__main__` 셀프테스트를 새 구조로 갱신.

### 컴포넌트 D — 테스트 (오프라인, 라이브 LLM 불요)
D1. `eval/` 에 `test_consolidated_sections.py`: 가짜 `llm_sectionize`(고정 dict 반환) 주입 → (a) 향 언급 0 → `scent=None` → UI 빈칸, (b) 단일 소스 → `integrated=None`, (c) 홍보성 분리 유지 회귀. `build_consolidated` 결정적 부분(by_source/gap/top_points)은 LLM 없이 검증.
D2. `app/ui.py` 헤드리스 AppTest 로 새 렌더 경로 예외 0 확인(기존 검증 패턴).
D3. `python -m pytest eval/ -q` 그린 확인(베이스라인 회귀 없음).

## 5. 미결 결정 (실행 전 확인)

- **`summary` 필드 하위호환**: 완전 제거(모든 참조 갱신) vs `summary`=integrated 프로즈로 백필. → **제거 권장**(참조처는 ui·pipeline demo·__main__ 3곳뿐, 검색으로 전수 확인). 사용자 확인.
- **fixture `source_permalink` 실제 URL**(A5): 사용자만 제공 가능. 없으면 posts[].permalink 재사용 또는 null(UI 링크 생략)로 그레이스풀.
- **promo_view 도 속성 분리** 적용 여부: 기본 범위 밖(현행 텍스트 요약 유지). 원하면 컴포넌트 B와 동일 스키마 재사용.

## 6. Risks & Mitigations

| 리스크 | 완화 |
|---|---|
| LLM이 빈 섹션에 억지 서술(지어내기) | 스키마 `null` 허용 + 프롬프트 "evidence 없으면 null" + D1 테스트로 0-언급→null 강제 검증 |
| 소스당 1콜 × 3 = 제품당 최대 3 LLM콜(비용) | `llm_ops` LEDGER 로 비용 로깅(기존), 후기 0 소스는 콜 스킵, structured 1콜로 3섹션 동시 산출 |
| 통합 리뷰가 평균처럼 읽힘 | 프롬프트에 "평균 금지·gap 명시", 입력에 `sentiment_gap` 수치 동봉, reconciliation 문구 강제 |
| specs 마이그레이션이 기존 배포 DB 깨뜨림 | `ADD COLUMN IF NOT EXISTS`(NULL 허용) 멱등 — 기존 행 무해(beads 선례) |
| `summary` 제거가 숨은 참조 깨뜨림 | `grep -rn "\[.summary.\]\|\.summary\|view\[.summary" app slime_rag eval` 전수 후 갱신 |

## 7. Verification Steps

1. `grep` 로 `summary` 참조 전수 → 전부 `review_summaries` 로 이관 확인.
2. `python -m slime_rag.consolidated_view` 셀프테스트: 향 없는 소스 → 향 섹션 null.
3. `python -m slime_rag.layer1` : iter_specs 가 permalink 포함 7-튜플 산출.
4. `python -m slime_rag.pipeline`(pgvector 55432, .venv): setup→specs.source_permalink 적재→`consolidated_for` 가 URL + review_summaries(3블록) 반환.
5. `python -m pytest eval/ -q` 그린.
6. `app/ui.py` AppTest 헤드리스: 카드 렌더 예외 0, 빈 섹션 생략 확인.

## 8. Acceptance Criteria (testable)

- [ ] `specs` 에 `source_permalink` 컬럼 존재, `consolidated_for(...)["official_spec"]["source_permalink"]` 반환(값 또는 None).
- [ ] `consolidated_for(...)["review_summaries"]` 가 `instagram`/`dcinside`/`integrated` 키 보유.
- [ ] 각 소스 요약이 `{scent, texture, pros, cons}` 형태, 향/질감 미언급 시 해당 값 `None`(빈칸).
- [ ] 통합 요약이 두 소스 모두 있을 때만 dict, 아니면 `None`.
- [ ] 홍보성 후기는 `review_summaries` 에 미포함, `promo_view` 는 회귀 없음.
- [ ] `pytest eval/ -q` 전부 통과, UI AppTest 예외 0.
- [ ] 향 언급 0인 소스에 대해 LLM이 향 서술을 지어내지 않음(D1 고정-목 테스트로 검증).
