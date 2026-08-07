# web/ — 프런트엔드 (Vite + React + TS)

## Purpose (이 모듈이 소유하는 것)
검색 화면 하나. **표시만** 담당한다 — 평가 기준·링크·로고·발췌 **판단은 전부 백엔드**에 있고
([ADR-0011](../docs/adr/0011-six-criteria-summary-and-search-page.md) · `source_links` · ADR-0013),
여기는 [`../api`](../api/CLAUDE.md) 가 준 걸 그린다. 삭제된 Streamlit UI 의 후임
([ADR-0012](../docs/adr/0012-remove-streamlit-frontend.md)).

## Key files
| 파일 | 역할 |
|---|---|
| `src/screens/SlimeSearch.tsx` | 화면 전부. 디자인 HTML `Slime Search.dc.html` 의 **축자 이식**(인라인 style 값 무변경) |
| `src/data/api.ts` | 백엔드 연결. `mock.ts` 와 **같은 shape** 을 돌려주는 게 존재 이유 |
| `src/data/mock.ts` | 자리표시자 + 타입 원본(`Cell`·`SummaryRow`·`CRITERIA`) |
| `src/components/kds/` | 디자인 번들에서 잘라낸 KDS 6개 — **편집 금지**, [README](src/components/kds/README.md) 참조 |
| `src/styles/kds/` | KDS 토큰 **byte-for-byte 사본** — 여기를 고쳐서 색을 바꾸지 말 것 |
| `src/styles/slime-accent.css` | 민트 액센트 격리 지점(KDS 기본은 파랑) |

## Common patterns (workflow)
```bash
npm install && npm run dev        # http://127.0.0.1:5173 (API 없으면 목 데이터로 폴백)
npm run build && npm run lint     # 타입체크·번들 · oxlint
# 실데이터를 보려면 repo 루트에서: uvicorn api.main:app --reload --port 8000
```
- 백엔드 오리진은 `VITE_API_BASE`(기본 `http://127.0.0.1:8000`).
- 새 필드를 그리려면 순서가 **`pipeline` → `api/main.py` → `src/data/api.ts` → 화면**이다.
  화면에서 계산하기 시작하면 표시 규칙이 백엔드와 갈라진다.

## Non-obvious (주의 / Gotcha)
- **Important:** `SlimeSearch.tsx` 의 인라인 style 값은 디자인 원본과 **한 글자도 다르지 않다**.
  측정치가 근거다 — 목업 대비 **0.025% 픽셀 차이**(8px 오프셋은 목업이 `body` 기본 margin 을
  리셋하지 않아서고, 우리는 리셋한다). 값을 '정리'하면 이 대조가 무너진다.
- **Don't:** `components/kds/` 와 `styles/kds/` 안을 고치지 말 것. 슬라임 커스텀은
  `slime-accent.css` 나 사용처의 `style` prop 으로.
- **Important:** 리뷰 본문은 **서버가 자른 발췌**다(ADR-0013 §3). CSS `line-clamp` 로 접는 건
  발췌가 아니다 — 전문이 이미 브라우저에 도달한 것이다. 전문으로 가는 길은 '원문 보기' 링크 하나뿐.
- **Don't:** 링크가 없을 때 URL 을 조립하지 말 것. 틀린 링크는 링크 없음보다 나쁘다
  ([ADR-0009](../docs/adr/0009-source-links-and-owner-media.md)) — 식별자가 없으면 텍스트로 그린다.
- **Note:** 목록은 '더보기' 누적이 아니라 **쪽 번호**다(사용자 결정 2026-08-06). 누적식은 정렬을
  바꿨을 때 이미 펼친 만큼의 처리가 애매하고 되돌아갈 방법이 없다.
- **Note:** 값이 없는 자리는 `—`. 인스타에는 조회/추천이, 디시 댓글에는 글단위 지표가 없다 —
  0 으로 채우면 없는 걸 0 이라고 말하는 게 된다.
- **Don't:** 출처별 수치를 합치거나 평균내지 말 것. 소스 편향(인스타 긍정 / 디시 부정)은 보정
  대상이 아니라 이 프로젝트의 존재 이유다 — 출처별로, 갭과 함께 그린다.
- **Important:** 기준 줄의 `scope` 는 백엔드가 준다([ADR-0015](../docs/adr/0015-market-scope-order-criteria.md)).
  고객 응대·배송 두 줄은 제품이 아니라 **마켓** 평가라 `해당 마켓 전체 기준`을 붙인다.
  **Don't:** 그 라벨에 건수를 되붙이지 말 것(사용자 결정 2026-08-07) — 화면에서 숫자를 걷어낸
  ADR-0014 개정과 같은 이유다. 건수는 `marketSummaryMeta` 에 provenance 로만 남는다.
  **Don't:** 배지 철회(ADR-0014)를 이유로 이 라벨을 걷어내지 말 것 — 걷어낸 건 건수·정서 분포처럼
  `verdict`/`minority` 가 이미 문장으로 말하던 것이고, 이건 **문장 어디에도 없는 사실**이다.
  없으면 읽는 사람이 배송 평가를 이 제품 것으로 오해한다.
- **Warning:** 요약 카드의 **점수는 전부 가짜다** — 링의 `7.8` 도, 축별 `8.4` 도
  `mock.PLACEHOLDER_SCORE` 의 상수이고 실데이터와 무관하다. 백엔드에 점수 산출이 아직 없다
  (사용자 결정 2026-08-07: 곧 도입). 카드에서 **문장(`verdict`/`minority`)만 실데이터**다.
  산출이 들어오면 지울 곳은 두 군데뿐이다 — `mock.PLACEHOLDER_SCORE` 정의와 `api.ts`
  `toPageData` 의 `score:` 줄. **Don't:** 그때 `d.score ?? PLACEHOLDER_SCORE` 로 남기지 말 것.
  폴백이 남으면 요약 생성이 안 된 페이지가 조용히 가짜 숫자를 띄운다.
  **Don't:** 점수를 화면에서 계산하지 말 것 — 정서 집계는 `consolidated_view.criterion_stats`
  가 이미 갖고 있고, 두 벌이 되면 카드와 기준표가 다른 말을 한다. 그리고 두 출처를 하나로
  평균낸 숫자만 내보내지 말 것(1급 규칙) — 통합 점수를 쓰더라도 출처별 값과 갭이 같이 보여야 한다.
- **Note:** 화면 문구는 전부 `~해요`체이고, 단어는 **출처**다(`소스` 아님).

## Cross-module dependencies
- [`../api`](../api/CLAUDE.md) ← HTTP 로만. DB·`slime_rag` 를 직접 알지 않는다.
- 응답 shape 변경은 `api/main.py` 와 `src/data/api.ts` 를 **동시에** 건드린다(타입 수동 동기화).
- `consolidated_view.CRITERIA` 가 바뀌면 평가 기준 표의 행이 바뀐다([ADR-0011](../docs/adr/0011-six-criteria-summary-and-search-page.md))
