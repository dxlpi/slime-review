# ADR-0001 — 임베딩 BGE-M3 + 벡터스토어 pgvector

**상태:** Accepted

## 맥락
한국어 후기 검색 + 1층 스펙↔2층 후기 조인 + 메타필터(마켓/종류/소스)가 동시에 필요.

## 결정
- **임베딩: BGE-M3**(로컬). 한국어 친화 + dense/sparse 동시 지원 → 하이브리드를 한 모델로. 콜당 비용 0.
- **벡터스토어: pgvector**(Postgres). 1층↔2층 조인과 메타필터를 SQL 로 처리. Chroma 안은 폐기.
- 청킹: 후기 1개 = 1청크. 검색: 하이브리드(dense + kiwipiepy/BM25 RRF) + 메타필터.

## 근거
- **Why pgvector:** 관계형 조인(`reviews.spec_id → specs.id`)과 벡터 검색을 한 엔진에서 —
  별도 벡터 DB + RDB 이중화 회피. 스키마는 [../../sql/schema.sql](../../sql/schema.sql).
- **Why BGE-M3:** 임베딩 한 번으로 dense + sparse 를 얻어 하이브리드 파이프라인 단순화.

## 영향
- `embedding vector(1024)` 차원 고정 — 임베딩 모델 교체 시 컬럼·HNSW 인덱스 재생성 필요.
- 적재 `slime_rag/index.py`, 조회 `slime_rag/search.py`.
