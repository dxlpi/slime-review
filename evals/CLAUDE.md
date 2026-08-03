# evals/ — pass-rate 평가 하네스 (Cat G)

## Purpose (이 모듈이 소유하는 것)
"개선했다"를 감이 아니라 **숫자(pass-rate)** 로 말한다. 파이프라인 핵심 단계의 정답셋을
회귀 없이 유지하는지 매 실행마다 계량하고, JSON 지표로 남겨 CI 게이트로 쓴다.
`eval/`(단위 오프라인 테스트)와 구분 — 여기는 **정답셋 대비 품질 지표** 산출.

## Key files
| 파일 | 역할 |
|---|---|
| `run.py` | 러너 — 스위트 실행 → 종합 pass-rate → `results/latest.json` + 요약 |
| `gold/linking_gold.json` | 개체연결 정답셋(demo KB 기준, 결정적·무비용) |
| `gold/relevance_gold.json` | 관련성 정답셋 — 절대축 `label.M/Q/E`(이진 3축) + 쿼리조건부 `label.keep` |
| `gold/boundary_rulings.json` | 경계 15건 판정 원장(§7 블로커 #1). `provisional:true` = 사용자 확정 대기 |
| `check_gold_integrity.py` | AC1/AC2 CI 게이트 — 동일 텍스트의 절대축 일치 + 3축 스키마 |
| `migrate_gold_mqe.py` | `kind` 4분류 → M/Q/E 마이그레이션(멱등). 판정이 바뀌면 다시 돌린다 |
| `train_e_probe.py` | E 프로브 오프라인 학습 → `gold/e_probe.npz` (numpy only, 런타임 학습 없음) |
| `calibrate_relevance.py` | τ_topic 보정 + M/Q/E 축 리포트(AC8 편향 보존·AC9 거짓DROP·버킷 순도) |
| `cost_profile.py` | 추출 호출 비용 구조 실측(AC11) + 프롬프트 캐싱 확인(AC14) |
| `results/latest.json` | 최근 실행 지표(자동 생성) |

## 스위트
- **linking** — `slime_rag.linking.link` vs 정답셋. 마켓 정규화 + 보류(abstain) 동시 채점. LLM 불필요.
- **extract** — 2층 추출(LLM). `OPENAI_API_KEY` 없으면 skip(집계 제외).

## Common patterns (workflow)
```bash
source .venv/bin/activate
python -m evals.run                 # 전체 스위트, 요약 + JSON 저장
python -m evals.run --min 1.0       # pass-rate 하한(미달 시 exit 1 → CI 게이트)
python -m evals.run --json out.json # 지표 저장 경로 지정
python evals/check_gold_integrity.py       # 골드 무결성(CI 게이트, 위반 시 exit 1)
python evals/calibrate_relevance.py --report  # τ + 3축 리포트(편향 보존 위반 시 exit 1)
python evals/train_e_probe.py              # E 프로브 5-fold CV 리포트(--write 로 가중치 커밋)
python evals/cost_profile.py               # 비용 구조 실측(키 필요, 없으면 skip)
```
- 정답셋 확장 = `gold/*.json` 에 케이스 추가만. 새 스위트는 `run.py` 의 `SUITES` 에 함수 추가.

## Non-obvious (주의 / Gotcha)
- **Important:** 골드 라벨은 **현재 정답 동작**을 인코딩 — 의도적 동작 변경 시 골드도 같이 갱신.
- **Note:** demo KB 엔 초성 충돌이 없다(충돌 abstain 은 전체 118 KB·`linking.py` 셀프테스트가 담당).
- **Don't:** 키 없는 CI 에서 extract 를 fail 로 세지 말 것 — skip 은 집계에서 빠진다.
- **Important:** `label.keep` 은 **쿼리 조건부**(타깃이 바뀌면 정답이 바뀐다), `label.M/Q/E` 는
  **텍스트의 절대 속성**이다. 같은 텍스트인데 keep 이 다른 6건은 버그가 아니라 AC3 하드 네거티브
  **자산**이다 — 지우면 `check_gold_integrity.py` 가 실패한다.
- **Note:** 커밋된 `e_probe.npz` 는 골드 전체로 학습됐다. `calibrate_relevance` 의 버킷 순도는
  in-sample 이라 낙관적 — 정직한 수치는 `train_e_probe.py` 의 5-fold CV 값이다.

## Cross-module dependencies
- → [`../slime_rag/`](../slime_rag/CLAUDE.md): `linking`(+키 있으면 `extract`/`llm_ops`)
- 단위 테스트: [`../eval/`](../eval/CLAUDE.md) · CI 연결: [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml)
