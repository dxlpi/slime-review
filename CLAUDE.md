# CLAUDE.md — 슬라임 리뷰 RAG

> 이 문서는 Claude Code가 프로젝트를 이어서 만들기 위한 단일 컨텍스트다. 저장소 루트에 `CLAUDE.md`로 저장하면 자동 로드된다.
> **현재 진행**: Phase 0~6 코드 완성(수집·추출·연결·RAG·종합뷰·UI까지 end-to-end 라이브 검증). **남은 건 배포(Render)뿐**. 1층은 IG App Review 차단으로 fixture 방식 전환(`data/layer1_fixture.json`, 현재 3마켓 6제품 시드). 단계별 상세는 §9, 빌드 기록은 `BUILD_LOG.md`, 스택 근거는 `README.md` 참조.
> **먼저 읽을 것**: §11 "너에게 필요한 입력" — A/B/D 해소, C(도메인 데이터)는 샘플로 파이프라인 완주 가능. 확장은 데이터 추가만.

---

## 1. 목표 & 맥락
한국 슬라임 마켓의 **공식 제품 스펙(정형) + 사용자 후기(비정형)**를 통합해, 출처를 인용하며 답하는 **근거 기반 RAG 어시스턴트**. 제논(GenON) AI Product Engineer 지원용 포트폴리오이며, GenOS(AI Search + 에이전트 + LLM Ops)의 축소판으로 설계한다.

평가 하드 게이트(반드시 충족):
- 배포된 데모 + 리포지토리 + 기술 문서
- **AI 코딩 도구로 만들었다는 생산성 근거** (BUILD_LOG: 핵심 프롬프트 / AI생성 vs 사람수정 / 소요시간)
- 관측성(로깅·메트릭·비용·장애 추적)

## 2. 데이터 아키텍처 (2층)
- **1층 공식 스펙(객관, 그라운드 트루스)**: 마켓 인스타 게시물에서 추출 → 제품별 `향료 / 풀조합 / 종류`.
- **2층 후기(주관)**: 디시 아모스 갤(부정 편향) + 인스타 유저 캡션(긍정 편향). 1층 제품에 개체연결로 매핑.
- **소스 편향은 1급 기능**: 인스타=긍정 쏠림, 디시=부정 쏠림 → 평균내지 말고 **소스별 + 갭**으로 투명하게.

## 3. 소스 & 합법성
| 소스 | 층 | 경로 | 상태 |
|---|---|---|---|
| 디시 아모스 갤 (`id=amos`, 마이너갤) | 2층 후기 | 책임 스크래핑(robots·저속·미재배포·유해필터) | **본문+댓글 라이브 검증 완료**(댓글 AJAX·`_GALLTYPE_=M` 통과) |
| 인스타 Graph API | 1층 스펙 + 2층 유저후기 | business_discovery(핸들) / hashtag(캡션) | 스텁(`InstagramSource`)만 — App Review 벽으로 1층은 fixture 전환 |
| 인스타 해시태그 (Apify 스크래퍼) | 2층 유저후기(긍정편향) | `apify/instagram-hashtag-scraper`(공개 데이터·`meta.source=apify`/`scraped=True` 라벨링) | **`ApifyHashtagSource` 라이브 검증 완료**(2026-07-14, `#슬라임후기` 30건→캡션·provenance 정상). `APIFY_TOKEN` 만 있으면 **무료/제한 플랜도 API 수집 동작**(1페이지 ≈30건/태그 상한, $1.90/1000건) |
| 네이버 블로그·쇼핑 / 유튜브 | (확장) | 공식 API | 보류 — 플러그인으로 나중에 |
- 수집 레이어는 **`Source` 플러그인 인터페이스**라 소스 추가 = 구현체 추가.
- 디시는 익명 UGC·비재배포·robots 준수라 가장 방어 가능한 스크랩. 면접 포인트로 "공식 API + 책임 수집"을 명시.

## 4. 마켓 KB
- **repo에는 데모 13개만 포함**: `data/slime_market_kb_demo.json`. 전체 118개(`slime_market_kb.json`)는 미포함(필요 시 사용자가 제공).
- 데모 마켓: 봄·웨이즈·진통제·베이퍼·머머·푸딩·빈짱·모모네·연찌·캐치·예찬·지나·늪지.
- 머머는 계정 2개 병합(대표 `from.murmurslime`, 보조는 `handles_alt`의 `murmurslime`).
- JSON 구조: 최상위 `{slime_types[], markets[]}`. 각 마켓 필드 = `market, handle, handles_alt[], market_word, choseong, choseong_aliases[], aliases[], products[]`. **`products[]`는 비어있음**(1층 미시드).
- **초성 충돌 12그룹** 존재(ㅁㅁ, ㅇㅇ, ㄴㅈ 등) → 개체연결(`linking.py`)이 확신도를 나눠 보류해야 함.

## 5. 통제어휘 & 스키마
**슬라임 종류(TYPE_ENUM, 1층)**: 폼볼, 촉감류(점토), 디폼, 난사, 눈꽃, 지글리, 크런치, 빈백, 클라우드, 샤베트, 클리어, 버글리, 젤라또 (확장 가능)
**질감 서술어(FEEL_VOCAB, 2층)**: 말랑, 말캉, 쫀득, 퐁신, 폭닥, 크리미, 로션크리미, 얄랑, 매트, 빳빳, 텐션감있는, 흐물거리는, 쳐지는, 흐름성있는 — + `~같은` 비유(feel_simile) + 신규어(feel_other)

1층/2층 JSON 스키마, few-shot, 추출 규칙은 **`prompts/slime_rag_extraction_prompts.md`** 참조. 실제 적용 스키마는 `slime_rag/extract.py`의 `LAYER2_SCHEMA`/`LAYER2_SYSTEM`. 핵심 원칙:
- 명시된 것만, 미언급은 `null`(지어내기 금지)
- 필드별 **근거 스니펫(15자 내외)** → 인용·저작권 회피
- **명시(작성자) vs 추정(모델)** 분리, 점수는 작성자 명시분만 `stated_rating`
- 향 불일치/소스 갭은 LLM이 아니라 **조인·집계 단계에서 계산**
- **편향 태깅(IG 전용, `bias.py`)**: 홍보성(대가·무상 제공 문구 → `review_class='promo'`)은 드롭이 아니라 **분리 버킷**으로 라벨해 실사용과 따로 요약(종합뷰 `promo_view`). 판매자(ownerUsername=KB 핸들)는 후기 아님 → 1층 스펙(`extract.extract_spec`)으로 라우팅. 우선순위 판매자>홍보성. **홍보성 판정은 게이트→LLM 2단 캐스케이드**: 값싼 결정적 어휘 게이트(`promo_gate`, `GATE_LEXICON`=`PROMO_SEED` 상위집합, recall 전용, false positive 허용)가 '홍보 의심'만 통과시키고 통과분만 LLM verdict(`promo_verdict`)로 정밀 판정 → 명백한 실사용은 즉시 genuine 단락(LLM 미호출)으로 호출 5~10× 절감. 순수 구매어(비매·서비스·할인·세일)는 게이트에서 제외(단독으론 구매 맥락, recall 손실 0). `make_gated_llm_promo_detector`로 detector 만 갈아끼워 인터페이스 불변.
- **후기(주문) 단위 vs 제품 단위 분리**(BUILD_LOG 2026-06-18에서 확정): `market`·`shipping_cs`는 최상위(1주문=1마켓), 제품별 평가는 `reviews[]` 배열. 비교글은 제품 수만큼 항목 분리, 한쪽 전용 단점은 복제 금지. → 유령 제품·마켓 유실을 스키마로 차단.

## 6. 파이프라인
```
수집(sources.py)  →  관련성 필터(후기 vs 질문/양도/잡담)  →  추출(1층/2층 프롬프트)
   →  개체연결(KB + 초성/약칭/충돌해소 + 보류)  →  KB 조인(1층+2층)
   →  임베딩·색인(메타필터)  →  검색·근거답변  →  종합뷰+편향집계(consolidated_view.py)
   →  관측성(전 구간 로깅·비용·재시도)
```

## 7. 코드 현황 (`slime_rag/` 패키지 — 재사용, 다시 만들지 말 것)
완성·검증됨:
- **`sources.py`** — `Source` 인터페이스 + `DCInsideSource`(목록·본문·**댓글** 수집, robots·throttle·재시도·노이즈/유해 필터) + `InstagramSource`(business_discovery 인터페이스 + fixture 주입) + **`ApifyHashtagSource`**(2층 해시태그 스크래퍼, `_run`=네트워크 경계, 토큰/패키지/API 실패 시 예외 없이 []·비용로깅) + `collect_all()`. 댓글은 `e_s_n_o` 토큰 자동추출 후 AJAX. **댓글 AJAX·`_GALLTYPE_=M` 라이브 검증 완료**. **Apify 어댑터는 오프라인 매핑 검증 완료**(`eval/test_apify_source.py` 7테스트, 라이브는 유료 게이트).
- **`config.py`** — `.env` 단일 출처(모델/DB/임계값/`layer1_fixture_path`/`apify_*`·`ig_hashtags_path`). `Settings` 데이터클래스.
- **`data/ig_hashtags.json`** — Apify 스크래퍼용 큐레이션 해시태그(`by_market` 마켓 태그만; global 광역어는 폐기). **검색어 없는 광역 수집에만 사용** — 특정 제품 검색은 `_resolve_hashtags` 가 제품명 그대로만 검색(마켓명·'슬라임' 미부착). `_` 메타키 무시. `data/apify_hashtag_sample.json` = 오프라인 매핑 테스트용 출력 샘플.
- **`llm_ops.py`** — 모든 LLM 호출 단일 통로(`LLM.complete`). 로깅·토큰·비용(LEDGER)·재시도·structured outputs. **OpenAI 구현**(벤더는 이 파일에만 의존).
- **`extract.py`** — 2층 추출 러너(`LAYER2_SCHEMA`/`LAYER2_SYSTEM`, 후기/제품 단위 분리) + **1층 판매자→공식 스펙 추출**(`LAYER1_SCHEMA`/`LAYER1_SYSTEM`/`extract_spec`: 판매자 캡션 → `{product,scent,base_combo,slime_type,beads}`, 미언급 null). **`beads`**=비즈/토핑 구성요소 오픈어휘 배열(지렁이비즈·한글비즈·별비즈 등 마켓별 자체명, 없으면 `[]`) — 제품 아니라 구성요소지만 구매 결정 요인이라 1급 필드로 분리(별도 product 행 금지, base_combo 에도 넣지 않음). 무시 태그는 마켓 자기이름+광역슬라임어뿐 — '샵/캔디' 들어간 고유태그(#위즈캔디샵→'위즈캔디샵')는 제품명.
- **`bias.py`** — 편향 태깅(IG 전용). `detect_promo`(구문 seed `PROMO_SEED`/`data/promo_markers.json` + LLM 폴백 훅), `seller_index`(KB `handle`+`handles_alt` 역인덱스), `classify`(판매자>홍보성), `partition(raws,kb)→(seller_posts, user_reviews)`. **홍보성 캐스케이드**: `promo_gate`(값싼 recall 게이트, `GATE_LEXICON`/`data/promo_gate_terms.json`)→`promo_verdict`(LLM precision), `make_gated_llm_promo_detector`(게이트 단락=LLM 미호출, 선택 `on_label` 훅=ML distillation 라벨 싱크). 셀프테스트+`eval/test_bias.py`(게이트 recall/단락/precision 보존/config) 통과.
- **`linking.py`** — 개체연결. KB 표면형/초성 역인덱스, 충돌→abstain. 제품은 KB 미시드라 보류. 셀프테스트 통과.
- **`db.py` / `sql/schema.sql` / `docker-compose.yml`** — pgvector(Postgres, 포트 `55432`). specs(1층) ↔ reviews(2층) 조인 + 메타필터 컬럼(`reviews.review_class` = genuine/promo, `ALTER … IF NOT EXISTS` 멱등 마이그레이션). compose가 schema 초기화.
- **`index.py` / `search.py`** — BGE-M3 임베딩 + pgvector 적재 / 하이브리드(dense+BM25 RRF)+메타필터+근거답변. **라이브 검증**.
- **`layer1.py`** — 1층 fixture 로더 + `seed_kb_products`(KB `products[]` 채움) + `iter_specs`(→specs 행, `(market,product,scent,base_combo,slime_type,beads)` 6-튜플). 셀프테스트 통과.
- **`pipeline.py`** — end-to-end 오케스트레이터: 스키마→1층 specs 적재(upsert)→2층 골드 색인(멱등)→`spec_id` 조인→`list_markets/products`·`consolidated_for`·`answer` 글루. **`ingest_hashtag(keywords)`**: 해시태그 수집→`bias.partition`(게이트→LLM 캐스케이드 detector)→판매자는 `extract_spec`→specs upsert, 실사용/홍보성은 `index_post(review_class=…)`→조인. 게이트 통과율/절감 LLM호출을 `counts`(`gate_suspect`/`llm_calls_saved`)·로그로 노출. UI 데이터접근 캡슐화. **라이브 검증**.
- **`app/ui.py`** — Streamlit 챗+필터+종합뷰. `pipeline`/`search` 연결. **헤드리스 AppTest 검증**(클릭 경로까지 예외 0).
- **`data/layer1_fixture.json`** — 1층 큐레이션 스냅샷(business_discovery 응답 모양). 3마켓 6제품 시드.
- **`consolidated_view.py`** — 소스별 정서·갭·호평/지적·향불일치·소스aware 요약(`SUMMARY_PROMPT`). **headline 은 실사용(genuine)만**, 홍보성은 `promo_view`(별도 `PROMO_SUMMARY_PROMPT`)로 분리. 홍보성 없으면 `promo_view=None`(회귀 없음). 실행 검증됨.
- **`eval/layer2_gold.json`** — 2층 추출 골드셋(현재 비교글 1건, 사람 검수).
- **`prompts/slime_rag_extraction_prompts.md`** — 1층/2층 추출 프롬프트 스펙.

## 8. 남은 컴포넌트 (TODO)
1. **배포** ← **마지막 하드게이트**. Render(관리형 Postgres+pgvector), schema.sql 적용, `streamlit run app/ui.py` 호스팅 + 데모 URL.
2. **관련성 필터** `relevance.py` — **스텁(인터페이스만)**. RawReview가 실제 제품 후기인지 분류(휴리스틱→애매하면 LLM). 비후기 드롭. → **§11-C 사용자 기준 필요**. (현 파이프라인은 골드셋 직색인이라 미차단)
3. **2층 데이터 확장** — 현재 골드 1건(샘플). 디시 라이브 수집→relevance→extract→색인 연결 시 종합뷰의 소스 갭이 실제로 채워짐. 1층도 게시물 추가로 확장.
4. **인스타 해시태그 라이브 수집** — `ApifyHashtagSource` **라이브 검증 완료**(2026-07-14). `APIFY_TOKEN` 만 있으면 무료/제한 플랜도 API 수집 동작(1페이지 ≈30건/태그·$1.90/1000건). ~~"무료 티어 API 불가"는 사전 조사 오류로 실측 정정~~. 공식 Graph `business_discovery`(1층)는 여전히 App Review 벽으로 fixture 유지 — 통과 시에만 라이브. 자동색인은 `relevance.py`(§11-C) 확정 후 연결(현재 수집까지만).
5. **eval 확장** — 골드셋 1건 → 개체연결 정답셋 ~30~50개(§11-C) + 검색 품질 지표.

## 9. 빌드 단계 (Claude Code에 순서대로 — 진행 표시)
**✅ Phase 0 — 스캐폴딩**: repo 구조, 의존성, `BUILD_LOG.md`, 기존 파일 배치. `llm_ops`/`config` 골격. 완료.
**🔶 Phase 1 — 수집·정제**: 디시 수집(댓글 포함) **라이브 검증 완료**. `relevance.py`는 **스텁만**(§11-C 기준 대기).
**✅ Phase 2 — 구조화**: `extract.py`(2층) 완료·검증. 후기/제품 단위 분리 스키마 확정. 1층(인스타/수동)은 미시드.
**✅ Phase 3 — 개체연결**: `linking.py` + abstain 완료, 셀프테스트 통과. 정답셋 평가는 §11-C 대기.
**✅ Phase 4 — RAG**: `index.py`/`search.py`/`db.py` **라이브 검증 완료**(DB→색인→하이브리드 검색→`answer()` 근거·소스편향 답변, 2026-06-29). 실행은 프로젝트 `.venv` + compose 포트 `55432`.
**✅ Phase 5 — 종합·편향**: `consolidated_view.py` 구현·검증 + `pipeline.consolidated_for` 로 DB 연결(소스별 net·갭·향불일치·소스aware 요약).
**🔶 Phase 6 — UI·배포**: `app/ui.py`(Streamlit 챗+필터+종합뷰) + `pipeline.py`(end-to-end 오케스트레이터) **연결·헤드리스 AppTest 검증 완료**. **남은 것: Render 배포 + 데모 URL**(평가 하드게이트 마지막 1개).

## 10. 규칙 & 컨벤션
- 수집은 **책임 있게**: robots 준수, 요청 딜레이, 페이지 상한, 원문 미재배포(스니펫만).
- LLM 출력은 **근거 기반**: 미언급 null, 근거 스니펫, 명시 vs 추정 분리.
- **소스 편향 투명화**: 평균 금지, 소스별+갭. 보정보다 라벨링.
- **관측성 기본 내장**: 외부 콜은 전부 로깅·비용 집계·재시도.
- 새 소스/모델은 인터페이스 뒤에 두어 교체 가능하게(LLM 벤더는 `llm_ops.py` 한 곳에만 의존 — Anthropic→OpenAI 전환이 파이프라인 무변경으로 끝난 게 증거).
- JSON 결정성은 **structured outputs(`response_format` json_schema, strict)**로 확보, 파싱 실패 1회 재시도. ⚠️ GPT-5 계열은 추론 모델이라 `temperature`가 무시/제한될 수 있어 **미전송**한다(과거 "temperature 낮게" 규칙 폐기).

---

## 11. 너에게 필요한 입력 (남은 병목 = C)

### A. 크리덴셜 / 접근 (P0~P1)
- [x] **LLM**: OpenAI 채택 — 추출 `gpt-5.4-mini`, 판정 `gpt-5.4`. 키는 `.env`의 `OPENAI_API_KEY`.
- [x] **임베딩**: BGE-M3(로컬, 콜당 비용 0). 벡터스토어 pgvector. *(Phase 4에서 실제 로더 구현 필요)*
- [x] **인스타 Graph API**: `business_discovery` 가 App Review(advanced access) 요구라 데모 범위 밖 → **1층은 fixture/수동 시드로 확정 전환**(`data/layer1_fixture.json`). `InstagramSource` 는 인터페이스만 설계 보존. (2026-06-29 검증, 메모 `instagram-businessdiscovery-blocked`)
- [x] **배포 타깃**: Render(관리형 Postgres+pgvector), 대안 Fly.io.

### B. 디시 라이브 검증 (P0 — 한 번만) ✅ 완료
- [x] 본문+댓글 end-to-end 정상 수집 확인. 댓글 AJAX 엔드포인트/`_GALLTYPE_=M` 라이브 통과(BUILD_LOG 2026-06-17).

### C. 너만 줄 수 있는 도메인 데이터 (샘플로 파이프라인 완주 — 확장은 데이터 추가만)
- [~] **1층 제품 스펙** — fixture(`data/layer1_fixture.json`)에 **3마켓 6제품 시드 완료**(봄 3·머머 1·빈짱 2). `seed_kb_products`→`specs` 적재로 제품매칭 보류 해소. 나머지 데모 마켓(웨이즈·진통제·베이퍼·푸딩·모모네·연찌·캐치·예찬·지나·늪지)은 게시물 붙이면 동일 방식 확장.
- [ ] **관련성 필터 기준** — 후기 vs 질문 vs 양도 vs 잡담 구분 규칙 + 예시 (아모스 갤은 글 종류 혼재). `relevance.py` 구현 차단 중.
- [ ] **개체연결 정답셋 ~30~50개** — 후기↔(마켓/제품) 손라벨. `linking.py` 정확도 측정용(현재 셀프테스트만).
- [ ] **제품 약칭 사전 시드** — 몽땅←사과몽땅 식 매핑. `linking.link(..., aliases=...)`에 주입.
- [ ] **유해 필터 기준** — 거를 표현/욕설 목록 or 분류기.
- [ ] (선택) 종류·질감 어휘 추가분.

### D. 결정 완료 (기본 제안 → 확정값)
- [x] 임베딩: **BGE-M3**(한국어 친화 + dense/sparse 동시 → 하이브리드 한 모델로).
- [x] 벡터스토어: **pgvector**(1층↔2층 조인 + 메타필터를 SQL로). *(Chroma 안은 폐기)*
- [x] 청킹: **후기 1개=1청크**.
- [x] 검색: **하이브리드(벡터+kiwipiepy/BM25 RRF)+메타필터**.
- [x] 개체연결 보류 임계값: **0.6 미만 → abstain**(`LINK_ABSTAIN_THRESHOLD`).
- [x] UI: **Streamlit**.
