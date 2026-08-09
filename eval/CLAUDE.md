# eval/ — 오프라인 테스트 & 골드셋

## Purpose (이 모듈이 소유하는 것)
네트워크·유료 API 없이 도는 결정적 오프라인 테스트와 사람 검수 골드셋. 추출·편향·수집
어댑터의 회귀를 막는다. 라이브(유료/App Review) 경로는 게이트 뒤라 여기서 제외.

## Key files
| 파일 | 역할 |
|---|---|
| `test_bias.py` | 편향 태깅 — 게이트 recall/단락/precision 보존/config (18 케이스) |
| `test_apify_source.py` | Apify 어댑터 오프라인 매핑·provenance·중복접힘·회복력 + **`fetch_profiles`**(로고 URL — HD 우선·폴백·결손 드롭, 게시물/타계정 아바타 미유출) + **URL 소스**(앵커 오염 금지) + **피드 전량 소스**(실 액터 payload 32건 매핑 · `review_class` 미부착 · 소유자 폴백 · 핸들당 호출 1회 · profile-scraper 와 매퍼 한 벌) + 큐레이션 태그가 **KB 마켓 전부**를 덮는지(개수 상수 대신 커버리지) (24 케이스) |
| `test_rawstore.py` | 원문 스냅샷 저장소 — 봉투 왕복(원문 무가공) · **append-only**(덮어쓰기 없음) · **최신 캡처 우선**(같은 초 충돌 정렬 회귀) · 런 합집합 · 0건 런 기록 · **워터마크 키별 격리** · 핸들 구분자 보존 · 경로이탈 차단 · 깨진 파일 비치명 · manifest 집계 (12 케이스) |
| `test_product_registry.py` | 제품 후보 유도 — 마켓명/광역어 제외 · **고빈도 개인태그 분리(삭제 아님)** · 소표본 단정 금지 · 게시물 단위 계수 · 캡션 본문 미유출(ADR-0013) · **LLM 0회** · `load_product_registry` 마켓태그 배제 (10 케이스) |
| `test_consolidated_sections.py` | 리뷰 요약(**6기준 × `{verdict, minority}`** + 장단점) — 미언급=빈칸·단일소스=통합None·홍보성 분리·no-LLM 회귀 + 배송·CS 섹션(주문단위 필드의 행 복제 계약) + 스키마 계약(소리·지속력 재료 유입 / `cs`·`shipping` 분기 / `promo_view` 키) + **ADR-0014 계약**(`criterion_stats` 판정 · 갭 미유입·카운트 출처 격리 · 부재/메타 스크럽 · **과잉 차단 회귀** · 1회 재시도 · 여섯 프롬프트 규칙 도달) + **ADR-0015 계약**(축 분리 — 제품 축에 주문 기준 부재 · 팬아웃 `_fold_orders` 접기 + **과소 집계 회귀** · 축 간 프롬프트 누출 · 축별 스키마) (24 케이스) |
| `test_relevance_gate.py` | 관련성 게이트 — topic/domain 축 + **M/Q/E 3축**(KAX-AC4~AC10: chrome-strip·평서형 종결어미·전언 분리·편향 보존·순위/예산) |
| `test_extract_hearsay.py` | 전언 하드닝(AC15) — 프롬프트 스냅샷 + `firsthand_evidence` 결정적 게이트 + 실호출 통합 |
| `test_extract_thread.py` | 스레드 배치 추출(AC12/AC13) — 호출 수·조각별 귀속·누락 패딩 + 형제 댓글 문맥 복원. `--batch-size`(반복 가능) + `gold/thread_gold.json` 기반 귀속 채점(`grade_thread_attribution`) |
| `test_index_meta.py` | `index_post` 의 `relevance_meta`·`source_ref` JSONB 영속화 — 전달 시 INSERT 반영/미전달 시 NULL/팬아웃 복제 (무네트워크·무모델). 파라미터는 **컬럼 이름**으로 찾는다 + **멱등성 계약**(`ON CONFLICT DO NOTHING` 절 부착 · 스킵 시 실제 적재 수 반환) |
| `test_product_repair.py` | 제품명 귀속 복구 — 유령 제거(풀조합·향료·광역태그)와 **진짜 제품 보존**(해시태그면 1층 부재와 무관 유지 · 같은 글의 별개 제품은 개명도 삭제도 안 함)의 균형 + 접기 계약(이중 계상 방지·생존자 선택·보류 미접기). 개발 중 **양방향으로 한 번씩 깨진** 자리라 회귀 게이트로 둔다 (10 케이스) |
| `test_layer1_collection.py` | 1층 수집 **누적성** 계약 — `_upsert_spec` 전 값 칸 COALESCE 보호(+`beads` 는 cardinality) · `ingest_seller_profiles` 기본 대상이 KB 전체(스펙 보유 마켓 포함) · `only_missing` 은 명시 옵션 · `dry_run` 무접촉. 액터가 최신 ~12글만 주므로 **수집은 누적이지 교체가 아니다** (5 케이스) |
| `test_incremental_collection.py` | 증분 수집 계약 — **Phase 0** 디시 댓글 `post_id` 가 `comment_no` 기반(런 무관·`ordinal` 폴백 금지·결손은 카운트 노출)·재실행 `indexed_rows==0` / **Phase 1** 기보유 컷이 **첫 유료 단계 앞**(해시태그는 `bias.partition` 앞·디시는 `extract_collected` 앞)·절감은 조각이 아니라 **배치 수**·판매자 컷은 `extract_spec` **앞**이고 `skip_seen=False` 강제 재추출이 산다 / **Phase 2** 워터마크가 **상세 HTTP 앞**에서 컷·미지정 시 하위호환·워터마크는 **앵커 스코프**(이력 없으면 전량 수집)·`revisit_threads` 는 검색 목록 밖 글도 **직접 조회**·기보유 글은 배치 문맥으로만 남고 색인 제외(**무관한 새 글이 죽은 스레드를 끌어오지 않음** 회귀 포함) · 스레드 키는 `extract.thread_key` 한 벌(글=URL·댓글=`parent_no`) — **픽스처가 수집기 meta 를 그대로 흉내낸다**(글엔 `thread_no` 없음) / **Phase 3** `_is_stale` 4조건·naive/결손 타임스탬프 안전·개수는 뷰 계산(축별 정의 보존)·`dry_run` 기본 무생성·생성 후 멱등 (29 케이스) |
| `test_source_links.py` | 원문 링크 **정책**(순수) — `permalink` degrade·`#cmt` 제거 · 식별자 조립 · 한 스레드 댓글 distinct · `embed_url` 게이트 · 근거 목록 그룹핑 · 댓글 id 보존 · 캡션 계약 + **로고 게이트**(`logo_asset` 3중 fail-closed·경로이탈 차단·링크백 보존·모노그램 결정성) |
| `gold/thread_gold.json` | 스레드 골드 — 실제 디시 3스레드 51조각, 조각별 `mentioned_product` 라벨(~200자 스니펫 정책) |
| `test_post_columns.py` | 원문·작성 메타 매핑(ADR-0013) — 라벨 붙은 카운트('조회 428') 파싱 · 연도 없는 날짜 폐기 · 디시 글/댓글 작성자 양경로 · 인스타 owner_username · 글단위 지표는 댓글에서 None. **실수집에서 두 번 조용히 빈 자리**라 회귀 게이트로 둔다 |
| `layer2_gold.json` | 2층 추출 골드셋(사람 검수, 현재 비교글 1건) |

## Common patterns (workflow)
```bash
source .venv/bin/activate
python -m eval.test_bias          # 모든 bias 오프라인 테스트
python -m eval.test_apify_source  # Apify 어댑터 오프라인 테스트
python -m eval.test_relevance_gate   # 관련성 3축 게이트 회귀
python -m eval.test_source_links     # 원문 링크·임베드 정책(CI 게이트)
python -m eval.test_layer1_collection  # 1층 수집 누적성(upsert COALESCE · 대상 선정)
python -m eval.test_incremental_collection  # 증분 수집(안정 키 · 추출 전 컷 · 워터마크 · 변경분 요약)
python -m eval.test_product_repair     # 제품명 귀속 복구(유령 제거 vs 진짜 제품 보존)
python -m eval.test_rawstore           # 원문 저장소(append-only · 최신캡처 우선 · 워터마크)
python -m eval.test_product_registry   # 제품 후보 유도(빈도 분리 · 무과금 · 캡션 미유출)
python -m eval.test_post_columns     # 원문·작성 메타 매핑(CI 게이트)
python -m eval.test_extract_thread   # 스레드 배치(키 없으면 실호출 케이스만 skip)
python -m evals.run               # 추출/개체연결 pass-rate 지표 (→ ../evals/CLAUDE.md)
```
- 새 테스트는 `python -m eval.<name>` 로 돌 수 있게 `__main__` 셀프테스트 블록 포함.

## Non-obvious (주의 / Gotcha)
- **Important:** 반드시 repo 루트에서 `python -m eval.<name>` 로 실행 — 파일 직접 실행은 `slime_rag` import 실패.
- **Note:** 여기 테스트는 전부 오프라인(결정적). 라이브 수집·LLM 호출은 골드셋 밖.
- **Don't:** 유료 Apify/OpenAI 를 테스트에서 실제 호출하지 말 것 — 어댑터 매핑만 검증.
- **Important:** **gitignore 된 파일에 의존하는 테스트는 로컬에서만 통과한다.** `test_apify_source`
  의 피드 실 payload 스냅샷(`apify_posts_backfill_raw.json`)이 `apify_posts_*_raw.json` 무시
  규칙에 걸려 클론엔 없고, CI 가 `FileNotFoundError` 로 죽었다(2026-08-09). 이제 없으면 그 두
  케이스만 **눈에 보이게 스킵**한다.
  **Don't:** 통과시키려고 샘플을 커밋하지 말 것 — 캡션 본문 32건이 들어 있다.
  **Don't:** 이 주의사항을 쓰면서 그 파일 경로를 `디렉터리/파일.확장자` 꼴로 적지 말 것 —
  경로 검사기가 참조로 읽어 **문서가 같은 실패를 낸다**(실제로 그렇게 한 번 더 깨졌다).
  파일명만 적으면 정규식(`디렉터리/` 접두 필요)에 안 걸린다.
  **로컬 전량 통과는 CI 통과의 근거가 아니다** — 확인은 추적 파일만 뽑은 트리에서
  `.github/workflows/ci.yml` 의 스텝을 그대로 돌린다(`git ls-files` → tar → 그 트리에서 실행).
  ⚠️ 그 재현은 **편집을 다 끝낸 뒤** 돌려야 한다. 검사 후에 문서를 한 줄 더 고치고 커밋해서
  세 번째 적색을 냈다.
- **예외:** `test_extract_hearsay`·`test_extract_thread` 는 프롬프트 준수를 실호출로만 확인할 수 있는
  부분이 있어 `OPENAI_API_KEY` 가 있을 때만 그 케이스를 돈다(없으면 skip, 나머지는 오프라인).
  전언 차단을 프롬프트에만 맡기면 같은 입력에 4번 다른 답이 나온다는 걸 실측했기 때문에,
  결정적 부분(`firsthand_evidence` 게이트)은 키 없이도 검증된다.

## Cross-module dependencies
- → [`../slime_rag/`](../slime_rag/CLAUDE.md): `bias`, `sources`, `linking`
- 지표 산출 하네스: [`../evals/`](../evals/CLAUDE.md) (pass-rate 계량, Cat G)
