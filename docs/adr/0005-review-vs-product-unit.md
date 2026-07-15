# ADR-0005 — 후기(주문) 단위 vs 제품 단위 분리

**상태:** Accepted

## 맥락
한 후기 글에 여러 제품이 섞이고(비교글), 배송·CS 는 주문 전체에 걸린다. 한 덩어리로 추출하면
유령 제품이 생기거나 마켓 정보가 유실된다.

## 결정
- `market`·`shipping_cs` 는 **최상위**(1주문 = 1마켓).
- 제품별 평가는 `reviews[]` 배열 — 비교글은 제품 수만큼 항목 분리.
- 한쪽 전용 단점은 다른 제품에 **복제 금지**.

## 근거
- **Why:** 스키마로 유령 제품·마켓 유실을 원천 차단. 제품 1개 = 1행 = 1청크로 색인 단위와도 정렬.

## 영향
- `slime_rag/extract.py` 의 `LAYER2_SCHEMA`/`LAYER2_SYSTEM` 이 이 분리를 강제.
- DB: `reviews` 행 = 제품 항목 1개, `attributes` JSONB 에 원본 항목 전체 보존([../../sql/schema.sql](../../sql/schema.sql)).
