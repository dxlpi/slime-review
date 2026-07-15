# Plan — 1층 스펙에 비즈(beads) 섹션 추가

> Status: **✅ DONE (2026-07-15)** — 승인 후 구현·라이브 검증 완료.
> 검증: 저장 raw 재추출(재스크랩 $0, LLM ~$0.015/run) → 머머 7제품 전원 beads 정상, 위즈캔디샵→[지렁이비즈], 어떤 비즈도 제품행 안 됨. 오프라인 스키마 strict 테스트·UI headless AppTest green.
> 보너스 수정(재추출 중 발견): (1) '샵/캔디' 든 고유태그를 shop 으로 오인해 드롭하던 회귀 → 무시목록 좁힘(#위즈캔디샵=제품). (2) LLM 이 향료어를 유령 제품(에그노그)으로 분리하던 비결정 → **결정적 제품 게이트**(제품명은 캡션 해시태그여야 저장) 추가. 둘 다 [[layer1-product-name-hashtag-rule]] 반영.
> ~~Status: pending approval~~
> Scope: Layer-1 official spec 에 제품 구성요소인 **비즈(beads)** 를 1급 필드로 추가한다.
> 관련 메모: [[layer1-base-combo-scent-separation]], [[layer1-product-name-hashtag-rule]], [[layer1-mm-size-means-deform]]

## 요구 요약 (사용자 지시)
- 비즈는 제품이 아니라 **구성요소**지만, 구매 결정의 중요 요인 → **별도 섹션**으로 노출.
- 비즈 **없는** 슬라임도 있다 → 없을 때는 빈 값(회귀 없음).
- 비즈는 **오픈 어휘** — 마켓마다 자체 제작·명칭 상이(지렁이비즈·나뭇잎비즈·퍼즐비즈 등) → enum 금지, 캡션 표기 그대로.
- 비즈는 **자기 product 행이 되면 안 됨**(이전에 지렁이비즈가 유령 제품으로 샜던 버그 방지) — 해당 제품의 `beads` 필드로만 귀속.

## 핵심 설계 결정 (확정 제안)
1. **데이터 타입 = `TEXT[]`(Postgres 배열)**. 이유: 비즈는 진짜 리스트(제품당 0~N개)이고, `slime_type` 처럼 콤마결합 TEXT 로 뭉개면 개별 비즈 필터/집계가 어렵다. psycopg3 가 `list[str] ↔ text[]` 를 native 매핑. 스키마 값 표현은 `[]`(없음) — Layer-2 `texture.feel` 의 "없으면 []" 관례와 일치, nullable 스칼라보다 회귀 안전.
2. **비즈만으로는 제품이 아니다** — `ingest_hashtag`/`reextract` 의 all-null 백스톱은 **여전히 `scent/base_combo/slime_type` 3필드 기준 유지**. 즉 이 3개가 전부 null 이면 드롭(비즈가 있어도). beads 는 '이미 진짜인 제품'에 붙는 **부가 메타**일 뿐. → 위즈캔디샵의 지렁이비즈는 위즈캔디샵 제품행의 `beads` 로 살아나지만, 비즈 단독 노이즈는 제품행이 되지 않는다.
3. **LLM 추출 규칙 전환** — 현재 `LAYER1_SYSTEM`(extract.py:205-206)은 "비즈…별도 product 로 만들지 말 것(base_combo/설명 일부로만 취급)". 이걸 **"별도 product 금지 + `beads` 필드에 캡션 표기 그대로 배열로 담기(오픈 어휘), 없으면 []"** 로 교체. base_combo 에는 비즈를 넣지 않는다(base_combo=풀 재료만, memo layer1-base-combo-scent-separation 유지).

## 구현 단계 (파일별)

### 1. `slime_rag/extract.py` — 추출 스키마 + 프롬프트
- **1a. `_SPEC_PROPS`(extract.py:179-188)** 에 `beads` 추가:
  ```python
  "beads": {"type": "array", "items": {"type": "string"},
            "description": "제품에 포함된 비즈/토핑 구성요소 목록(오픈 어휘, 마켓별 명칭 상이: "
                           "지렁이비즈·나뭇잎비즈·퍼즐비즈·별비즈 등). 캡션 표기 그대로. 비즈 없으면 []."},
  ```
  - strict json_schema 준수: `_obj` 가 `required=list(properties)` 로 전 필드 required 처리(extract.py:48-50) → `beads` 자동 포함. 배열+string items 는 strict 허용. **추가 조치 불필요**(검증은 self-test 에서).
  - 필드 순서: `slime_type` 다음, `evidence` 앞에 배치(스펙 축 → 구성요소 → 근거).
- **1b. `LAYER1_SYSTEM`(extract.py:205-206) 비즈 라인 교체**:
  - 기존: `비즈·토핑·파츠(...)는 제품이 아니라 구성요소다 → 별도 product 로 만들지 말 것(해당 제품의 base_combo/설명 일부로만 취급).`
  - 신규(요지): 비즈·토핑·파츠는 제품이 아니다 → **별도 product 금지**. 대신 해당 제품의 **`beads` 배열**에 캡션 표기 그대로 넣는다(예: `지렁이비즈`, `퍼즐비즈`). base_combo 에는 넣지 마라(base_combo=풀 재료만). 비즈가 없으면 `beads=[]`.
  - `[뽑을 것]` 블록(214-222)에 `beads:` 항목 한 줄 추가.
- **1c. self-test**: 파일 하단 `if __name__ == "__main__"` 에 비즈 캡션(예: "8mm 25g+지렁이비즈 20g #위즈캔디샵") → `beads=["지렁이비즈"]` & 제품행 1개(비즈 별도행 없음) 확인 케이스 추가.

### 2. `sql/schema.sql` — DB 컬럼 (멱등)
- `CREATE TABLE specs`(schema.sql:9-) 블록에 `beads TEXT[] DEFAULT '{}',` 컬럼 추가(신규 DB 용).
- 하단 멱등 마이그레이션(reviews 의 `ALTER … ADD COLUMN IF NOT EXISTS` 패턴, schema.sql:50 과 대칭)에 기존 DB 용:
  ```sql
  ALTER TABLE specs ADD COLUMN IF NOT EXISTS beads TEXT[] NOT NULL DEFAULT '{}';
  ```
- 배열 원소 필터가 필요해지면 GIN 인덱스는 후속(YAGNI, 지금은 생략).

### 3. `slime_rag/pipeline.py` — upsert / ingest / view 통로
- **3a. `_upsert_spec`(pipeline.py:33-44)** 시그니처에 `beads` 추가: `_upsert_spec(cur, market, product, scent, base_combo, stype, beads=None)`.
  - INSERT/UPDATE 에 `beads` 컬럼 추가, 값은 `beads or []`(None→빈배열). `ON CONFLICT … DO UPDATE SET … beads=EXCLUDED.beads`.
- **3b. `load_specs`(pipeline.py:47-58)** 호출부: `iter_specs` 가 beads 를 함께 yield → 언패킹 6-튜플로 확장, `_upsert_spec(..., beads)` 전달.
- **3c. `ingest_hashtag` 판매자 루프(pipeline.py:108-126)**: 백스톱(122줄 `any(p.get(k) for k in ("scent","base_combo","slime_type"))`)은 **그대로**(설계 결정 2). upsert 호출(124-125)에 `p.get("beads")` 추가.
- **3d. `list_products`(pipeline.py:198-200)** & **`consolidated_for`(pipeline.py:221-237)** SELECT 에 `beads` 추가 → `official_spec` dict 에 `"beads": row[...]` 포함(consolidated_for:230). `list_products` 결과 dict 에도 `beads` 노출.

### 4. `slime_rag/layer1.py` — fixture → specs 매핑
- **`iter_specs`(layer1.py:78-90)**: 현재 `(mw, product_name, official_scent, glue_composition, slime_type)` 5-튜플 yield → `beads` 를 6번째로 추가: `p.get("beads", [])`.
- fixture product 스키마(layer1.py:15 주석 + `data/layer1_fixture.json`)에 optional `beads` 필드 문서화.

### 5. `data/layer1_fixture.json` — 기존 6제품 backfill
- 각 product 에 `beads` 추가(캡션/evidence 에서 복원). 예: `레몬커드쉘도넛` → `evidence.type` 에 "퍼즐비즈15g" 있음 → `beads: ["퍼즐비즈"]`. 비즈 근거 없는 제품 → `beads: []`.
- 목적: fixture↔스크랩 스키마 대칭 유지, 종합뷰에서 비즈 노출 회귀 방지.

### 6. `slime_rag/consolidated_view.py` — 비즈 노출
- `official_spec` 를 받는 경로(consolidated_view.py:142-155 근처)에서 `beads` 를 요약/뷰 출력에 포함(구매 결정 요인이므로 headline 스펙 옆에 노출). LLM 요약 프롬프트에는 필요 시 "공식 구성 비즈: …" 한 줄만 컨텍스트로 주입(집계/판정은 코드, LLM 은 표기만).

### 7. 스크랩 재추출 검증 — `scratchpad/reextract_murmur.py` + `data/specs_murmur_scraped.json`
- `reextract_murmur.py`: 추출 결과 record 에 `beads` 캐리(`p.get("beads")`), `_upsert_spec(..., p.get("beads"))` 전달, JSON 출력 라인에 beads 표시.
- **저장된 raw 스냅샷에서 재추출($0 재스크랩)** → 검증 기대치:
  - `위즈캔디샵` 제품행의 `beads` 에 `지렁이비즈` 포함(이전엔 드롭됐던 것 부활).
  - 어떤 비즈도 **자기 제품행이 되지 않음**(제품 수는 이전 클린 결과와 동일, beads 만 채워짐).
  - `첵스초코딸기빙수_딸기` 등 비즈 근거 있으면 채워지고, 없으면 `[]`.
- `specs_murmur_scraped.json` 재기록 + DB 교체(기존 스크랩 삭제→재upsert, fixture 보존) — 기존 스크립트 로직 재사용.

### 8. 테스트 — `eval/test_bias.py` 인접 / extract self-test
- extract self-test(1c)로 strict 스키마 + 비즈 귀속 확인.
- `_assert_strict` 계열 검증에 `beads` 가 required/additionalProperties:false 를 깨지 않는지 확인(배열 필드 strict 준수).

## 승인 기준 (Acceptance Criteria — 테스트 가능)
- [ ] `LAYER1_SCHEMA` 가 strict json_schema 검증 통과(모든 object required 완전, `beads` 포함). extract self-test green.
- [ ] `beads` 없는 캡션 추출 시 `beads == []`(null 아님), 기존 제품 추출 회귀 없음(scent/base_combo/slime_type 동일).
- [ ] 비즈 문구만 있고 스펙 3필드 all-null 인 아이템은 **제품행 미생성**(백스톱 유지) — 지렁이비즈 유령 제품 재발 0.
- [ ] `specs` 테이블에 `beads TEXT[]` 존재(신규 compose + 기존 DB `ALTER … IF NOT EXISTS` 둘 다), 멱등(2회 적용 무오류).
- [ ] `reextract_murmur.py` 재실행 → 위즈캔디샵 `beads=[지렁이비즈]`, 제품 수 불변, JSON+DB 반영.
- [ ] `consolidated_for(market, product)` 반환 `official_spec["beads"]` 가 DB 값과 일치.
- [ ] fixture 6제품에 `beads` 존재(있는 것/`[]` 명시), `load_specs` upsert 무오류.

## 위험 & 완화
| 위험 | 완화 |
|---|---|
| LLM 이 비즈를 base_combo 에도 중복 기입 | 프롬프트에 "base_combo 에 비즈 금지" 명시(기존 규칙 강화) + self-test 로 회귀 감시 |
| 비즈가 별도 제품행으로 재유출 | all-null 백스톱을 3필드 기준으로 유지(설계 결정 2), 프롬프트 "별도 product 금지" 유지 |
| `TEXT[]` psycopg 매핑 이슈 | upsert 에 `beads or []` 로 None 방지, 재추출 스크립트로 라운드트립 검증 |
| 기존 DB 마이그레이션 누락 | `ALTER … ADD COLUMN IF NOT EXISTS … DEFAULT '{}'` 로 기존 행 자동 `{}` 채움 |
| 오픈 어휘 표기 흔들림(지렁이비즈 vs 지렁이 비즈) | 지금은 캡션 verbatim 저장(정규화는 후속 — linking/약칭사전과 함께 다룸, YAGNI) |

## 검증 단계
1. `PYTHONPATH=. python -m slime_rag.extract`(self-test) → 비즈 케이스 green.
2. compose 재기동 또는 `psql`로 `ALTER` 적용 → `\d specs` 에 `beads` 확인.
3. `PYTHONPATH=. python scratchpad/reextract_murmur.py` → 콘솔에 위즈캔디샵 beads, 제품 수 불변 확인.
4. `PYTHONPATH=. python -c "from slime_rag import pipeline; print(pipeline.consolidated_for('머머','위즈캔디샵')['official_spec'])"` → beads 노출.
5. LLM 비용 = 재스크랩 0 + 재추출 ~$0.02(저장 raw 사용).

## 범위 밖 (후속)
- 비즈 명칭 정규화/약칭 사전(§11-C aliases) — 지금은 verbatim.
- 비즈 GIN 인덱스/필터 UI — 수요 생기면.
- 나머지 데모 마켓 fixture beads backfill — 게시물 붙일 때 동일 방식.
