# sql/ — pgvector 스키마

## Purpose (이 모듈이 소유하는 것)
1층 스펙(`specs`) ↔ 2층 후기(`reviews`)의 관계형 그라운드 트루스. 검색은 메타필터 +
dense 벡터(BGE-M3) + BM25(앱단)로 하이브리드. `docker-compose.yml` 이 이 스키마로 초기화.

## Key files
| 파일 | 역할 |
|---|---|
| `schema.sql` | 전체 DDL — `specs`, `reviews`, HNSW/메타 인덱스, 멱등 마이그레이션 |

## Schema 요약
- `specs (market, product, scent, base_combo, slime_type, official_texture, beads[])` · UNIQUE(market, product)
  — `official_texture` = 판매자가 캡션에 쓴 **질감 서술의 요약**(1~2문장). `slime_type` 은 '무슨
    종류인가'(통제어휘), 이건 '만지면 어떤가'다. 둘을 한 칸으로 합치지 말 것 — 합친 상태가
    화면의 '질감' 줄에 분류코드만 뜨던 원인이었다. 미언급이면 NULL.
- `reviews (source, market, product, spec_id→specs, review_class, attributes JSONB, relevance_meta JSONB, source_ref JSONB, embedding vector(1024), tokens[])`
  — `relevance_meta` = 관련성 게이트 판정 전문(M/Q/E·topic_score·target/target_scope/τ·rank_score, ADR-0007). 게이트 미경유 행은 NULL.
  — `source_ref` = 원문 조각 식별자 `{platform,url,thread_no,comment_no,shortcode}`(ADR-0009). URL 이 아니라 **식별자**를 저장하고 렌더는 `slime_rag/source_links.py` 가 한다. 미보유 행은 NULL.
- 조인 키: `reviews.spec_id → specs.id`. 메타필터 컬럼: `market`, `slime_type`, `source`, `review_class`.

## Common patterns (workflow)
```bash
docker compose up -d              # 포트 55432 로 pgvector 기동 + schema.sql 초기화
psql postgresql://localhost:55432 -f sql/schema.sql   # 수동 재적용(멱등)
```
- 컬럼 추가는 항상 `ADD COLUMN IF NOT EXISTS ... DEFAULT` 로 — 기존 배포 DB 무중단 마이그레이션.

## Non-obvious (주의 / Gotcha)
- **Important:** 원문 본문은 **저장한다**(ADR-0013 — 저장·처리는 허용). 제한이 걸리는 곳은 DB 가
  아니라 **표시**다: 화면에 나가는 건 서버에서 자른 발췌다. 수집물은 DB 에만 두고 git 에 커밋하지
  않는다. `source_ref` 는 여전히 **주소만** 담는다(참조지 복제가 아님, ADR-0009).
- **Warning:** `source_ref` 는 **백필하지 않는다**. 기존 행은 NULL 로 남고 `index_gold` 가 존재
  `post_id` 를 스킵하므로 `setup(reset=False)` 로는 안 채워진다 → 데모 DB 는 `setup(reset=True)` 재적재.
  ⚠️ `setup()` 이 재생성하는 건 fixture+골드뿐이라 **라이브 수집분은 리셋으로 사라진다**(git 에 없음).
- **Note:** `review_class` 기본 `'genuine'` — 홍보성(`'promo'`)은 별도 버킷으로 집계 분리(평균 금지).
- **Warning:** `embedding` 은 1024차원 고정(BGE-M3). 임베딩 모델 바꾸면 차원·인덱스 재생성 필요.
- **Note:** 후기 규모 커지면 HNSW 가 IVFFlat 보다 관리 쉬움(현재 HNSW).
- **Important:** `review_summaries` 는 **행이 두 종류**다([ADR-0015](../docs/adr/0015-market-scope-order-criteria.md)):
  `product IS NOT NULL` = 제품 축 요약, `product IS NULL` = 그 마켓의 **주문 축**(고객 응대·배송)
  요약. PK 는 NULL 을 못 담아 **부분 유니크 인덱스 둘**로 바꿨다 — `ON CONFLICT` 도 인덱스의
  `WHERE` 절을 그대로 붙여야 맞는 인덱스를 고른다(`pipeline._store_summary`).
  ⚠️ 마이그레이션 **순서 주의**: PK 를 먼저 떼야 `product` 의 NOT NULL 을 풀 수 있다.
  **Don't:** `product=''` 센티널로 되돌리지 말 것 — 빈 문자열은 '이름이 빈 제품'과 구별되지 않는다.

## Cross-module dependencies
- ← [`../slime_rag/`](../slime_rag/CLAUDE.md): `db.py`(연결), `index.py`(적재), `search.py`(조회)
- 컬럼 의미의 근거: [../MEMORY.md](../MEMORY.md) (후기/제품 단위 분리, beads 필드 결정)
