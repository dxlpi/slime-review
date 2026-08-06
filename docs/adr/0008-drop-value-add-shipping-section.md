# ADR-0008 — `value`(가성비) 축 제거, 배송·CS 를 요약 1급 섹션으로

**상태:** Accepted (2026-08-05, 사용자 결정)

## 맥락
2층 제품 단위 평가 축은 향·질감·소리·지속력·가성비(`value`) 다섯이었고, 배송·CS(`shipping_cs`)는
후기(주문) 단위 필드로 따로 있었다([ADR-0005](0005-review-vs-product-unit.md)). 실제 사용에서
드러난 비대칭:

- `value` 는 표시층에 도달하지 못했다. 요약 섹션은 향/질감/장단점 넷뿐이라 가성비는 pros/cons 로만
  흘렀고, DB 에도 `value_sentiment` 컬럼이 없어 메타필터 대상도 아니었다(`search._FILTERABLE`).
  `krw` 는 대부분 null 이다 — 후기는 금액보다 "이 가격이면"이라는 판정만 쓴다.
- 관련성 게이트는 이미 가격 푸념을 평가로 안 친다(`relevance_rules.LEXICON` 의 '비싸' 제외 근거).
  축을 유지할수록 스키마 지시문만 길어지는데, 추출 입력의 대부분이 고정 프롬프트다.
- 반대로 배송·CS 는 디시 부정 후기의 큰 축인데도 전용 섹션이 없어 pros/cons 한 줄로 눌렸다.

## 결정
1. **`value` 축 제거** — `_PRODUCT_PROPS`, `ATTR_FIELDS`, `_SALIENT`, `render_review`, 골드에서 삭제.
   가격 언급은 계속 pros/cons 로 요약된다(정보 손실 아님, 전용 축이 없어질 뿐).
2. **`shipping_cs` 는 후기(주문) 단위 그대로 유지** — ADR-0005 불변. 제품 단위로 내리지 않는다.
3. **요약 스키마에 `shipping` 섹션 추가** — `SOURCE_REVIEW_SCHEMA` 가 향/질감/**배송·CS**/장단점 넷.
   소스별·통합·서포터 세 프롬프트 모두 동일 규칙(미언급=null, 지어내기 금지).
4. **`index_post` 가 `shipping_cs` 를 제품별 팬아웃 행에 복제** — 아래 근거 참조.

## 근거
- **Why 1:** 축의 값은 '표시·필터·순위 중 어디에 닿는가'로 판정한다. `value` 는 셋 다 아니었다.
- **Why 3:** 배송·CS 는 소스 편향이 가장 선명하게 갈리는 축이다(인스타=포장 호평 / 디시=지연 지적).
  pros/cons 에 묻으면 1급 기능인 **소스 갭이 한 줄로 뭉개진다** — 섹션으로 올려야 갭이 보인다.
- **Why 4 (발견된 결함):** `index_post` 는 `attributes` JSONB 에 **제품 항목만** 넣는다(`Jsonb(r)`).
  `shipping_cs` 는 doc 최상위에 있어 행에 실리지 않았고, `pipeline._records_for` 는 그 행을 그대로
  읽는다. 즉 `ATTR_FIELDS` 의 `shipping_cs` 항목은 **이 ADR 이전까지 죽은 코드**였다 —
  종합뷰 배송 집계(`top_points`)도 상시 0이었다. `relevance_meta` 와 같은 규칙(조각 단위 속성은
  제품별 팬아웃 행 전체에 복제)을 적용해 고친다.

## 영향
- `slime_rag/extract.py` — `_PRODUCT_PROPS` 에서 `value` 삭제. **`LAYER2_SYSTEM` 무변경**
  (프롬프트가 `value` 를 언급한 적 없음 → 스레드 배치 스키마도 자동 동기화).
- `slime_rag/index.py` — `render_review` 의 `가격:` 조각 → `배송·CS:` 조각. `index_post` 에 복제 추가.
  **주의:** 제품 평가가 0개인 후기(배송만 언급)는 여전히 행이 0개다 — ADR-0005 의 귀결이며
  이 ADR 이 바꾸지 않는다. 배송 단독 후기까지 담으려면 별도 결정이 필요하다.
- `slime_rag/consolidated_view.py` — 스키마 + 세 프롬프트 + `promo_view` 기본값.
- UI(제거됨) — 리뷰 블록에 `**배송·CS**` 줄(빈 섹션은 통째 생략, 패딩 없음).
- `eval/layer2_gold.json` — `value` 엔트리 제거. `eval/test_consolidated_sections.py` 에
  섹션 흐름 + 복제 계약 테스트 2건 추가.
- **DB 무변경** — `value` 전용 컬럼이 애초에 없었다. 기존 행의 `attributes` JSONB 에 남아 있는
  `value` 키는 읽는 코드가 사라져 무해하게 방치된다(백필 불필요).

> **2026-08-06 주석(ADR-0012)**: 이 ADR 이 언급한 Streamlit UI 파일은 삭제됐다.
> 결정 자체는 유효하고 백엔드 쪽 근거(`consolidated_view` · `source_links`)는 그대로 산다 —
> 사라진 건 그 결정을 렌더하던 화면뿐이다.
