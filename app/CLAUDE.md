# app/ — Streamlit UI

## Purpose (이 모듈이 소유하는 것)
사용자 대면 화면만. 챗(질문→근거답변), 종합뷰(소스별 정서·갭·향불일치), 사이드바
메타필터/1층 스펙 패널. **표시 전용** — 모든 백엔드 로직은 `slime_rag` 에 있다.

## Key files
| 파일 | 역할 |
|---|---|
| `ui.py` | 전체 Streamlit 앱. `slime_rag.pipeline` / `slime_rag.search` 만 호출 |

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

## Cross-module dependencies
- → [`../slime_rag/`](../slime_rag/CLAUDE.md): `pipeline`(글루), `search`(근거답변)
- 검증: 헤드리스 `AppTest` 로 클릭 경로까지 예외 0 확인
