# slime_rag/ — 코어 RAG 패키지

## Purpose (이 모듈이 소유하는 것)
슬라임 리뷰 RAG의 엔진. 소스 수집 → 관련성 → 추출(1·2층) → 개체연결 → 색인 →
하이브리드 검색 → 근거 답변 → 종합뷰·편향집계까지 전 파이프라인. UI(`../app`)와
DB 스키마(`../sql`)는 이 패키지를 소비만 한다. 전체 흐름은 [ARCHITECTURE.md](../ARCHITECTURE.md) 참조.

## Key files
| 파일 | 역할 |
|---|---|
| `sources.py` (패키지) | 수집 레이어 — `Source` 인터페이스 + DCInside/Instagram/Apify 구현체 + `collect_all`. 큰 파일이라 `sources/` 패키지로 분할됨 → [sources/CLAUDE.md](sources/CLAUDE.md) |
| `relevance.py` | 관련성 필터 — topic(코사인)·domain(centroid)·**M/Q/E 3축**. 합집합 후보 + E 신뢰도 순위 + 예산 |
| `relevance_rules.py` | M/Q/E 표면 규칙 캐스케이드 — 한국어 증거성 표지 기반. 어휘는 `LEXICON` 상수 데이터 |
| `extract.py` | 추출 러너 — 2층 후기(`LAYER2_SCHEMA`) + 1층 판매자 스펙(`extract_spec`) |
| `linking.py` | 개체연결 — KB 표면형/초성 역인덱스, 충돌 시 abstain |
| `bias.py` | 편향 태깅(IG) — 홍보성 게이트→LLM 캐스케이드, 판매자 라우팅 `partition` |
| `layer1.py` | 1층 fixture 로더 + `seed_kb_products` + `iter_specs` |
| `index.py` / `search.py` | BGE-M3 임베딩 적재 / 하이브리드(dense+BM25 RRF)+메타필터+근거답변 |
| `source_links.py` | 원문 링크 정책(순수) — `permalink`/`embed_url`/`evidence_group_key`/`build_source_ref`/`group_evidence_sources` + `logo_asset`(마켓 로고 표시 게이트). DB·네트워크·streamlit 무의존 |
| `logos.py` | 마켓 IG 프로필 아바타 **1회성 수동 수집** CLI(ADR-0010) — 파이프라인 미배선, 자동갱신 없음 |
| `consolidated_view.py` | 소스별 정서·갭·향불일치 + 인스타/디시/통합 **리뷰 요약**(향/질감/배송·CS/장단점 섹션, 미언급=빈칸; 홍보성 분리). 마켓 모드(product=None) 지원 — 재료에 제품 라벨. **요약 프롬프트에 1층 스펙 미유입**(스펙↔후기 분리) |
| `db.py` | pgvector(Postgres) 연결 한 곳 |
| `llm_ops.py` | **모든 LLM 호출 단일 통로** — 로깅·토큰·비용(LEDGER)·재시도·structured outputs |
| `config.py` | `.env` 단일 출처(`Settings` 데이터클래스) |
| `pipeline.py` | end-to-end 오케스트레이터 + `ingest_hashtag`(인스타)·`ingest_dcinside`(디시 배치) + UI 데이터접근 캡슐화(`consolidated_for` 제품 / `consolidated_for_market` 마켓 단위) |

## Common patterns (workflow)
```bash
# 모든 명령은 repo 루트에서, .venv 활성화 후
source .venv/bin/activate
python -m slime_rag.linking      # 셀프테스트 예 (대부분 모듈에 __main__ 셀프테스트 존재)
python -m slime_rag.pipeline     # end-to-end 글루 (pgvector + .env 필요, 포트 55432)
```
- **LLM 추가/교체는 `llm_ops.py` 한 곳만** 수정 — 벤더는 이 파일에만 의존(Anthropic→OpenAI 전환이 파이프라인 무변경으로 끝난 게 증거).
- **새 소스 추가 = `Source` 구현체 추가** → `sources/` 에 파일 하나. 파이프라인 무변경.
- 결정성은 structured outputs(`response_format` json_schema, strict)로 확보, 파싱 실패 1회 재시도.

## Non-obvious (주의 / Gotcha)
- **Important:** 미언급은 `null`, 지어내기 금지. 필드별 근거 스니펫(15자 내외)으로 인용·저작권 회피.
- **주의:** 향 불일치·소스 갭은 LLM 이 아니라 **조인·집계 단계**(`consolidated_view.py`)에서 계산.
- **Warning:** GPT-5 계열은 추론 모델이라 `temperature` 미전송(무시/제한됨).
- **Important:** 관련성 3축에서 **DROP 사유는 M 하나뿐**이다. E 음성은 드롭이 아니라 순위 꼬리이고,
  예산 초과분은 `unprocessed`(미처리)로 따로 센다 — 침묵 절단 금지.
  **Q 는 순위에 관여하지 않는다** — 정렬 키는 `(e_bucket, bias_hold, topic_score)` 뿐이다.
  질문글이 뒤로 밀리는 건 Q 때문이 아니라 그런 글이 대개 E=0 이기 때문이다.
  부정 감성은 E=0 이어도 `bias_hold` 로 후보에 남는다(D6 편향 보존 하드게이트).
- **Don't:** 전언 차단을 프롬프트에만 맡기지 말 것. 같은 입력 4회 호출에 4번 다른 답이 나왔다
  → `extract.drop_hearsay_reviews` 가 `firsthand_evidence` 없는 항목을 코드로 버린다.
- **Note:** 추출 호출 입력의 **99.4%가 고정 프롬프트**(실측). 비용 레버는 분류 정확도가 아니라
  호출 단위다 → `extract_collected` 는 스레드 배치(`extract_thread`)로 돈다.
- **Don't:** 소스 편향을 평균내지 말 것 — 소스별 net + 갭으로 표시. **'긍정/부정 쏠림' 편향 라벨은 노출 안 함**(2026-07-15 결정). 서포터(홍보성)는 분리하되 소수라도 실내용(향/질감/배송·CS/장단점) 요약해 포함.
- **Note:** 후기(주문) 단위 vs 제품 단위 분리 — `market`·`shipping_cs`는 최상위, 제품별 평가는 `reviews[]`.
- **Important:** 그런데 `attributes` JSONB 에 들어가는 건 제품 항목뿐이라, `index_post` 가
  `shipping_cs` 를 제품별 팬아웃 행마다 **복제**해 넣는다. 이 복제를 빼면 종합뷰의
  배송·CS 섹션이 예외 없이 조용한 빈칸이 된다(2026-08-05 `value` 축 교체 시 발견).
- **Important:** `source_ref`(원문 링크 식별자)는 조각 단위 속성이라 팬아웃 행마다 **복제**되지만
  `relevance_meta` 와 소비 방식이 다르다 — 저건 행 단위 집계 입력이고 이건 식별자라 **읽는 쪽에서
  중복 제거**(`source_links.evidence_group_key`)가 필요하다. 안 하면 한 조각이 제품 수만큼 링크로 도배된다.
- **Don't:** `_records_for` 가 rec 에 넣는 값을 요약 프롬프트로 흘리지 말 것. `_source_material` 의
  `ATTR_FIELDS`/`_SALIENT` **화이트리스트**가 `source_ref`(URL·id)의 LLM 유출을 막는 유일한 장치다 —
  payload 를 '남는 키 전부 통과'로 넓히면 그 보장이 사라진다.
- **Warning:** 디시 댓글 **점프 앵커는 없다**(2026-08-06 라이브 확인 — 댓글이 AJAX 렌더라 서버 HTML 에
  앵커 부재). 댓글 링크는 스레드 URL 로 가고 수집기가 굽는 `#cmt` 는 `permalink()` 가 제거한다.
  `comment_no` 는 나중에 켤 옵션으로 보존만 한다([ADR-0009](../docs/adr/0009-source-links-and-owner-media.md)).
- **Important:** 마켓 로고는 무재배포 원칙의 **유일한 예외**다([ADR-0010](../docs/adr/0010-market-logo-assets.md)) —
  ADR-0009 §1 의 배제 대상은 후기 본문·게시물 미디어이고, 마켓 본인 프로필 아바타(1개·320px·
  링크백 필수)만 다운로드한다. **파일 삭제 = 즉시 철회**(모노그램 자동 폴백)라는 성질이 그 결정의
  전제이므로 `logo_asset` 의 파일 존재 확인을 없애지 말 것. 게시물 미디어는 여전히 전면 금지.
- **Note:** `search._dense`/`_sparse` 는 **명명 인덱스**로만 행을 읽는다(`_BASE_COLS`). 예전의
  `r[6]`/`r[:6]` 위치 하드코딩은 컬럼 하나 삽입만으로 **무예외** BM25 파손을 냈다 — 되돌리지 말 것.
- **Note:** 제품 단위 평가 축은 향/질감/소리/지속력 넷 — **`value`(가성비) 축은 제거됨**(2026-08-05,
  [ADR-0008](../docs/adr/0008-drop-value-add-shipping-section.md)). 가격 얘기는 pros/cons 로만 흐른다.

## Cross-module dependencies
- `../app/ui.py` → `pipeline`, `search` (표시 전용, 백엔드 글루는 여기 캡슐화)
- `../sql/schema.sql` ← `db.py`/`index.py` 가 specs↔reviews 조인·메타필터 컬럼 사용
- `../eval/` → `bias`, `sources`, `linking` 오프라인 테스트
- 도메인 규칙·결정 근거: [../MEMORY.md](../MEMORY.md), [../docs/adr/](../docs/adr/)
