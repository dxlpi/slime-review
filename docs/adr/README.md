# Architecture Decision Records

구조적 결정과 그 근거. 새 결정은 다음 번호로 파일을 추가한다(`NNNN-slug.md`).

| # | 결정 | 상태 |
|---|---|---|
| [0001](0001-embedding-and-vectorstore.md) | 임베딩 BGE-M3 + 벡터스토어 pgvector | Accepted |
| [0002](0002-source-bias-first-class.md) | 소스 편향을 1급 기능으로(평균 금지) | Accepted |
| [0003](0003-ig-businessdiscovery-fixture.md) | IG 1층은 fixture(App Review 차단) | Accepted |
| [0004](0004-promo-gate-llm-cascade.md) | 홍보성 판정 게이트→LLM 캐스케이드 | Accepted |
| [0005](0005-review-vs-product-unit.md) | 후기(주문) 단위 vs 제품 단위 분리 | Accepted |

배경 흐름: [../../ARCHITECTURE.md](../../ARCHITECTURE.md) · 도메인 규칙: [../../MEMORY.md](../../MEMORY.md).
