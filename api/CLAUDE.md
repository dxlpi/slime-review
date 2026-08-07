# api/ — HTTP 층

## Purpose (이 모듈이 소유하는 것)
`web/` 이 백엔드를 보는 **유일한 창구**. 소유하는 것은 라우팅·직렬화·화면용 형태 맞추기뿐이고,
SQL·표시 정책·집계는 전부 [`../slime_rag`](../slime_rag/CLAUDE.md) 안에 있다.
전체 흐름은 [ARCHITECTURE.md](../ARCHITECTURE.md).

## Key files
| 파일 | 역할 |
|---|---|
| `main.py` | 전부. FastAPI 앱 + 5개 엔드포인트 + 화면 형태 어댑터(`_paragraph` · `_by_criterion` · `_logo`) |

| 엔드포인트 | 반환 |
|---|---|
| `GET /api/page?market=&product=` | 제품 페이지 1벌 — 히어로 · 1층 스펙 · 요약 · 커뮤니티 리뷰 |
| `GET /api/markets` · `/api/markets/{market}/products` | 검색 드롭다운 재료 |
| `GET /api/logo/{handle}` | 마켓 로고 파일(ADR-0010) |
| `GET /api/health` | 배포 헬스체크 |

## Common patterns (workflow)
```bash
source .venv/bin/activate                          # repo 루트에서 (DB 포트 55432)
uvicorn api.main:app --reload --port 8000
curl -s 'http://127.0.0.1:8000/api/page?product=빠코볼' | python -m json.tool | head
```
- **엔드포인트 추가 = `pipeline` 에 함수 추가 후 여기서 부르기.** 여기에 SQL 을 쓰기 시작하면
  백엔드가 두 벌이 되고, 표시 규칙이 화면마다 갈라진다.
- 응답 shape 을 바꾸면 [`../web/src/data/api.ts`](../web/src/data/api.ts) 의 타입도 같이 바꾼다.

## Non-obvious (주의 / Gotcha)
- **Important:** `/api/page` 는 **저장된 요약만** 읽는다(`with_summary=False`). 페이지 로드마다
  요약을 생성하면 열 때마다 과금된다 — 생성은 `pipeline.generate_summaries` 로 따로 돈다.
- **Important:** 응답 shape 은 [`../web/src/data/mock.ts`](../web/src/data/mock.ts) 와 **같은 모양**을
  유지한다. 그래야 목 데이터와 실데이터를 마크업 수정 없이 갈아끼울 수 있고, 디자인 원본과의
  픽셀 대조가 계속 성립한다.
- **Important:** 본문(`body`)은 `pipeline.list_reviews` 가 **서버에서 이미 자른 발췌**다
  (ADR-0013 §3). 여기서 전문을 다시 꺼내지 말 것 — 자르는 자리는 한 곳이어야 공개 전환 때
  빠뜨리지 않는다.
- **Warning:** `market` 은 **선택**이다. 디시 이용자는 마켓을 언급하지 않는 경우가 많아
  개체연결이 보류하는데, 마켓 필수로 조회하면 후기가 있어도 0건이 된다(2026-08-06 실측).
- **Warning:** `_resolve_market` 은 후보가 2개 이상이면 **입력을 그대로 되돌린다**(결과 0건).
  임의로 하나를 고르면 엉뚱한 마켓의 후기를 그 마켓 것처럼 보여준다 — 틀린 귀속 < 결과 없음.
- **Don't:** 탭 라벨('아모스갤')을 `platform` 으로 넘기지 말 것. `PLATFORM_BY_TAB` 이 여기 한 곳에서
  변환한다. `list_reviews` 는 모르는 값에 예외를 던지는데, 그건 소스 미평균(1급 규칙)이 조용히
  깨지는 걸 막는 가드다.
- **Note:** 로고는 파일 경로를 그대로 주지 않는다(서버 로컬 경로) — `/api/logo/{handle}` 로 서빙하고
  파일이 없으면 모노그램으로 degrade 한다(ADR-0010 의 '삭제=철회' 성질).
- **Warning:** CORS 는 지금 Vite 개발서버(`5173`)만 허용한다. 배포 시 실제 오리진으로 교체.

## Cross-module dependencies
- `../slime_rag` → `pipeline`(데이터 접근 전부) · `source_links`(링크·임베드·로고) · `linking`(마켓 해석)
- `../web` ← HTTP 로만. 이쪽이 DB 를 아는 유일한 층이고 화면은 모른다.
- `consolidated_view.CRITERIA` 가 바뀌면 `_by_criterion` 의 키가 따라 바뀐다([ADR-0011](../docs/adr/0011-six-criteria-summary-and-search-page.md))
