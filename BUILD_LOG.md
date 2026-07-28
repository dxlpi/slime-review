# BUILD_LOG — 슬라임 리뷰 RAG

> 평가 하드 게이트("AI 코딩 도구로 만들었다는 생산성 근거")용 실시간 기록.
> 형식: 무엇을 / 핵심 프롬프트 / AI생성 vs 사람수정 / 소요시간.

## 2026-06-17 — Phase 0: 스캐폴딩

**무엇을**: repo 구조화, 기존 산출물 패키지 배치, 스택 결정을 코드 골격으로 고정,
관측성 래퍼(`llm_ops.py`) 구현, 의존성·환경 템플릿 생성.

**핵심 프롬프트(요지)**:
- "저장소를 스캔해 프로젝트를 파악하라"
- "스택은 네가 결정하되 면접에서 방어 가능한 이유를 붙여라"
- "Phase 0 스캐폴딩을 진행하라(LLM 키는 Anthropic 직접 보유)"

**스택 결정(요약 — 상세는 README §스택)**:
- LLM: Claude(`llm_ops` 인터페이스 뒤) — 추출=Sonnet 4.6, 판정=Opus 4.8
- 임베딩: BGE-M3 / 벡터스토어: pgvector / 하이브리드 키워드: kiwipiepy+BM25
- UI: Streamlit / 배포: Render(대안 Fly.io)

**검증**:
- `consolidated_view.py` 데모 실행 → 소스별 정서·갭·집계 정상 출력 확인.
- `sources.py` import + 디시 검색 URL 빌더 정상.
- **디시 라이브 수집 검증 완료(§11-B P0)**: 본문+댓글 end-to-end 정상,
  `[ADJUST]`였던 **댓글 AJAX 엔드포인트/`_GALLTYPE_=M` 라이브 통과**.
  - 첫 실행 "수집 0건"의 원인은 버그가 아니라 데모 키워드가 과도하게 구체적이라
    해당 갤에 매칭 글이 0개였던 것(`연유스무디`=0, `슬라임`=39). `__main__` 키워드 수정.
  - 부수 신호: 수집물 다수가 제품 후기가 아닌 갤 내 잡담 → `relevance.py`(Phase 1) 필요성 라이브 확인.

**AI생성 vs 사람수정**:
- 패키지 구조/스텁/`llm_ops.py`/설정·문서: AI 생성.
- 모델 ID·단가는 `claude-api` 스킬로 실값 확인 후 반영(추측 금지).
- 발견: Opus 4.8/Sonnet 4.6 은 `temperature` 제거 → JSON 결정성은
  `temperature` 가 아니라 structured outputs(`output_config.format`)로 확보하도록 수정.

**소요시간**: 약 1세션(스캔 + 결정 + 스캐폴딩).

**다음(Phase 1)**: 디시 라이브 1회 검증 → `relevance.py`(관련성 필터 기준 필요) → 소량 데이터셋.

## 2026-07-15 — 1층 스펙에 비즈(beads) 섹션 추가

**무엇을**: 비즈(구성요소지만 구매 결정 요인)를 1층 공식 스펙의 **1급 필드 `beads`**(오픈어휘 `TEXT[]`)로 분리. 제품 아니라 구성요소라 별도 product 행 금지·base_combo 미포함, 없으면 `[]`.

**핵심 프롬프트(요지)**:
- "비즈는 제품이 아니라 구성요소지만 구매 결정 중요요인이라 비즈 섹션을 따로. 비즈 없는 슬라임도 있음. 마켓별 자체제작 명칭 상이(지렁이비즈·나뭇잎비즈·퍼즐비즈)."
- (재추출 중) "위즈캔디샵은 샵 이름이 아니라 제품명이다" → 무시태그 목록 좁힘.

**구현(파일)**: `extract.py`(`_SPEC_PROPS.beads` strict array + `LAYER1_SYSTEM` 비즈 라인) · `sql/schema.sql`(`specs.beads TEXT[] DEFAULT '{}'` + 멱등 ALTER) · `pipeline.py`(`_upsert_spec(...,beads)`·`ingest_hashtag`·`list_products`·`consolidated_for`) · `layer1.iter_specs`(6-튜플) · `data/layer1_fixture.json`(6제품 backfill) · `app/ui.py`(비즈 노출) · `eval/test_bias.py`(strict 계약에 beads).

**검증(라이브)**: 저장 raw 재추출(재스크랩 $0, LLM ~$0.015/run) → 머머 7제품 전원 beads 정상(위즈캔디샵→`[지렁이비즈]`, 첵스→`[첵스 비즈]`, 곰돌디핑→`[감자칩비즈]`…), **어떤 비즈도 제품행 안 됨**. `consolidated_for`/`list_products`/DB/JSON 전 경로 beads 일치. 오프라인 strict 스키마 테스트·UI headless AppTest green.

**AI생성 vs 사람수정**: 스키마/DB/파이프라인/프롬프트 전부 AI 구현. 사람은 (1) beads 분리 요구, (2) 위즈캔디샵=제품명 정정만. 이 2개 피드백이 두 회귀를 잡음:
- **회귀①**: LLM 이 '샵/캔디' 든 고유태그(#위즈캔디샵)를 shop 태그로 오인해 드롭 → 무시목록을 '마켓 자기이름+광역슬라임어'로 못박아 수정.
- **회귀②**(재추출 중 발견): LLM 이 향료어를 유령 제품으로 분리(향 '에그노그'를 `#키튼우유크림롤` 글에서 제품 '에그노그'로) → run 마다 튐. **결정적 제품 게이트**(제품명은 반드시 그 캡션의 해시태그) 추가로 원천 제거(실제 7제품 전원 자기태그 매칭, over-drop 0). LLM 비결정성을 코드 불변식으로 대체한 사례.

**소요시간**: 약 1세션(구현 + 라이브 재추출 3회 + 회귀 2건 수정).

## 2026-06-17 — Phase 2: 추출 연결 + LLM 벤더 전환(Anthropic→OpenAI)

**무엇을**: `한글과자한줌` 실후기 1건을 통과시킬 2층 추출 구현 + LLM 벤더 교체.

- `extract.py`: 스펙의 2층 스키마를 structured outputs용 `LAYER2_SCHEMA`(json_schema)로,
  추출 규칙을 `LAYER2_SYSTEM`으로 옮김. 모든 블록 nullable, FEEL_VOCAB enum 강제.
- **벤더 전환**: 사용자가 OpenAI 키 사용 결정 → LLM 레이어만 교체.
  - 웹에서 현재 단가 확인(2026-06): gpt-5.4-mini $0.75/$4.50, gpt-5.4 $2.50/$15.
  - 티어 분리: 추출=`gpt-5.4-mini`, 판정=`gpt-5.4`. (이전 Anthropic 안보다 저렴)
  - `llm_ops.py`만 OpenAI SDK(`response_format` json_schema strict)로 재작성.
    `config`/`.env`/`requirements`/문서 동반 수정. **`extract.py` 등 파이프라인은 무변경**
    → `llm_ops` 인터페이스 분리가 실제로 벤더 락인을 막은 증거(면접 포인트).

**설계 메모**: GPT-5 계열은 추론 모델이라 temperature 무시/제한 가능 → temperature 미전송,
결정성은 structured outputs로 확보(원래 그렇게 설계해 전환 비용 0).

**소요시간**: 약 1세션(추출 구현 + 단가 조사 + 벤더 전환).

**반복(라이브 피드백 기반)**:
- 1차 실행 결과로 추출 오류 7건 확인 → `LAYER2_SYSTEM` 하드닝 + 스키마 description 추가 → 전부 해소.
- 사용자 도메인 정정: '걀걀거림'=sound(질감 아님), 지속력≠배송. 프롬프트·스키마에 반영([[메모리]]).
- 구조 결함 발견: 1후기=1제품 가정이 **비교글**(한글과자한줌 vs 과일사탕한줌)에서 깨짐
  → 최상위를 `reviews[]` 배열로 전환(제품별 분리 추출). `flags`는 리뷰 전체로.

**다음**: 사용자 키로 재실행 → 비교글이 제품 2개로 분리되는지 확인 → 개체연결(`linking.py`).

## 2026-06-18 — Phase 3: 개체연결(`linking.py`)

**무엇을**: 후기의 `mentioned_market/product` → KB 정규 레퍼런스 + 보류(abstain).

- 라이브 재실행으로 비교글이 제품 2개(한글과자한줌/과일사탕한줌)로 분리 확인. 다만
  '한쪽 전용 단점'(과일사탕의 걀걀거림/비즈)이 양쪽에 복제되는 누수 발견 → 규칙 추가:
  "양쪽 반영은 작성자가 직접 견준 축(향·재미)만, 한쪽 전용 단점은 그 항목에만." → 골드 확정([[메모리]]).
- `linking.py`: KB 명부를 표면형/초성 역인덱스로 구성. 마켓은 표면형(0.95)>초성(0.85),
  충돌이면 확신도=1/후보수 → 임계(0.6) 미만 abstain. 풀워드('빈짱')·초성('ㅂㅉ') 같은 키로 환원.
- **제품은 보류 설계**: KB `products[]` 미시드(1층 공백) → 검증 그라운드 트루스 없음 →
  제품은 매칭 않고 표면형만 잠정 보존. "정답 없으면 안 지어낸다"의 실증(면접 포인트).
- 셀프테스트 통과: ㅂㅉ→빈짱, 자사몰→보류, 가짜 충돌 KB(ㅁㅁ 2후보)→보류.

**AI생성 vs 사람수정**:
- AI 초안에서 **초성 인덱스 중복등록 버그**(같은 마켓을 `choseong`와 `choseong(market_word)`
  양쪽에 등록 → ㅂㅉ가 거짓 충돌) 발견 → 마켓별 키 집합화로 수정. 셀프테스트가 잡아냄.

**다음**: 약칭 사전 시드 + 개체연결 정답셋(사용자) → Phase 4 색인·검색(`index.py`/`search.py`).

## 2026-06-18 — Phase 2 재반복: 스키마를 도메인에 맞춰 재구성

**무엇을**: 비교글 재실행에서 3개 제품으로 과분할 + 마켓 유실 발견 → 스키마 구조 전환.

- 증상: "ㅂㅉ 한줌 / 배송중 / 한글과자한줌 / 과일사탕…" 후기가 **3제품**으로 분할.
  제목+배송줄이 유령 제품 'ㅂㅉ 한줌'이 되고, 진짜 두 제품의 `mentioned_market`이 null로 유실.
- 진단: **마켓·배송은 제품 단위가 아니라 '후기(주문) 단위' 사실**. 제품마다 칸을 두니
  모델이 배송줄을 제품으로 오인하고 마켓 귀속이 흔들림.
- 수정(프롬프트 우기기 대신 구조): `market`·`shipping_cs` 를 **최상위(후기 단위)로 이동**.
  제품 항목(`reviews[]`)은 순수 평가만(scent/texture/sound/longevity/value/overall).
  → 유령 제품·마켓 유실이 구조적으로 불가능. 제목 머리말 마켓은 모든 제품에 공유.
- 추가 규칙: '주어 생략 평가는 직전 등장 제품에 귀속, 양쪽 복제 금지'(걀걀거림 누수 대응).
- `linking.py`도 동기화: `link_post(doc)` 가 후기 단위 market 하나를 전 제품에 공유 매핑(검증됨).

**설계 교훈**: 모델이 반복해서 틀리면 프롬프트보다 **스키마가 도메인 구조와 어긋났는지**를 먼저 본다.
주문 단위 사실 / 제품 단위 평가 분리 = 모델의 자유도를 줄여 오류를 구조적으로 차단(면접 포인트).

**다음**: 사용자 키로 재실행 → market="ㅂㅉ" + reviews 2개(걀걀=과일사탕만) 확인 → 골드 확정.

## 2026-06-18 — Phase 4: 색인·검색(`index.py`/`search.py`/`db.py`)

**무엇을**: 추출·연결 후기 → BGE-M3 임베딩 → pgvector 적재 → 하이브리드 검색·근거 답변.

- 라이브 재실행 통과: 구조 전환 후 market="ㅂㅉ"(최상위)·reviews 2개·걀걀=과일사탕만. 3/3 목표 달성.
  사소한 nit 3개(재미→texture, 향 evidence, 과일사탕 sound neu) 손보아 첫 eval 골드 확정
  → `eval/layer2_gold.json` 저장.
- **인프라**: `docker-compose.yml`(pgvector/pgvector:pg16) + `sql/schema.sql`(specs↔reviews, HNSW).
  배포 시 같은 schema.sql 을 Render 관리형 Postgres 에 적용해 그대로 이전.
- **무재배포 설계**: 원문 본문을 저장하지 않는다. 구조화 필드로 만든 '렌더링 텍스트'(예:
  "[빈짱 과일사탕한줌] 향: 컨셉향 좋음 / 질감: 비즈양 적은 아쉬움 …")를 임베딩·보관하고,
  이게 곧 검색 대상이자 인용 근거. evidence 폴백으로 recall 보강.
- **하이브리드**: dense(pgvector 코사인) + BM25(kiwipiepy 형태소, 앱단) → RRF 융합.
  메타필터는 컬럼 화이트리스트로 SQL 인젝션 차단. 답변은 소스(amos/insta)별 구분(편향 미평균).
- **지연 import**: psycopg/BGE-M3/kiwi 를 함수 내부에서 로드 → 의존성 미설치 환경에서도
  모듈 import·순수 로직(render/RRF/필터) 테스트 가능.

**AI생성 vs 사람수정**:
- 순수 로직 라이브 검증: render_review(렌더), _where(화이트리스트·인젝션 차단), _rrf(순위 융합) 통과.
- AI 초안 버그: `sent()` 내부함수를 `phrase()`로 리팩터 후 value 블록이 옛 `sent()` 참조(NameError)
  → 즉시 수정. 렌더 빈서술('질감:  아쉬움') → evidence 폴백으로 개선.

**남은 검증(사용자 환경)**: Docker 데몬 기동 → `docker compose up -d` → `pip install -r requirements.txt`
→ `python -m slime_rag.db`/`index`/`search`. (BGE-M3 첫 다운로드 ~2GB)

**다음(Phase 5~6)**: `consolidated_view` 연결(소스갭·향불일치 화면) → `llm_ops` 대시보드 → Streamlit UI → 배포.

## 2026-06-29 — Phase 4 라이브 검증(사용자 환경 end-to-end)

**무엇을**: 위 "남은 검증"을 실제 환경에서 수행. DB→색인→하이브리드 검색까지 라이브 통과.

**통과**:
- `db`: pgvector 확장 활성 + `reviews`/`specs` 테이블 생성 확인.
- `index`: 골드 1건 → 제품 2행 적재. linking 이 `ㅂㅉ→빈짱` 정규화, 임베딩(1024d)·BM25 토큰(23/57개) 동반.
- `search`(하이브리드, LLM 불필요): 향질문+market필터→과일사탕(좋음)>한글과자(아쉬움); 걀걀(소리) 질문→BM25가 과일사탕만 상위; 인젝션 컬럼 거부.
- 순수 로직 18/18(render/_sent/_where/_rrf/_format_context) 별도 통과.

**환경 마찰(전부 실환경에서만 드러남) & 처치**:
- 호스트 5432·5433 둘 다 네이티브 Postgres 점유 → compose 포트 `55432:5432` 로 매핑, `.env` `DATABASE_URL` 동기화.
- transformers 가 `torch.load` CVE(CVE-2025-32434)로 torch≥2.6 요구(BGE-M3 가 `.bin` 배포) → `requirements.txt` 에 `torch>=2.6` 핀.
- anaconda base 환경 의존성 충돌(transformers 5.x ↔ 구 peft 의 `BloomPreTrainedModel` import) → **프로젝트 전용 `.venv`** 로 격리 설치(배포와 동일한 깨끗한 resolve). 이후 전부 `.venv/bin/python` 으로 실행.

**`answer()` 검증 완료**: 키 주입 후 라이브 통과. "빈짱 한줌 향?" → 제품별 분리(과일사탕=향긍정/한글과자=향아쉬움), 인용 [1][2], **소스 편향 명시**("근거는 amos만, 부정 쏠림 가능") — 평균내지 않음. 설계대로 동작.

**다음(Phase 5~6)**: `consolidated_view` 연결(소스갭·향불일치 화면) → `llm_ops` 대시보드 → Streamlit UI → 배포.

## 2026-06-29 (2) — 1층 fixture 시드 + Phase 5·6 연결(end-to-end 완성)

**배경**: IG `business_discovery` 가 App Review(advanced access)를 요구해 라이브 1층 수집이 막힘
(2시간 검증: 본인 IG 직접읽기 성공·standard access 켜짐·버전 다운그레이드 불가인데 그 엔드포인트만 `#10`).
→ **결정**: 1층 공식 스펙은 라이브 API 대신 **큐레이션 fixture**(`data/layer1_fixture.json`)로 주입.
`sources.InstagramSource` 는 business_discovery *인터페이스*로 남겨 라이브/픽스처 동일 코드경로(포트폴리오 설계 근거).

**한 일**:
- `data/layer1_fixture.json` — 마켓 게시물 캡션(원문) + 손추출 1층 스펙. 실데이터 3마켓 6제품 시드:
  봄(카피바라4pm스낵·홈메이드말차라떼·허니푸냥이), 머머(레몬커드쉘도넛), 빈짱(한글과자한줌·빠삭귤).
  타입 표기는 마켓 관습차를 보존 — 봄슬은 enum 미명시→`type:[]`+`type_other`(솝퐁말 등),
  빈짱은 "크런치" 직접명시→`type:["크런치"…]`. **명시된 것만, 비유는 type 승격 금지** 원칙 유지.
- `layer1.py` — `iter_specs()` 추가(시드 KB→specs 행). `seed_kb_products()` 로 KB `products[]` 채워 §11-C 보류 해소.
- `pipeline.py`(신규) — end-to-end 오케스트레이터: 스키마→1층 specs 적재(upsert)→2층 골드 색인(멱등)
  →`spec_id` 조인→검색/종합뷰 글루. 소스→플랫폼(amos→dcinside) 매핑 단일화. UI 데이터접근 캡슐화.
- `app/ui.py` — Phase 6 Streamlit 완성: ① 챗(근거 인용·소스 미평균) ② 종합뷰(소스별 net·갭·향불일치·소스aware 요약)
  ③ 사이드바 마켓/소스 필터 + 1층 스펙 패널. `pipeline`/`search` 에만 의존.

**라이브 검증(.venv + pgvector 55432)**:
- `python -m slime_rag.pipeline`: specs 6행 적재 → 골드 2행 색인 → `한글과자한줌` spec 조인 성공
  (`과일사탕한줌` 은 1층 미시드라 미조인 — 부분조인 정상). 검색은 "한글과자 향/비즈?" 에
  **향=아쉬움 인용 + 비즈는 '한글과자엔 직접언급 없음, 과일사탕에만'으로 정직 분리**. 종합뷰는
  디시 1건만이라 갭=None 을 명시적으로 라벨(인스타 후기 없음).
- `streamlit.testing.v1.AppTest`: 스크립트 실행 예외 0. 마켓 셀렉트(봄/머머/빈짱)→제품→종합뷰 버튼까지
  전 경로 렌더(소스별 정서·향불일치·호평/지적·요약 섹션 생성).

**AI생성 vs 사람수정**: pipeline/ui 초안 그대로 통과(헤드리스 AppTest 로 클릭 경로까지 자동검증).
1층 타입 매핑은 사람 판단으로 보수화(봄슬 enum 미강제 → type_other 보존).

**남은 것**: Render 배포(관리형 pgvector + schema.sql) + 데모 URL — 평가 하드게이트 마지막 1개.
2층은 현재 골드 1건(샘플) — 디시 라이브 수집 확장 시 소스 갭 화면이 실제로 채워짐.

## 2026-07-14 — 2층 인스타 해시태그 라이브 소스(Apify 스크래퍼)

**배경**: 2층 *긍정편향* 소스(인스타 해시태그 캡션)를 라이브로 확보하려 했으나, 공식 Graph API
`ig_hashtag_search` 는 `business_discovery` 와 같은 벽(advanced access = App Review·비즈 인증)에 막힘.
→ **결정**: 서드파티 스크래퍼 `apify/instagram-hashtag-scraper`(공개 데이터만)로 스크래퍼 티어 대체.
공식 API 아님을 **투명 라벨링**(`meta.source="apify"`, `scraped=True`)해 "공식 API vs 책임 수집" 서사 보존.

**검증 벤더 사실(2026-07-14, 라이브 확인)**: $1.90/1000건(=30건당 약 $0.057), 해시태그당 ~30건 상한(=1페이지).
⚠️ **정정**: 사전 조사 때 "무료 티어는 API 호출 불가"라 적었으나 **틀림** — `LIMITED_PERMISSIONS`(무료/제한) 플랜에서
`.call()` API 가 정상 동작하고 30건을 반환했다(로그 `Running under "LIMITED_PERMISSIONS"` + `Scraped 30 results`).
무료의 실제 제약은 API 차단이 아니라 **"1페이지(≈30건)로 제한"**(로그 `limited to one page for free users`).
→ 폭(큐레이션 태그 다수)으로 recall, 깊이는 포기 전략은 그대로 유효.

**한 일**:
- `sources.ApifyHashtagSource(Source)` — 기존 `Source` 심 뒤에 드롭인. `platform="instagram"`(하류 소스편향
  집계에서 긍정편향 IG 로 취급) + `meta.kind="hashtag_caption"`. `_run` 이 유일한 네트워크 경계(테스트 주입점).
  토큰 없거나 apify-client 미설치·API 실패 시 **예외 없이 [] 반환**(collect_all 회복력 = DCInside 스킵과 동일).
  관측성: 요청 태그 수 / 반환 건수 / 예상비용($items/1000×1.90) 로깅(무음 상한 금지).
- `data/ig_hashtags.json` — 큐레이션 해시태그(global 7 + 데모 마켓 16, 총 23) — fixture 관습 그대로 `_` 메타키 무시.
- `config.py` — `apify_token`/`apify_hashtag_actor`/`apify_results_per_hashtag`/`ig_hashtags_path` + `.env` 문서키.
- `requirements.txt` — `apify-client>=1.7`(설치 검증: 3.0.6, v3 API `.actor().call()`·`.dataset().iterate_items()` 일치).
- `eval/test_apify_source.py` + `data/apify_hashtag_sample.json` — **무비용 오프라인 매핑 테스트**(`_run` 샘플 주입).
- `InstagramSource._collect_hashtag` 스텁에 "라이브는 ApifyHashtagSource 사용" 포인터 추가(Graph 경로는 유지).

**검증(오프라인 무비용 + 라이브 확인)**:
- `python -m eval.test_apify_source`: 7테스트 통과 — 매핑·provenance(source=apify/scraped), shortCode 중복접기,
  저품질('ㅋㅋ')/빈캡션 드롭, limit 준수, keywords→태그 파생(초성 단독 'ㅂ' 제외), 토큰 미설정 회복력, 큐레이션 로드.
- 회복력 라이브 확인: 토큰 있으나 apify-client 미설치 → 경고 로그 + [] 반환(예외 0).
- **라이브 스모크 통과(`APIFY_TOKEN` 주입)**: `#슬라임후기` 30건 반환 → limit=10 방출, 예상비용 $0.057 로깅.
  실제 한국어 캡션·`owner_username`·`hashtag` provenance 정상 흐름(웨이즈/연찌/폴린 등 마켓 태그 캡션 확인).

**버그 2건 사람수정**:
1. keyword 파생 필터를 `len(kw)<2` 로 짰다가 '봄'(완성형 1음절, len 1)이 잘못 배제됨 →
   한글 호환 자모 범위(U+3130~U+318F)로 '초성 단독'만 배제하도록 교정. **단위 테스트가 잡음**.
2. apify-client **v3 는 `.call()` 이 dict 아닌 pydantic `Run` 모델을 반환** → `run.get("defaultDatasetId")` 가
   `'Run' object has no attribute 'get'` 로 실패(라이브 첫 실행에서 30건 스크랩됐으나 클라가 못 읽음).
   → `run.default_dataset_id`(v3) + dict `.get`(v1) 양쪽 지원으로 교정. **라이브 로그가 잡음**(오프라인 샘플은
   dict 라 못 잡은 케이스 — 실 API 모델 반환이 원인). 데이터셋 아이템은 v3 도 `Iterator[dict]` 라 매핑은 무영향.

**AI생성 vs 사람수정**: 어댑터/테스트/시드 초안은 AI 생성. 사람수정 2건(자모 필터 경계 + v3 Run 모델 접근).

**정정된 소싱 결론**: 애초 "라이브 해시태그는 유료 게이트"라 계획했으나, 실측 결과 **무료/제한 플랜에서도
API 수집이 동작**(1페이지 ≈30건 상한). 즉 데모용 소량 실데이터는 **무료로 라이브 수집 가능**. 대량/멀티페이지만
유료. → 2층 긍정편향 소스는 이제 **라이브 검증 완료**(디시=부정편향과 대칭 확보).

**남은 것**: 파이프라인 자동색인은 `relevance.py`(스텁, §11-C) 확정 후 연결 — 이번 단계는 **수집까지만**
의도적으로 멈춤. 다음: 수집 캡션 → relevance → extract → index 연결 시 종합뷰 소스 갭이 실제로 채워짐.

## 2026-07-15 — 후기 편향 태깅 (홍보성 분리 + 판매자→1층 라우팅)

**무엇을**: 인스타 해시태그 후기(2층 긍정편향)에 편향 축을 추가. (1) **홍보성**(대가·무상 제공
문구)은 드롭이 아니라 `review_class='promo'` 로 라벨해 실사용과 **분리 요약**, (2) **판매자**
(ownerUsername=KB 핸들) 게시물은 후기에서 빼고 **1층 공식 스펙으로 자동 추출**. 계획서
`.omc/plans/bias-tagging-hashtag-reviews.md` 대로 Step 1~7 구현.

**핵심 프롬프트(요지)**:
- "Implement the bias tagging hashtag plan."

**한 일**:
- `slime_rag/bias.py`(신규) — `detect_promo`(구문 seed `PROMO_SEED` + `data/promo_markers.json` 로더
  + LLM 폴백 훅; '광고' 단독은 오탐이라 구문 단위로만), `seller_index`(KB `handle`+`handles_alt`
  역인덱스, owner_username 소문자 정규화), `classify`(판매자>홍보성 우선순위), `partition(raws,kb)`
  → `(seller_posts, user_reviews)` + `review_class`/`seller_market` 태깅. 셀프테스트 통과.
- `sources.py` — `ApifyHashtagSource._to_review` 가 캡션 자족으로 `review_class`/`promo_marker` 라벨.
  CLI(`__main__`)에 `[판매자·머머→1층]`/`[홍보성·서포터즈]`/`[실사용]` 태그 표시.
- `extract.py` — 1층 추출 경로 신설: `LAYER1_SCHEMA`(json_schema strict) + `LAYER1_SYSTEM` +
  `extract_spec`(판매자 캡션 → `{product,scent,base_combo,slime_type,evidence}`, 미언급 null).
- `sql/schema.sql` + `index.py` — `reviews.review_class TEXT NOT NULL DEFAULT 'genuine'`
  (`ALTER … ADD COLUMN IF NOT EXISTS` 멱등 마이그레이션 + 인덱스). `index_post(review_class=…)`.
- `consolidated_view.py` — headline(by_source/gap/praised/criticized/summary)은 **genuine 만**,
  홍보성은 `promo_view`(별도 `PROMO_SUMMARY_PROMPT`)로 분리. 홍보성 0건이면 `promo_view=None`(회귀 없음).
- `pipeline.py` — `_records_for` 가 `review_class` 를 레코드에 실어보냄. `ingest_hashtag(keywords)`
  글루(수집→partition→판매자 specs upsert / 실사용·홍보성 색인→조인, 관측성 카운트). specs upsert 는
  `_upsert_spec` 헬퍼로 fixture 시드와 공유.
- `eval/test_bias.py`(신규, 9테스트) + `test_apify_source.py`(review_class 케이스 추가).

**검증(오프라인 무비용 + DB 통합)**:
- `python -m eval.test_bias`: 9테스트 통과 — detect_promo seed/부정문·LLM 폴백·seller_index·
  판매자>홍보성 우선순위·partition 분리·consolidated 홍보성 분리·LAYER1 strict 구조.
- `python -m eval.test_apify_source`: 9테스트 통과(review_class 라벨 포함).
- linking/layer1 셀프테스트 회귀 없음.
- **DB 통합**: `apply_schema()` 재적용 → 기존 2행 `review_class='genuine'` 백필(무해). 합성 promo 행
  1건 삽입 → `consolidated_for` 가 headline(genuine 1, dcinside만)에서 제외하고 `promo_view.n_promo=1`
  (IG net=1.0)로 분리 확인 → 정리(delete).

**AI생성 vs 사람수정**: bias/extract/consolidated/pipeline/test 초안 그대로 통과. 사람수정 1건:
`ingest_hashtag` 의 `join_specs(connect())` 커넥션 누수 → `with connect()` 컨텍스트로 교정.

**남은 것**: 라이브 Apify 수집→ingest_hashtag 실데이터 1회(유료/무료 30건 게이트)는 선택 확인.
relevance.py(후기 vs 비후기)는 여전히 별도 축·범위 밖(§8 TODO). Render 배포가 마지막 하드게이트.

## 2026-07-15 — 홍보성 판정 캐스케이드(게이트 recall + LLM verdict)

**무엇을**: 홍보성 판정을 2단 캐스케이드로 전환. 값싼 결정적 게이트가 '홍보 의심' 캡션만 통과시키고
(recall), 통과분만 LLM verdict 로 정밀 판정(precision). 명백한 실사용은 게이트에서 즉시 genuine
단락 → LLM 호출을 5~10× 절감. (플랜: `.omc/plans/promo-gate-llm-cascade.md`)

**핵심 프롬프트(요지)**:
- "Implement promo gate plan."

**근거(왜 캐스케이드)**: 키워드 seed 는 verdict 에 부적합 — precision 실패(`서포터분들은…내가 만졌을 땐`
인용문 오탐 + `할인/비매/서비스` 구매 오탐). 하지만 recall 은 값싸고 강함. → 키워드를 **decider 가
아니라 gate** 로 강등하면 gate 는 recall 만, precision 은 LLM 이 책임진다. 비용 절감 + 관측성 서사
(제논 하드게이트) + §7 ML distillation 라벨 축적을 동시에 얻는다.

**한 일**:
- `slime_rag/bias.py` — `GATE_LEXICON`(`PROMO_SEED` 상위집합 superset, 애매어 증정/나눔/무료/이벤트/
  당첨/체험/제공/광고/sponsored/gifted 등 recall 어휘 추가; 순수 구매어 비매·서비스·할인·세일은 제외 D7),
  `load_gate_terms`(`data/promo_gate_terms.json` 선택 로더), `promo_gate(text,terms)`(결정적 무비용
  부분문자열, 빈문자/초성단독 False), `make_gated_llm_promo_detector`(게이트 단락=`(False,None)`·LLM
  미호출, 통과분만 `make_llm_promo_detector` 재사용해 try/except 폴백 유지, 선택 `on_label` 훅=D6 라벨 싱크).
- `data/promo_gate_terms.json`(신규·선택) — recall 어휘 config(코드 수정 없이 튜닝). `_` 접두 키 무시.
- `slime_rag/pipeline.py` — `ingest_hashtag` 가 `make_gated_llm_promo_detector` 사용. 게이트 통과율
  집계(non-seller 대상): `gate_suspect`/`llm_calls_saved` 를 `counts`·로그로 노출.
- `slime_rag/sources.py` — CLI 편향 태깅을 게이트 detector 로 교체. `[bias] 게이트→LLM 캐스케이드` 표기,
  순수 실사용 캡션은 LLM 없이 `[실사용]` 단락.
- `eval/test_bias.py` — 게이트 테스트 7종 추가(recall·순수 구매어 제외 D7·순수 실사용 단락·detector
  단락(LLM 0호출)/통과(1호출)·precision 보존(인용문 genuine)·on_label 훅·config 로드).

**검증(오프라인 무비용)**:
- `python -m eval.test_bias`: 전량 통과(기존 + 신규 게이트 7종). 단락 증거 = stub LLM `calls==[]`,
  통과 시 정확히 1호출. `set(PROMO_SEED) ⊆ set(GATE_LEXICON)`·`비매/서비스/할인/세일` 미포함 단언.
- `python -m slime_rag.bias` 셀프테스트 회귀 없음(게이트 캐스케이드 어서션 추가).
- `pipeline`/`sources` import 정상.

**AI생성 vs 사람수정**: 게이트 코어/config/pipeline·sources 배선/테스트 초안 그대로 통과. 사람수정 0건.

**라이브 검증(Apify 실수집, 2026-07-15)**: `#레몬커드쉘도넛` 실캡션 7건 수집.
- **게이트통과 1 / genuine단락 6 → 절감 LLM호출 6건**(수집당 7→1호출, **-86%**).
  `gate_passthrough_rate = 1/7 = 0.14`(<0.6 목표 충족). LEDGER 실호출 수 = 1 로 n_suspect 와 정합.
- precision: 통과 1건(@wavvyslm '서포터 …')만 LLM→`홍보성·supporter` 정확 판정.
- **D7 실증**: @angdduslm 캡션(`비매`·`추가 할인`, 순수 구매어)이 게이트에서 **즉시 genuine 단락**
  (LLM 미호출) → 직전 비결정 flip 지점을 게이트가 값싸게 제거.
- 비용: 단일 호출 $0.0015(무게이트 7호출 ≈ $0.0105 대비 절감). Apify 무료 티어 1페이지 상한(7건).

**남은 것**: Render 배포가 마지막 하드게이트.


## 2026-07-15 (2) — 속성 분리형 리뷰 요약(향/질감/장단점 · 인스타/디시/통합)

**무엇을**: 종합뷰의 단일 `summary` 블롭을 **소스별 3블록(인스타/디시/통합) × 3섹션(향/질감/장단점)**
구조로 교체. 향·질감·장단점 중 **언급 없는 섹션은 빈칸**(지어내기 금지). 통합은 평균이 아니라
소스 갭 reconciliation. + 공식 스펙 인스타 URL(`source_permalink`) DB 플러밍.
(플랜: `.omc/plans/attribute-sectioned-review-summaries.md`)

**핵심 프롬프트(요지)**:
- "리뷰 요약에 질감/향 섹션을 나누고 장단점도. 언급 없으면 그냥 비워둬."
- "장단점은 향·질감과 별개 총괄 섹션."

**근거(왜 구조화)**: 도메인 규칙(§10 미언급 null·근거 기반)과 정합 — 섹션을 스키마 `null` 로 강제하면
LLM 이 빈 향/질감을 지어낼 여지가 없다. 소스당 structured output **1콜**(`{scent,texture,pros,cons}`)로
3섹션 동시 산출 → 섹션별 개별 호출(3×) 대비 절감. 통합은 두 소스 요약 + `sentiment_gap` 을 입력받아
'평균 금지, 갭 명시'.

**한 일**:
- `slime_rag/consolidated_view.py` — `SOURCE_REVIEW_SCHEMA`(strict, scent/texture nullable + pros/cons),
  `SECTION_PROMPT`/`INTEGRATED_PROMPT`, `_source_material`(속성별 evidence 재료, 미언급 속성 제외),
  `_sectionize_source`/`_sectionize_integrated`. `build_consolidated` 개편: `summary`→`review_summaries`
  (instagram/dcinside/integrated), 소스 후기 0=None·통합은 두 소스 있을 때만. `llm_sectionize` 콜백 추가
  (홍보성 `promo_view` 텍스트 요약은 `llm_summarize` 로 현행 유지).
- `sql/schema.sql`·`slime_rag/layer1.py`·`slime_rag/pipeline.py` — `specs.source_permalink` 컬럼(멱등
  마이그레이션) + `iter_specs` 7-튜플 + `seed_kb_products` 게시물 permalink 폴백(사용자 결정) +
  `_upsert_spec`(COALESCE 로 URL 보존) + `list_products`/`consolidated_for` 노출. `consolidated_for` 가
  `llm_sectionize`(structured) 주입.
- `app/ui.py` — `_render_review_block`(향/질감/장단점, 빈 섹션 생략) + 3블록 렌더 + 스펙 URL 링크.
  app body 를 **`main()` 가드**(`__name__=='__main__'`) 뒤로 이동 → import 시 부트스트랩(DB) 미실행 →
  렌더 헬퍼 헤드리스 테스트 가능(streamlit run 은 `__main__` 이라 그대로 동작).
- `eval/test_consolidated_sections.py`(신규 5케이스) + `eval/test_ui_render.py`(신규, AppTest 헤드리스).

**검증(오프라인 무비용)**:
- `python -m eval.test_consolidated_sections`: 미언급→빈칸(None), 단일소스→통합None, 홍보성 분리,
  no-LLM 회귀, `_source_material` 언급속성만 — 전량 통과(fake sectionize, LLM 0호출).
- `python -m eval.test_ui_render`: AppTest 로 3블록·URL 링크·빈 요약헤더 생략 렌더, **예외 0**.
- 회귀: `test_bias`/`test_apify_source` + `consolidated_view`/`layer1` 셀프테스트 전량 통과.

**AI생성 vs 사람수정**: 스키마/프롬프트/플러밍/렌더/테스트 초안 그대로 통과. 사람수정 0건.

**미결(데이터)**: fixture `posts[].permalink` 가 전부 placeholder(None) → 실제 게시물 URL 채우면
스펙 링크가 즉시 표시(플러밍은 완료·null 이면 링크 생략으로 그레이스풀). 라이브 LLM 요약 품질은
디시/인스타 2층 데이터 확장 후 검증 예정.

**라이브 검증(2026-07-15)**: `#레몬커드쉘도넛` 실수집(Apify) → 인스타 genuine 4·promo 1 색인 →
`consolidated_for('머머','레몬커드쉘도넛')` 가 실 LLM 으로 향/질감/장단점 3섹션 생성. 이견("인위적 레몬빵")은
평균 대신 ➖con 으로 보존, 서포터 1건은 promo_view 로 분리. 통합은 디시 부재로 None(가짜 평균 안 함).

**남은 것**: Render 배포가 마지막 하드게이트.


## 2026-07-15 (3) — 리뷰 요약 표시 조정(편향 라벨 제거 + 서포터 실내용 포함)

**무엇을**(사용자 피드백): (1) '긍정 쏠림/부정 쏠림' 편향 라벨을 노출에서 전부 제거. (2) 서포터
(홍보성) 후기를 소수라도 포함하되 '표본 적음' 무내용 disclaimer 대신 향/질감/장단점 실내용으로 요약.

**한 일**:
- `consolidated_view.py` — `PLATFORM_BIAS`·`bias_label` 제거(`per_source_sentiment` 은 net·건수만).
  `PROMO_SUMMARY_PROMPT`(텍스트 disclaimer) → `SUPPORTER_SECTION_PROMPT`(구조화 향/질감/장단점).
  `build_consolidated` 에서 `llm_summarize` 파라미터 제거(dead), promo_view = `{n_promo,scent,texture,pros,cons}`
  (`_sectionize_supporter`). INTEGRATED/SECTION 프롬프트·모듈 docstring 의 쏠림 문구 제거.
- `pipeline.consolidated_for` — `llm_summarize` 람다 제거, `llm_sectionize` 만 주입.
- `app/ui.py` — 소스 라벨 `디시 아모스갤/인스타`(쏠림 표기 삭제), 메트릭 help·`_meta` 에서 bias_label 제거,
  '🎁 서포터 리뷰 (협찬·무상 제공)' 블록 렌더 추가.
- `search.py` — 근거답변 프롬프트/헤더의 '디시=부정/인스타=긍정 쏠림' → '소스별 구분(평균 금지)'로 중립화.
- 테스트: `test_bias`(promo_view by_source→섹션형 어서션), `test_ui_render`(서포터 블록 렌더 + '쏠림'
  문자열 부재 어서션, mock bias_label 제거). 전량 통과.

**라이브 검증**: `머머/레몬커드쉘도넛` — by_source 에 bias_label 없음, promo_view 가 실 서포터 향/질감
(버터크림 느낌·뽀득·몽글 등) 요약. 회귀: 전 오프라인 스위트 그린.

**AI생성 vs 사람수정**: 사람수정 0건. 메모: `review-summary-display-prefs`.

**남은 것**: Render 배포가 마지막 하드게이트.
