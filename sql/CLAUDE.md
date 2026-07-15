# sql/ — pgvector 스키마

## Purpose (이 모듈이 소유하는 것)
1층 스펙(`specs`) ↔ 2층 후기(`reviews`)의 관계형 그라운드 트루스. 검색은 메타필터 +
dense 벡터(BGE-M3) + BM25(앱단)로 하이브리드. `docker-compose.yml` 이 이 스키마로 초기화.

## Key files
| 파일 | 역할 |
|---|---|
| `schema.sql` | 전체 DDL — `specs`, `reviews`, HNSW/메타 인덱스, 멱등 마이그레이션 |

## Schema 요약
- `specs (market, product, scent, base_combo, slime_type, beads[])` · UNIQUE(market, product)
- `reviews (source, market, product, spec_id→specs, review_class, attributes JSONB, embedding vector(1024), tokens[])`
- 조인 키: `reviews.spec_id → specs.id`. 메타필터 컬럼: `market`, `slime_type`, `source`, `review_class`.

## Common patterns (workflow)
```bash
docker compose up -d              # 포트 55432 로 pgvector 기동 + schema.sql 초기화
psql postgresql://localhost:55432 -f sql/schema.sql   # 수동 재적용(멱등)
```
- 컬럼 추가는 항상 `ADD COLUMN IF NOT EXISTS ... DEFAULT` 로 — 기존 배포 DB 무중단 마이그레이션.

## Non-obvious (주의 / Gotcha)
- **Important:** 원문 미재배포 — 본문 전체가 아니라 `evidence` 스니펫 + 임베딩만 저장(저작권).
- **Note:** `review_class` 기본 `'genuine'` — 홍보성(`'promo'`)은 별도 버킷으로 집계 분리(평균 금지).
- **Warning:** `embedding` 은 1024차원 고정(BGE-M3). 임베딩 모델 바꾸면 차원·인덱스 재생성 필요.
- **Note:** 후기 규모 커지면 HNSW 가 IVFFlat 보다 관리 쉬움(현재 HNSW).

## Cross-module dependencies
- ← [`../slime_rag/`](../slime_rag/CLAUDE.md): `db.py`(연결), `index.py`(적재), `search.py`(조회)
- 컬럼 의미의 근거: [../MEMORY.md](../MEMORY.md) (후기/제품 단위 분리, beads 필드 결정)
