# MEMORY — 도메인 암묵지 & 의사결정 근거

코드만 봐서는 알 수 없는 슬라임 도메인 규칙과 "왜 이렇게 했나"를 레포 안에 외부화한다.
구조적 결정은 [docs/adr/](docs/adr/) 의 번호 매긴 레코드로, 반복 적용되는 도메인 규칙은
아래에 둔다. 아키텍처 흐름은 [ARCHITECTURE.md](ARCHITECTURE.md).

## 통제 어휘 (도메인)
- **슬라임 종류(TYPE_ENUM, 1층)**: 폼볼, 촉감류(점토), 디폼, 난사, 눈꽃, 지글리, 크런치,
  빈백, 클라우드, 샤베트, 클리어, 버글리, 젤라또 (확장 가능).
- **질감 서술어(FEEL_VOCAB, 2층)**: 말랑·말캉·쫀득·퐁신·폭닥·크리미·로션크리미·얄랑·매트·
  빳빳·텐션감있는·흐물거리는·쳐지는·흐름성있는 + `~같은` 비유 + 신규어(feel_other).

## 속성 분류 규칙 (사람이 가르친 것)
- **Why:** "걀걀거림"은 **sound**(소리)지 질감이 아니다 — texture 로 태깅하면 오답.
- **Why:** "지속력"은 제품 속성이지 **배송(shipping)** 과 무관 — 섞지 말 것.
- **주의:** 향 불일치/소스 갭은 LLM 이 아니라 조인·집계에서 계산(`consolidated_view.py`).

## 홍보성 vs 구매 (업계 규칙, 사람이 가르친 것)
- **구매(홍보성 아님)**: 할인·비매·서비스·세일 — 단독으론 그냥 구매 맥락.
- **홍보성**: 서포터(즈)·체험단·협찬·무상 제공·PPL — `review_class='promo'` 버킷으로 분리.
- **Why:** 순수 구매어를 게이트에서 제외해야 recall 손실 없이 오탐을 막는다. 상세 [ADR-0004](docs/adr/0004-promo-gate-llm-cascade.md).

## 1층 스펙 추출 규칙
- **제품명 = 제품 고유 해시태그에서만.** 해시태그 없는 비매품/공지글은 미출시라 저장 안 함.
- **base_combo = 풀 재료만** — 향료·비즈·규격 제외. 캡션 ` / ` 앞=풀조합, 뒤=향료.
- **mm 규격(6mm·8mm…)** = 디폼 알갱이 크기 → `slime_type=디폼`(기본).
- **beads** = 비즈/토핑 구성요소(오픈 어휘 배열). 구매결정 요인이라 1급 필드로 분리 —
  별도 product 행 금지, base_combo 에도 넣지 않음. 없으면 `[]`.
- 무시 태그: 마켓 자기이름 + 광역 슬라임어뿐. '샵/캔디' 든 고유태그(#위즈캔디샵)는 제품명.

## 개체연결 (linking)
- **Why:** 초성 충돌 12그룹(ㅁㅁ, ㅇㅇ, ㄴㅈ 등) 존재 → 확신도 0.6 미만이면 **abstain**.
- **Gotcha:** 추출기가 제품명 대신 종류단어(예: "촉감류")를 잡으면 조인 실패 → 약칭 사전으로 보강.
- **Note:** `index.py` 는 raw mentioned_product 가 아니라 `linking` 정규화 결과(`lk.product`)를 저장해야 함(과거 버그).

## 수집 (합법성)
- 디시는 익명 UGC·비재배포·robots 준수라 가장 방어 가능한 스크랩(면접 포인트).
- Apify 해시태그는 공개 데이터, `meta.source=apify`/`scraped=True` 라벨링. `APIFY_TOKEN` 만 있으면 무료/제한 플랜도 동작.
- IG 공식 `business_discovery` 는 App Review 벽 → 1층은 fixture. [ADR-0003](docs/adr/0003-ig-businessdiscovery-fixture.md).

## 확정된 스택 결정 → docs/adr/
- 임베딩 BGE-M3 / 벡터스토어 pgvector → [ADR-0001](docs/adr/0001-embedding-and-vectorstore.md)
- 소스 편향은 1급 기능(평균 금지) → [ADR-0002](docs/adr/0002-source-bias-first-class.md)
- 후기(주문) 단위 vs 제품 단위 분리 → [ADR-0005](docs/adr/0005-review-vs-product-unit.md)
