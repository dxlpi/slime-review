# eval/ — 오프라인 테스트 & 골드셋

## Purpose (이 모듈이 소유하는 것)
네트워크·유료 API 없이 도는 결정적 오프라인 테스트와 사람 검수 골드셋. 추출·편향·수집
어댑터의 회귀를 막는다. 라이브(유료/App Review) 경로는 게이트 뒤라 여기서 제외.

## Key files
| 파일 | 역할 |
|---|---|
| `test_bias.py` | 편향 태깅 — 게이트 recall/단락/precision 보존/config (18 케이스) |
| `test_apify_source.py` | Apify 어댑터 오프라인 매핑·provenance·중복접힘·회복력 (9 케이스) |
| `layer2_gold.json` | 2층 추출 골드셋(사람 검수, 현재 비교글 1건) |

## Common patterns (workflow)
```bash
source .venv/bin/activate
python -m eval.test_bias          # 모든 bias 오프라인 테스트
python -m eval.test_apify_source  # Apify 어댑터 오프라인 테스트
python -m evals.run               # 추출/개체연결 pass-rate 지표 (→ ../evals/CLAUDE.md)
```
- 새 테스트는 `python -m eval.<name>` 로 돌 수 있게 `__main__` 셀프테스트 블록 포함.

## Non-obvious (주의 / Gotcha)
- **Important:** 반드시 repo 루트에서 `python -m eval.<name>` 로 실행 — 파일 직접 실행은 `slime_rag` import 실패.
- **Note:** 여기 테스트는 전부 오프라인(결정적). 라이브 수집·LLM 호출은 골드셋 밖.
- **Don't:** 유료 Apify/OpenAI 를 테스트에서 실제 호출하지 말 것 — 어댑터 매핑만 검증.

## Cross-module dependencies
- → [`../slime_rag/`](../slime_rag/CLAUDE.md): `bias`, `sources`, `linking`
- 지표 산출 하네스: [`../evals/`](../evals/CLAUDE.md) (pass-rate 계량, Cat G)
