# slime_rag/ — 코어 RAG 패키지

## Purpose (이 모듈이 소유하는 것)
슬라임 리뷰 RAG의 엔진. 소스 수집 → 관련성 → 추출(1·2층) → 개체연결 → 색인 →
하이브리드 검색 → 근거 답변 → 종합뷰·편향집계까지 전 파이프라인. HTTP 층(`../api`)·
화면(`../web`)과 DB 스키마(`../sql`)는 이 패키지를 소비만 한다. 전체 흐름은 [ARCHITECTURE.md](../ARCHITECTURE.md) 참조.

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
| `spec_overrides.py` | **1층 스펙 사람 검수 오버레이**(순수 정책 + 파일 IO, [ADR-0016](../docs/adr/0016-human-in-the-loop-spec-review.md)) — `OVERRIDABLE`(6칸) / `QUEUE_FIELDS`(4칸) · `load`/`save`(원자적) · `apply`(**오버레이가 이긴다**) · `needs_review` · `record`(`was` 보존) · `orphans` · 프로세스 캐시. DB·네트워크·LLM 무의존 |
| `logos.py` | 마켓 IG 프로필 아바타 **1회성 수동 수집** CLI(ADR-0010) — 파이프라인 미배선, 자동갱신 없음 |
| `consolidated_view.py` | 소스별 정서·갭·향불일치 + **기준별 건수**(`criterion_stats`) + 인스타/디시/통합 **리뷰 요약**(6기준 `CRITERIA` × **`{verdict, minority}`**, 미언급=빈칸; 홍보성 분리). **축 둘**(ADR-0015): 제품 축 `build_consolidated`(질감·향·소리·지속력 + 장단점) / 주문 축 `build_order_view`(고객응대·배송, **마켓당 한 벌**, 팬아웃 `_fold_orders` 접기). 마켓 모드(product=None) 지원 — 재료에 제품 라벨. **요약 프롬프트에 1층 스펙 미유입**(스펙↔후기 분리) |
| `rawstore.py` | **수집 원문 스냅샷 저장소** — 가공 전 액터 응답을 그대로 디스크에(`data/raw/<kind>/<key>/<시각>.json`). append-only·최신캡처 우선 병합·키별 워터마크·`manifest`(무과금 관측). 네트워크·LLM·DB 무의존 |
| `db.py` | pgvector(Postgres) 연결 한 곳 |
| `llm_ops.py` | **모든 LLM 호출 단일 통로** — 로깅·토큰·비용(LEDGER)·재시도·structured outputs |
| `config.py` | `.env` 단일 출처(`Settings` 데이터클래스) |
| `pipeline.py` | end-to-end 오케스트레이터 + **`collect_seller_feeds`**(1층 피드 전량 → 디스크, LLM 0회) · **`derive_product_registry`/`load_product_registry`**(해시태그 빈도 → 마켓별 제품 후보, LLM 0회) + `ingest_hashtag`(인스타)·`ingest_dcinside`(디시 배치, 안정 키 `dc_post_id` · 워터마크 `_max_thread_no`) + 변경분 요약 갱신(`refresh_stale_summaries`/`_is_stale`, 판정 무료) + UI 데이터접근 캡슐화(`consolidated_for` 제품 / `consolidated_for_market` 마켓 / `order_view_for` 주문 축 / `list_reviews` 커뮤니티 패널 + `REVIEW_SORTS`) + 요약 저장(`generate_summaries` 제품 · `generate_market_summaries` 마켓 · `stored_*`) |

## Common patterns (workflow)
```bash
# 모든 명령은 repo 루트에서, .venv 활성화 후
source .venv/bin/activate
python -m slime_rag.linking      # 셀프테스트 예 (대부분 모듈에 __main__ 셀프테스트 존재)
python -m slime_rag.pipeline     # end-to-end 글루 (pgvector + .env 필요, 포트 55432)
python -m slime_rag.spec_overrides   # 사람 검수 오버레이 현황(무과금·DB 무접촉)
```
- **LLM 추가/교체는 `llm_ops.py` 한 곳만** 수정 — 벤더는 이 파일에만 의존(Anthropic→OpenAI 전환이 파이프라인 무변경으로 끝난 게 증거).
- **새 소스 추가 = `Source` 구현체 추가** → `sources/` 에 파일 하나. 파이프라인 무변경.
- 결정성은 structured outputs(`response_format` json_schema, strict)로 확보, 파싱 실패 1회 재시도.

## Non-obvious (주의 / Gotcha)
- **Important:** 미언급은 `null`, 지어내기 금지. 필드별 근거 스니펫(15자 내외)으로 인용·저작권 회피.
- **Important:** 1층은 '객관 스펙만'이 아니다 — **판매자 본인의 질감 서술**(`official_texture`)은
  공식 정보로 뽑는다. 무시하는 건 **구매자 평가**뿐이다. `slime_type`(TYPE_ENUM 분류)과 별개 칸이며
  합치면 화면의 '질감' 줄이 다시 '폼볼' 한 단어로 돌아간다.
  **Don't:** 이 값을 요약 프롬프트에 넣지 말 것 — 판매자 말은 구조상 항상 긍정이라 인스타 편향이
  한 겹 더 얹힌다. 스펙↔후기 분리는 `official_scent` 뿐 아니라 여기에도 걸린다
  (게이트: `eval/test_consolidated_sections.py` 의 `공식질감토큰`).
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
- **Important:** 마켓 로고는 커밋되는 유일한 수집물이다([ADR-0010](../docs/adr/0010-market-logo-assets.md)).
  ADR-0013 이후로는 '무재배포의 예외'가 아니라 **같은 원칙의 한 사례**다 — 저장은 허용이고 표시는
  링크백을 동반한다. 마켓 본인 프로필 아바타(1개·320px·링크백 필수)만 다운로드한다. **파일 삭제 = 즉시 철회**(모노그램 자동 폴백)라는 성질이 그 결정의
  전제이므로 `logo_asset` 의 파일 존재 확인을 없애지 말 것. 게시물 미디어는 여전히 전면 금지.
- **Note:** `search._dense`/`_sparse` 는 **명명 인덱스**로만 행을 읽는다(`_BASE_COLS`). 예전의
  `r[6]`/`r[:6]` 위치 하드코딩은 컬럼 하나 삽입만으로 **무예외** BM25 파손을 냈다 — 되돌리지 말 것.
- **Note:** 제품 단위 평가 축은 향/질감/소리/지속력 넷 — **`value`(가성비) 축은 제거됨**(2026-08-05,
  [ADR-0008](../docs/adr/0008-drop-value-add-shipping-section.md)). 가격 얘기는 pros/cons 로만 흐른다.
- **Important:** 기준마다 **`scope`** 가 있다([ADR-0015](../docs/adr/0015-market-scope-order-criteria.md)).
  질감·향·소리·지속력은 `product`, 고객 응대·배송은 `market` — **요약을 만드는 함수가 다르다**
  (`build_consolidated` vs `build_order_view`). 주문 축은 **마켓당 한 벌**이고 제품 페이지가
  그걸 빌려 쓴다. 되돌려 제품 축에 합치면, 비교글 하나의 배송 불만이 그 글이 언급한 **모든
  제품의 요약**에 각각 관측된 것처럼 실린다(표시 문제가 아니라 귀속 오류).
  **Don't:** 한 축의 프롬프트에 다른 축 기준을 설명하지 말 것 — 담을 칸이 없으면 모델이 그
  내용을 **남은 칸에 밀어 넣는다**(게이트: `test_axis_prompts_never_mention_the_other_axis`).
- **Important:** 팬아웃 복제분은 `_fold_orders` 가 **조각 식별자**(`source_links.evidence_group_key`)로
  접는다 — 재료와 집계 **양쪽**에. 예전엔 프롬프트가 '제품 수만큼 부풀려 세지 마라'고 모델에게
  **부탁**했는데, 이 저장소의 규칙은 그 반대다(규칙은 프롬프트, 강제는 코드).
  **Don't:** 내용 해시로 접지 말 것 — 서로 다른 주문의 '배송 빨라요'가 한 건이 된다.
  **과소 집계는 과대 집계보다 알아채기 어렵다**(화면에 이상이 안 보인다). 식별자 없는 행
  (ADR-0009 이전 색인분)은 **접지 않고 남긴다** — 해결은 재수집뿐.
- **Important:** 요약 섹션은 **6기준**이다(`CRITERIA` 단일 출처 — 스키마 required·프롬프트·UI 표가
  이 1벌을 공유, [ADR-0011](../docs/adr/0011-six-criteria-summary-and-search-page.md)).
  제품 축 넷은 ADR-0008 그대로고, 주문 축 `shipping_cs` **재료 하나가 `cs`·`shipping` 두 섹션으로
  갈린다** — 갈리는 곳은 요약(표시) 단계뿐이고 **추출 스키마·DB 는 무변경**이다(ADR-0005 유지).
  `sound`/`longevity` 는 예전에도 재료가 프롬프트에 실렸는데 담을 필드가 없어 pros/cons 로만 샜다 —
  섹션 추가로 입력 비용은 그대로고 출력만 늘었다.
- **Important:** 요약 **말투는 `consolidated_view.TONE` 한 곳**에서 정한다 — '~해요'체(화면 카피가
  전부 해요체라 요약만 '~다'체면 톤이 튄다). 프롬프트가 **여섯**(축 2 × 인스타·디시·통합/서포터)에
  `search._ANSWER_SYSTEM` 까지라 한 곳만 고치면 반드시 어긋난다 →
  `eval/test_consolidated_sections.py::test_tone_rule_reaches_every_summary_prompt` 게이트.
  화면에 보이는 말은 '소스'가 아니라 **'출처'**다(디자인 카피 기준).
  1층 `official_texture`(스펙 카드 질감 줄)도 같은 해요체다 — 다만 그건 추출 프롬프트
  (`extract.py`) 소관이라 `TONE` 을 공유하지 않는다(추출층이 표시층에 의존하면 안 된다).
  fixture 16건은 이미 해요체로 적혀 있고, `load_specs` 로 다시 올리면 화면에 반영된다.
- **Important:** 기준 한 칸은 **`{verdict, minority}` 두 칸**이다([ADR-0014](../docs/adr/0014-verdict-minority-and-badge-meta.md)).
  다수 의견과 소수 반론을 **구조로** 가른다 — 문자열 한 칸으로 되돌리면 '유분기 많다 vs 잘 안
  붙는다'처럼 대립 평가가 다시 병렬로 누워 어느 쪽이 다수인지 읽는 사람이 알 수 없게 된다.
- **Important:** '어느 쪽이 다수인가'는 **`criterion_stats` 가 센다**(향 불일치·소스 갭과 같은 이유).
  실측: 저장된 요약이 `향 평가는 인스타에서만 나와요` 라고 썼는데 집계는 **인스타 23 · 아모스갤 1**
  이었다 — LLM 이 소수를 0으로 반올림했다. 이 카운트가 요약 프롬프트의 다수 판정 재료다.
  **Note:** 이 값은 **화면에 안 나간다**. 배지(`인스타만`·`인스타 27 · 아모스갤 6`·`갈림 19:5`)로
  띄워 봤다가 걷어냈다(사용자 결정 2026-08-07) — 다수/소수는 이미 `verdict`/`minority` 두 칸이
  문장으로 말해서 같은 말이 줄마다 두 번 붙었다. 집계는 프롬프트 재료 + `payload` 스냅샷으로 남는다.
  **Don't:** 되살리더라도 임계값 판정('갭이 큼')은 넣지 말 것 — 표본수 미반영·임의 상수라
  `sentiment_gap` 에서 이미 걷어낸 것이다.
- **Don't:** `sentiment_gap` 을 요약 프롬프트에 넣지 말 것. 넣었더니 `다만 출처 갭이 큰 편이라…`
  가 본문으로 샜다. 통합 프롬프트가 받는 건 기준별 pos/neg **카운트**뿐이고, 그것도 문장에 쓰라는
  게 아니라 어느 쪽을 `verdict` 에 둘지 고르라는 재료다.
- **Don't:** 부재 서술('~는 언급이 없어요') 차단을 프롬프트에만 맡기지 말 것 — 전언 구멍과 같은
  실패다. `_violations` 검출 → **1회 재시도** → `_scrub_section` 이 남은 **문장**을 잘라낸다.
  절 단위 수술은 하지 않는다(한국어 종결어미를 다시 지어야 해서 없던 말을 만들 위험).
  **Note:** 스크럽은 과잉 차단도 회귀 대상이다 — '잔여감도 **없다**'는 내용이지 부재 서술이 아니다
  (`eval/test_consolidated_sections.py::test_clean_summary_survives_scrub`).
- **Don't:** 말투를 핑계로 부정 평가를 순화하지 말 것. '별로였다' → '아쉬웠대요'로 눅이면
  소스 편향(1급 기능)이 지워진다. 바꾸는 건 종결어미뿐이고 평가의 세기는 원문 그대로다.
- **Important:** `list_reviews(market=None, product="빠코볼")` — **마켓은 선택**이다. 실측
  (2026-08-06 아모스갤 '빠코볼' 25건): 원문 17조각 중 **10개가 마켓을 언급하지 않았고**, 등장한
  `ㅈㄴ` 6건도 대부분 마켓(슬라임지나)이 아니라 부사 '존나'였다. 개체연결이 정상적으로 보류하면
  `market` 이 NULL 이라 마켓 필수 조회로는 **후기가 있어도 0건**이 된다. 대가로 동명 제품이 섞일 수
  있어 응답에 그 행의 `market`(보류면 None)을 함께 싣는다. 근본 해결은 KB `products` 등록.
- **Warning:** `platform` 에 모르는 값을 주면 **예외**다. 예전엔 조용히 필터가 꺼져 '아모스갤만'
  요청에 인스타가 섞여 나왔다 — 소스 미평균(1급 규칙)이 조용히 깨지는 경로였다. 받는 값은
  `SOURCE_PLATFORM` 의 값(`dcinside`/`instagram`)이지 화면 라벨('아모스갤')이 아니다.
- **Important:** `list_reviews` 는 원문 본문의 **서버 발췌**(`source_links.excerpt`)를 내보낸다
  (ADR-0013 §3, 컬럼은 `e930471`). 저장은 전문(`index.post_columns`), 자르는 건 **여기 한 곳**이다 —
  전문을 반환하고 프런트에서 `line-clamp` 로 접는 건 발췌가 아니다(전문이 이미 브라우저에 도달).
  공개 전환 시 길이를 줄이는 스위치도 같은 자리(ADR-0013 §5). `evidence`(~15자 근거 스니펫)는
  발췌와 **별개 필드**로 함께 나간다.
- **Note:** 정렬(`REVIEW_SORTS`)은 **DB 에 실재하는 컬럼으로만** 만든다. 좋아요/조회/추천 컬럼은
  이제 존재하고 `list_reviews` 도 반환하지만 **정렬 메뉴는 아직 수집순·감성순뿐**이다 — 그 커밋
  이전에 색인된 행은 값이 NULL 이라(**재수집으로는 안 채워진다** — 아래 멱등성 항목) 메뉴를 먼저 켜면 정렬이
  거짓말이 된다. 켜는 건 이 dict 에 한 줄 추가. '최근 수집순'의 '수집'도 같은 정직함이다 —
  `created_at` 은 작성일이 아니라 색인 시각이고, 작성일은 `posted_at` 이 따로 갖는다.
- **Important:** 색인 멱등성은 **DB 제약**이 갖는다 — `UNIQUE(source, post_id, product)` +
  `index_post` 의 `ON CONFLICT DO NOTHING`(2026-08-07, 게이트:
  `eval/test_index_meta.py::test_insert_carries_on_conflict_clause`).
  그전엔 파이프라인 독스트링만 '멱등'이라 주장했고 실제 스킵은 `index_gold` 에만 있어서,
  배치를 두 번 돌린 인스타 80행 중 **28행이 중복**이었다. 단순 중복이 아니라 같은 글이 런마다
  **다른 감성**으로 추출돼(LLM 비결정성) 독립된 후기 두 건처럼 `criterion_stats` 에 들어갔다 —
  다수/소수 판정을 행 수로 세는 구조라 그대로 편향 집계 오염이다.
  **Don't:** `DO UPDATE` 로 바꾸지 말 것 — 재수집마다 이미 내려진 판정을 조용히 덮어쓴다.
  원문 메타 백필이 필요하면 무엇을 덮는지 명시하는 별도 함수로 한다.
  **Note:** `post_id` 가 NULL 이면 Postgres 가 NULL 을 서로 다른 값으로 봐서 제약을 빠져나간다 —
  호출부가 조각 식별자를 항상 넘기는 게 전제다.
  **Note:** `index_post` 의 반환값은 이제 **실제 적재 행 수**(충돌 스킵분 제외)다. `len(texts)` 로
  되돌리면 한 행도 안 들어간 재실행이 'N건 색인'이라 보고한다.
- **Important:** 그 제약이 **디시 댓글에는 안 걸리고 있었다**(2026-08-07 수정). `post_id` 끝자리가
  그 런의 `enumerate` 위치라, 수집 결과가 한 건만 달라져도 같은 댓글이 **다른 키**로 새 행이 됐다.
  이제 `pipeline.dc_post_id` 가 `comment_no`(디시가 주는 댓글 고유 id — 이미 `source_ref` 에 저장돼
  있었다)로 조립하고, `sql/schema.sql` 의 멱등 `UPDATE` 가 기존 19행을 제자리 재작성했다.
  **Don't:** `ordinal` 로 폴백하지 말 것 — 수집기가 이름을 분리해 둔 이유가 그거다. id 가 없으면
  색인하지 않고 `skipped_no_comment_no` 로 **센다**(무음 스킵 금지).
  **Don't:** 마이그레이션 식에서 `#cmt` 대신 `:` 로 자르지 말 것 — `https://` 의 콜론에 먼저 걸려
  전 행이 `https` 로 파괴된다.
- **Important:** 기보유 조각 컷은 **첫 유료 단계 앞**에 둔다(`index.existing_post_ids`).
  해시태그 경로에서 그 자리는 추출이 아니라 **`bias.partition` 앞**이다 — 여기서 먼저 도는 LLM 은
  홍보성 캐스케이드라, 추출 직전에 자르면 verdict 값을 이미 치른 뒤다.
  디시는 **배치 추출**이라 `extract_collected` 앞이고, 절감 보고도 조각 수가 아니라
  `extract.count_thread_batches` 로 잰 **배치 수**다(조각당 1콜이 아니다).
  판매자 경로(`ingest_seller_profiles`)의 판정 기준은 `specs` 가 아니라 **이미 스펙을 만든 게시물
  URL**(`source_permalink`)이고, 캡션 수정 반영을 위해 `skip_seen=False` 강제 재추출을 남겨 둔다.
  **Note:** `_upsert_spec` 이 `source_permalink` 를 COALESCE 로 보존하므로, 제품마다 이미 URL 이
  차 있는 게시물은 자기 URL 을 못 남겨 매번 다시 추출된다 — 비용만 드는 페일오픈이라 그대로 둔다.
  **Note:** 절감 카운터는 `llm_calls_saved`(홍보 게이트 단락분)와 `llm_calls_saved_by_dedup` 으로
  **가른다** — 합치면 어느 절감인지 사후에 못 나눈다.
  **Note:** 추출 결과가 `reviews: []` 인 조각은 행을 안 남겨 매번 다시 추출된다(구조적 구멍, 의도적).
- **Important:** 디시 워터마크(`min_thread_no`)는 **상세 요청 앞**에서 자른다 — 뒤에 두면 HTTP 를
  못 아낀다. 값은 `max(thread_no) - WATERMARK_MARGIN` 이되 **앵커별**이다(`_max_thread_no(conn,
  anchors)`). **Don't:** `source='amos'` 전체 최댓값으로 되돌리지 말 것 — 수집이 제품 앵커 키워드
  검색이라(ADR-0007), 전체 최댓값을 쓰면 **처음 수집하는 제품**의 워터마크가 남의 제품이 올려놓은
  글번호가 되어 그 제품의 과거 글이 통째로 잘린다. 목록이 최신순이라 첫 페이지에서 페이징까지
  끝나고, 카운트엔 '새 글 없음'과 구분되지 않는 0 만 남는다 — 조용한 유실이다.
  앵커 이력이 없으면 워터마크가 None 이라 전량 수집으로 떨어지고, 그 판단은
  `counts["watermark_anchors"]` 로 드러난다. 마진의 근거는 따로다: 옛 글이 다른 키워드로 뒤늦게
  매칭될 수 있고, 마진 안쪽은 조각 단위 컷이 잡아 **HTTP 만 조금 더 쓰고 LLM 은 안 쓴다**.
  필터 뒤의 `if not post_urls: break` 는 '검색 결과 없음'이 아니라 **그 키워드 페이징 종료**다
  (목록이 최신순) — 버그로 보고 고치지 말 것.
  **Note:** 이건 **새 글만** 아낀다. 새 댓글은 옛 글에 달리므로 재방문은 `revisit_threads` 명시
  인자이고, 그 글번호들은 **검색 목록을 거치지 않고 직접 조회**한다(예외 처리만 하면 검색에 안
  잡히는 글엔 영영 못 닿는다).
  **Note:** 관련성 게이트 예산은 **수집 중에** 쓰이므로 기보유 컷이 아껴 주지 못한다 — 증분 런에서
  예산이 이미 본 조각에 소진될 수 있어 `counts["gate_unprocessed"]` 로 노출한다(0 보다 크면 `limit`
  을 올릴 신호).
- **Important:** 기보유 **글**은 그 스레드에 새 조각이 남아 있으면 배치에 **문맥으로** 남기고
  색인만 건너뛴다(`counts["context_posts"]`, 색인 스킵 수와 등식을 `assert` 로 강제).
  배치 추출의 존재 이유 절반이 형제 문맥이다 — 제품명을 생략한 댓글의 귀속(AC13)과
  `extract_collected` 의 market 상속은 글에서 온 값을 권위로 삼는다. 글을 빼면 **증분 런에서만**
  조용히 그 성질을 잃는다. 배치는 어차피 돌아서 추가 호출은 없고 늘어나는 건 입력 토큰뿐이다.
- **Important:** 스레드 판정은 **`extract.thread_key` 한 곳**이다 — 댓글은 `meta["parent_no"]`,
  **글은 URL 의 `no=`**. `_parse_post` 가 싣는 건 nick·ip·조회·댓글수·추천뿐이라 **글의 meta 엔
  스레드 번호가 없다**(`source_links.build_source_ref` 가 같은 이유로 같은 규칙을 쓴다).
  **Don't:** 호출부에서 `meta["thread_no"]` 하나로 통일하지 말 것 — 글 쪽이 늘 `None` 이 되어
  ①설계한 문맥 유지가 **한 번도 안 걸리고** ②그 `None` 이 비교 집합에 섞이면 죽은 스레드의 글까지
  전부 매칭돼 **버릴 글에 유료 호출**이 나간다. 양방향으로 조용히 틀리고, 어느 쪽이 나올지는 그 런의
  무관한 다른 결과에 달린다(2026-08-07 실제 결함 — 테스트 픽스처가 수집기가 안 싣는 키를 넣어
  통과시켰다. 그래서 `eval` 픽스처는 `_candidates_for` 가 싣는 meta 만 담는다).
- **Important:** 수집(유료·1회)과 처리(무료·N회)는 **디스크로 갈린다**(2026-08-07). 그전엔 Apify
  응답이 `RawReview` → LLM → DB 한 패스로 흘러 **어디에도 남지 않았고**, 추출 규칙이 틀렸다는 걸
  나중에 알면 액터를 다시 사야 했다(유령 제품 복구 때 실제로 그랬다). 이제
  `collect_seller_feeds` 가 원문을 `rawstore` 에 쌓고, `ingest_seller_profiles(from_raw=True)` 가
  그걸 읽어 **Apify 0원**으로 재추출한다. 재처리 매핑은 수집 경로와 같은 함수
  (`sources.apify._post_to_seller_review`)를 써야 한다 — 여기서 dict 를 직접 풀면 `meta` 모양이
  갈려 `bias.partition` 이 소스마다 다른 값을 받는다.
  **Don't:** 저장을 호출부로 올리지 말 것(수집기 `_run` 안이 제자리다) — 호출부가 예외로 죽는
  순간 방금 산 응답이 사라진다.
- **Important:** 판매자 경로의 마켓 열거자는 **`_seller_targets` 하나**다. `ingest_seller_profiles`
  와 `collect_seller_feeds` 가 공유한다 — 복제하면 '수집은 14마켓인데 적재는 12마켓' 같은
  어긋남이 조용히 생기고 어느 쪽이 맞는지 사후에 못 가른다.
- **Important:** `derive_product_registry` 가 가르는 건 **빈도**다. 해시태그 규칙만으로는
  `#꼼픽`(개인 태그)과 `#빠코볼`(제품)을 원리적으로 못 가른다 — 둘 다 그냥 고유 태그다.
  피드 전량이 있으면 갈린다: 개인/마켓 태그는 거의 전 게시물에, 제품 태그는 몇 건에만 붙는다.
  12개 창에서는 **계산 자체가 불가능했던** 신호이고, 이게 피드 전량 수집의 나머지 절반이다.
  **Don't:** 고빈도 태그를 자동 배제하지 말 것 — `market_tag_candidates` 로 분리만 하고 승격은
  사람이 한다. 과잉 배제는 진짜 인기 제품을 지우는데 그 손실은 **화면에 안 보인다**(유령 제품과
  반대 방향의, 더 알아채기 어려운 실패다). 표본이 작으면(<8건) 비율을 근거로 쓰지 않는다.
  **Don't:** 결과를 KB `products[]` 에 쓰지 말 것 — 저 칸은 1층 스펙 객체를 담고
  `layer1.iter_specs` 가 그 모양을 읽는다. 이름만 있는 항목은 `_PRODUCTHOOD_FIELDS` 전부 null 이라
  `_specs_from_seller_post` 가 제품성 미달로 버리는 바로 그 모양이 된다.
- **Important:** 그 레지스트리는 제품명 복구의 **2단 타이브레이크**(③′)로 들어간다 —
  `extract.resolve_product_name(known_fallback=...)`, 재료는 `pipeline.load_product_registry()`.
  1층(`specs`)에서 일치가 **0건일 때만** 본다. `specs` 는 캡션이 두꺼운 제품만 담고(제품성 게이트)
  레지스트리는 피드 전량의 해시태그라, 실측 408행 대 약 2,200후보 — 폴백이 더하는 이름이
  **1,998개**다(늪지 +298 · 연찌 +229 · 머머 +194 · 베이퍼 +59 · 지나 +29).
  **Don't:** 두 집합을 **합집합으로 합치지 말 것** — 1층에서 정확히 하나이던 판정이 레지스트리
  후보가 끼어들어 `hold_ambiguous` 로 **퇴화**한다. 있던 판정이 사라지는 방향이라 화면엔
  '제품 없는 후기'로만 보이고 원인이 안 보인다. 2단은 판정을 더하기만 한다(단조).
  **Don't:** 레지스트리를 1층보다 **앞**에 두지 말 것 — 사람이 승격한 목록이 아니라 유도된
  후보라 잡음이 섞인다(실측: 늪지의 `액괴`·`워터글루`·`jigglyslime` — 광역어·재료어인데
  마켓/종류 후보 임계값 아래라 products 에 남았다). 그래서 뒤에 두고 다중 일치는 보류한다.
  수집 경로(`_ingest_instagram_raws`)와 백필(`repair_product_attribution`)이 **한 벌**을 쓴다.
- **Important:** 사람이 채운 1층 스펙은 **오버레이가 이긴다**([ADR-0016](../docs/adr/0016-human-in-the-loop-spec-review.md)).
  마스킹 자리는 `_upsert_spec` **한 곳**이다 — fixture 시드와 판매자 자동추출이 공유하는 단일
  경로라 새 수집 경로가 생겨도 규칙이 안 갈라진다.
  **Don't:** 그 마스킹을 지우지 말 것. 그 함수는 전 칸이 `COALESCE(EXCLUDED.x, specs.x)` 라
  **들어오는 non-null 이 기존 값을 덮는다** — 사람이 채운 제품이 다른 캡션에서 다시 잡히는
  순간 LLM 값이 이긴다. 프로필 액터가 최신 ~12글만 주므로 같은 제품이 여러 글에 걸쳐 다시
  잡히는 건 **정상**이라, 이건 이론적 위험이 아니라 예정된 사고다.
  **Don't:** 읽기 시점(`consolidated_for`·`list_products`·`/api/page`)에 얹지 말 것 — 읽는 곳이
  넷을 넘고 하나만 빠뜨리면 **화면과 요약 프롬프트가 서로 다른 스펙을 본다.**
  **Note:** 오버레이는 DB 가 아니라 `data/spec_overrides.json` 에 산다. `specs` 는 파생
  테이블이라 `setup(reset=True)` 한 번에 손으로 채운 값이 날아간다 — 복원은 `setup` 꼬리의
  `apply_spec_overrides()` 이고, 그게 이 파일이 커밋되는 이유다.
  **Note:** `unknown` 은 **값을 만들지 않는다**(1급 규칙 유지). 큐에서만 빼고 DB 는 NULL 이며,
  나중에 판매자가 캡션에 적어 LLM 이 채우면 그 값이 들어온다(= 마스킹하지 않는다).
  **Note:** 사람이 **명시적으로 비운** 칸만 `_clear_overridden_blanks` 가 실제로 비운다 —
  비움은 NULL 이라 COALESCE 를 못 통과한다. `unknown` 은 여기 안 걸린다.
  **Note:** 고아(오버레이엔 있는데 `specs` 엔 없는 조합)는 `orphans()` 가 **카운트로 드러낸다** —
  `resolve_product_name` 의 개명·병합으로 생기고, 조용히 버리면 사람의 노동이 흔적 없이 사라진다.
- **Note:** `rawstore` 의 병합 순서 정본은 파일명이 아니라 봉투의 **`scraped_at`** 이다.
  개발 중 실제로 깨졌다: 충돌 때만 `-2` 접미사를 붙였더니 `...Z-2.json` 이 `...Z.json` 보다
  앞서 정렬돼(`-` < `.`) **최신 캡처가 옛 캡처에 밀렸다** — 캡션 수정이 조용히 무시되는 경로다.
- **Important:** `refresh_stale_summaries` 의 **판정은 무료, 생성만 유료**다(`dry_run=True` 기본).
  개수는 `consolidated_for(with_summary=False)`·`order_view_for(with_summary=False)` 에서 온다 —
  **SQL 로 재구현하지 말 것**: `n_reviews` 는 행 수지만 `n_orders` 는 팬아웃을 접은 **조각 수**라
  `count(*)` 로 세면 '배송 후기 27건'처럼 부풀려진다. 열거는 `specs` 기준이고, 그게 유령 제품에
  유료 요약을 쓰지 않는 자동 필터다. `ADR_0015_CUTOFF` 는 tz-aware — `timezone.utc` 를 떼면
  `generated_at` 과의 비교가 `TypeError` 로 죽는다.

## Cross-module dependencies
- [`../api/main.py`](../api/main.py) → `pipeline`, `source_links`, `linking` (얇은 직렬화층;
  화면 `../web` 은 HTTP 로만 닿는다 — DB 접근은 이 패키지가 전부 캡슐화)
- `../sql/schema.sql` ← `db.py`/`index.py` 가 specs↔reviews 조인·메타필터 컬럼 사용
- `../eval/` → `bias`, `sources`, `linking` 오프라인 테스트
- 도메인 규칙·결정 근거: [../MEMORY.md](../MEMORY.md), [../docs/adr/](../docs/adr/)
