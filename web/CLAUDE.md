# web/ — 프런트엔드 (Vite + React + TS)

## Purpose (이 모듈이 소유하는 것)
화면 둘 — 공개 검색 화면과 로컬 전용 1층 스펙 검수 도구. **표시만** 담당한다 — 평가 기준·링크·로고·발췌 **판단은 전부 백엔드**에 있고
([ADR-0011](../docs/adr/0011-six-criteria-summary-and-search-page.md) · `source_links` · ADR-0013),
여기는 [`../api`](../api/CLAUDE.md) 가 준 걸 그린다. 삭제된 Streamlit UI 의 후임
([ADR-0012](../docs/adr/0012-remove-streamlit-frontend.md)).

## Key files
| 파일 | 역할 |
|---|---|
| `src/App.tsx` | 경로 분기 — `/review` 면 `SpecReview`, 아니면 `SlimeSearch`. **라우터 미설치**(의존성 추가 없이 `location.pathname`) |
| `src/screens/SlimeSearch.tsx` | 공개 화면 전부. 디자인 HTML `Slime Search.dc.html` 의 **축자 이식**(인라인 style 값 무변경) |
| `src/screens/SpecReview.tsx` | 🔒 1층 스펙 검수 도구(`/review`, 로컬 전용). **픽셀 대조 계약 밖** — 아래 참조 |
| `src/data/api.ts` | 백엔드 연결. `mock.ts` 와 **같은 shape** 을 돌려주는 게 존재 이유 |
| `src/data/admin.ts` | 🔒 관리 라우트 연결. 404 는 게이트가 꺼져 있다는 신호로 읽는다(에러 아님) |
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
- **Important:** 🔒 **`SpecReview.tsx` 는 픽셀 대조 계약 밖이다**([ADR-0016](../docs/adr/0016-human-in-the-loop-spec-review.md)).
  아래 항목(0.025% 픽셀 차이)은 `SlimeSearch.tsx` 에만 걸린다 — 검수 화면은 **디자인 원본이
  없는 내부 도구**라 목업과 diff 할 대상이 애초에 없다. 같은 KDS 컴포넌트·토큰은 그대로
  쓰지만(`components/kds/` 편집 금지 규칙은 여기도 적용) 인라인 값은 자유다.
  **Note:** 이 화면은 `ADMIN_ENABLED=1` 로 띄운 로컬 API 에만 붙는다. 꺼져 있으면 `/api/admin/*`
  가 404 이고, 화면은 에러가 아니라 **켜는 방법 안내**로 degrade 한다.
  **Don't:** 여기에 값 추천·자동완성을 넣지 말 것 — 이 도구의 전제가 '**LLM 이 못 채운다**'이고
  (미언급 → null, 1급 규칙), 추천을 넣으면 사람이 확인 버튼만 누르게 된다. 화면 문구
  `게시물에서 확인되는 것만 적어요. 모르면 모름으로 표시해요` 와 [모름으로 표시] 버튼이
  저장 버튼과 **같은 줄·같은 크기**인 건 그 이유다.
- **Important:** `SlimeSearch.tsx` 의 인라인 style 값은 디자인 원본과 **한 글자도 다르지 않다**.
  측정치가 근거다 — 목업 대비 **0.025% 픽셀 차이**(8px 오프셋은 목업이 `body` 기본 margin 을
  리셋하지 않아서고, 우리는 리셋한다). 값을 '정리'하면 이 대조가 무너진다.
  **의도적 예외 1건**: 제품 정보 카드의 **'풀 조합' 줄은 칩이 아니라 평문**이다(사용자 결정
  2026-08-11). 디자인 원본은 `Chip` 여러 개였는데 재료가 6~8개인 조합이 흔해 칩이 줄바꿈하며
  카드 높이를 밀었다. 아래 향·종류·질감 세 줄과 같은 `specValue` 를 쓴다 — 이 한 줄만 목업과
  다르고, 나머지는 그대로다.
- **Don't:** `spec.glue` 를 화면에서 다시 쪼개지 말 것. 구분자 통일은 `api.ts` 의 `GLUE_SEP`
  한 곳이 한다 — 판매자 표기가 마켓마다 달라서(실측 1,803행: 공백 1,395 · `+` 347 · `,` 123)
  칩 시절 정규식이 `+` 를 안 나눴고, 그래서 43행은 화면에 **`+` 만 든 조각**을, 290행은
  `엘믹+아마존+플레인+…` **한 덩어리**를 그렸다. 규칙이 두 벌이 되면 같은 실수가 되돌아온다.
  ⚠️ **`>` 는 구분자가 아니다** — `뉴클>>>>아모스`·`컬글>아올+스쿨` 은 배합 **비율/순서**
  표기라(31행) 나누면 두 재료가 동등해 보이고 비율이 사라진다. 조각 안에 그대로 둔다.
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
