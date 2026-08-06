# Architecture Decision Records

구조적 결정과 그 근거. 새 결정은 다음 번호로 파일을 추가한다(`NNNN-slug.md`).

| # | 결정 | 상태 |
|---|---|---|
| [0001](0001-embedding-and-vectorstore.md) | 임베딩 BGE-M3 + 벡터스토어 pgvector | Accepted |
| [0002](0002-source-bias-first-class.md) | 소스 편향을 1급 기능으로(평균 금지) | Accepted |
| [0003](0003-ig-businessdiscovery-fixture.md) | IG 1층은 fixture(App Review 차단) | Accepted |
| [0004](0004-promo-gate-llm-cascade.md) | 홍보성 판정 게이트→LLM 캐스케이드 | Accepted |
| [0005](0005-review-vs-product-unit.md) | 후기(주문) 단위 vs 제품 단위 분리 | Accepted |
| [0006](0006-mqe-three-axis-relevance.md) | 관련성 `kind` 4분류 → M/Q/E 독립 이진 3축 | Accepted |
| [0007](0007-collected-for-target-policy.md) | `collected_for` 타깃 방침 — 플랫폼별 scope | Accepted (dcinside 활성화 보류) |
| [0008](0008-drop-value-add-shipping-section.md) | `value` 축 제거 + 배송·CS 섹션 승격 | Accepted |
| [0009](0009-source-links-and-owner-media.md) | 원문 링크는 참조 — 식별자 저장 + 판매자 미디어만 임베드 | Accepted |

배경 흐름: [../../ARCHITECTURE.md](../../ARCHITECTURE.md) · 도메인 규칙: [../../MEMORY.md](../../MEMORY.md).
