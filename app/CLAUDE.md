# app/ — Streamlit UI

## Purpose (이 모듈이 소유하는 것)
사용자 대면 화면만. 상단 선택(마켓 드롭다운 → **범위 토글(마켓 전체/특정 제품)** → 제품
타이핑 검색), 리뷰 요약 탭(소스별 정서·갭·향불일치), 챗 탭(질문→근거답변), 사이드바
소스 필터/1층 스펙 패널. **표시 전용** — 모든 백엔드 로직은 `slime_rag` 에 있다.

## Key files
| 파일 | 역할 |
|---|---|
| `ui.py` | 전체 Streamlit 앱. `slime_rag.pipeline` / `slime_rag.search` 만 호출 |

주요 함수: `render_selection`(선택 UI — 제품 조회 콜러블 주입 → DB 없이 테스트 가능) ·
`filter_products`(제품 타이핑 검색: 부분일치/공백무시/초성, 순수 함수) ·
`_render_consolidated`/`_render_spec`/`_render_review_block`(표시) · `_render_summary_tab`(요약 버튼).

## Common patterns (workflow)
```bash
source .venv/bin/activate
streamlit run app/ui.py          # docker pgvector(포트 55432) + .env OPENAI_API_KEY 필요
python -m pytest --version 2>/dev/null || true   # 헤드리스 검증은 streamlit.testing.AppTest 사용
```
- 화면 요소 추가 시: 데이터 접근을 UI 에 새로 쓰지 말고 `pipeline` 에 함수를 만들어 호출.

## Non-obvious (주의 / Gotcha)
- **Note:** `ui.py` 는 `streamlit run` 이 repo 루트를 `sys.path` 에 넣지 않으므로 상단에서 직접 추가한다.
- **Don't:** UI 에서 DB/LLM 을 직접 만지지 말 것 — 데이터접근은 `pipeline` 이 캡슐화(테스트·재사용).
- **Important:** 소스 평균 금지 — 종합뷰는 소스별 + 갭으로 투명하게 표시.
- **Important:** 1층 스펙은 결정적 카드(`_render_spec`, specs 행 그대로)로만 표시 — 챗 답변/요약은
  2층(후기)만 근거이며 스펙을 프롬프트에 넣지 말 것(스펙↔후기 완전 분리, 사용자 결정 2026-08-04).
- **Note:** 마켓 모드 종합뷰는 '추적 중인 제품들의 후기 집계'다(수집이 제품 앵커, ADR-0007) —
  범위 캡션을 지우지 말 것.
- **Don't:** 위젯 값을 `st.session_state[라벨]` 로 되읽지 말 것 — 라벨은 키가 아니다.
  `render_selection` 은 선택값을 dict 로 **반환**한다(scope 포함).
- **Note:** 요약 결과는 `st.session_state["summaries"]` 에 (마켓, 제품) 키로 캐시한다 —
  위젯 하나 건드릴 때마다 리런되는 Streamlit 에서 유료 요약을 다시 돌리지 않기 위함.

## Cross-module dependencies
- → [`../slime_rag/`](../slime_rag/CLAUDE.md): `pipeline`(글루), `search`(근거답변)
- 검증: 헤드리스 `AppTest` 로 클릭 경로까지 예외 0 확인
