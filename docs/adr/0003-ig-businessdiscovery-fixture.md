# ADR-0003 — IG 1층은 fixture (business_discovery 차단)

**상태:** Accepted

## 맥락
1층 공식 스펙은 마켓 인스타 게시물에서 나온다. 인스타 Graph `business_discovery` 는
advanced access(App Review) 를 요구해 데모 범위 밖.

## 결정
1층은 **fixture/수동 시드로 확정 전환**([../../data/layer1_fixture.json](../../data/layer1_fixture.json),
현재 3마켓 6제품). `InstagramSource` 는 인터페이스만 설계 보존 — App Review 통과 시에만 라이브.

## 근거
- **Why:** App Review 는 데모 타임라인 밖의 승인 절차. 인터페이스를 남겨두면 통과 즉시 fixture→라이브 스위치.
- 2층 유저후기(해시태그)는 별개 — Apify 스크래퍼로 라이브 동작(무료/제한 플랜 포함).

## 영향
- `slime_rag/layer1.py` 가 fixture 로더 + `seed_kb_products` + `iter_specs`.
- 나머지 데모 마켓은 게시물만 붙이면 동일 방식으로 확장.
