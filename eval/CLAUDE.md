# eval/ — 오프라인 테스트 & 골드셋

## Purpose (이 모듈이 소유하는 것)
네트워크·유료 API 없이 도는 결정적 오프라인 테스트와 사람 검수 골드셋. 추출·편향·수집
어댑터의 회귀를 막는다. 라이브(유료/App Review) 경로는 게이트 뒤라 여기서 제외.

## Key files
| 파일 | 역할 |
|---|---|
| `test_bias.py` | 편향 태깅 — 게이트 recall/단락/precision 보존/config (18 케이스) |
| `test_apify_source.py` | Apify 어댑터 오프라인 매핑·provenance·중복접힘·회복력 (9 케이스) |
| `test_consolidated_sections.py` | 리뷰 요약(향/질감/장단점) — 미언급=빈칸·단일소스=통합None·홍보성 분리·no-LLM 회귀 (5 케이스) |
| `test_relevance_gate.py` | 관련성 게이트 — topic/domain 축 + **M/Q/E 3축**(KAX-AC4~AC10: chrome-strip·평서형 종결어미·전언 분리·편향 보존·순위/예산) |
| `test_extract_hearsay.py` | 전언 하드닝(AC15) — 프롬프트 스냅샷 + `firsthand_evidence` 결정적 게이트 + 실호출 통합 |
| `test_extract_thread.py` | 스레드 배치 추출(AC12/AC13) — 호출 수·조각별 귀속·누락 패딩 + 형제 댓글 문맥 복원. `--batch-size`(반복 가능) + `gold/thread_gold.json` 기반 귀속 채점(`grade_thread_attribution`) |
| `test_index_meta.py` | `index_post` 의 `relevance_meta` JSONB 영속화 — 전달 시 INSERT 반영/미전달 시 NULL (무네트워크·무모델) |
| `gold/thread_gold.json` | 스레드 골드 — 실제 디시 3스레드 51조각, 조각별 `mentioned_product` 라벨(~200자 스니펫 정책) |
| `test_ui_render.py` | UI 헤드리스(AppTest) — 종합뷰 3블록·URL 링크·빈 섹션 생략 + **제품 타이핑 검색**(부분일치/공백무시/초성)·**선택 흐름**(마켓→범위→제품→무매치), 예외 0 |
| `layer2_gold.json` | 2층 추출 골드셋(사람 검수, 현재 비교글 1건) |

## Common patterns (workflow)
```bash
source .venv/bin/activate
python -m eval.test_bias          # 모든 bias 오프라인 테스트
python -m eval.test_apify_source  # Apify 어댑터 오프라인 테스트
python -m eval.test_relevance_gate   # 관련성 3축 게이트 회귀
python -m eval.test_extract_thread   # 스레드 배치(키 없으면 실호출 케이스만 skip)
python -m evals.run               # 추출/개체연결 pass-rate 지표 (→ ../evals/CLAUDE.md)
```
- 새 테스트는 `python -m eval.<name>` 로 돌 수 있게 `__main__` 셀프테스트 블록 포함.

## Non-obvious (주의 / Gotcha)
- **Important:** 반드시 repo 루트에서 `python -m eval.<name>` 로 실행 — 파일 직접 실행은 `slime_rag` import 실패.
- **Note:** 여기 테스트는 전부 오프라인(결정적). 라이브 수집·LLM 호출은 골드셋 밖.
- **Don't:** 유료 Apify/OpenAI 를 테스트에서 실제 호출하지 말 것 — 어댑터 매핑만 검증.
- **예외:** `test_extract_hearsay`·`test_extract_thread` 는 프롬프트 준수를 실호출로만 확인할 수 있는
  부분이 있어 `OPENAI_API_KEY` 가 있을 때만 그 케이스를 돈다(없으면 skip, 나머지는 오프라인).
  전언 차단을 프롬프트에만 맡기면 같은 입력에 4번 다른 답이 나온다는 걸 실측했기 때문에,
  결정적 부분(`firsthand_evidence` 게이트)은 키 없이도 검증된다.

## Cross-module dependencies
- → [`../slime_rag/`](../slime_rag/CLAUDE.md): `bias`, `sources`, `linking`
- 지표 산출 하네스: [`../evals/`](../evals/CLAUDE.md) (pass-rate 계량, Cat G)
