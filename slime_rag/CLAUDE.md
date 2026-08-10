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
| `linking.py` | 개체연결 — KB 표면형/초성 역인덱스, 충돌 시 abstain + **제품→마켓 역인덱스**(`MarketInversion` 2층 · 전용 confidence · 제외 오버레이) |
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
  **이제 코드가 이 문장과 일치한다**([ADR-0017](../docs/adr/0017-meta-only-drop-authority.md),
  2026-08-09). 강제 장치는 `RELEVANCE_CONF` 의 **`drop_axes`** — 드롭 권한을 가진 축 목록이고
  디시는 `("meta",)` 다. 목록 밖 축은 **판정을 계속 계산·기록하되 버리지 않는다**(순위와 사후
  분석용). 기본값 `DROP_AXES_DEFAULT` 는 전 축이라 **명시 안 한 플랫폼은 예전 그대로**다.
  **Don't:** 인스타에 `drop_axes` 를 달지 말 것 — 거긴 `domain` centroid 가 name-collision 방어의
  본체다(게이트: `test_meta_is_the_only_drop_axis_for_dcinside`).
  **Don't:** '드롭을 껐으니 축도 필요 없다'고 `topic` 계산을 지우지 말 것 — 점수가 `_rank_key` 의
  정렬 재료이고, 지우면 예산 절단이 무작위가 된다.
  **Note:** 살아남은 이유는 행에 남는다 — `relevance_meta.below_tau`(τ 미만인데 살아남음) ·
  `.low_e`(E 합집합 음성인데 살아남음). 드롭 카운터와 **합치지 않는다**: 합치면 '걸러냈다'와
  '규칙상 안 거른다'가 한 숫자가 되어 늘어난 행의 출처를 사후에 못 가른다. 되돌리려면
  `drop_axes` 한 줄을 지우고, 이미 적재된 행은 저 두 칸으로 식별한다.
  **Q 는 순위에 관여하지 않는다** — 정렬 키는 `(e_bucket, bias_hold, topic_score)` 뿐이다.
  질문글이 뒤로 밀리는 건 Q 때문이 아니라 그런 글이 대개 E=0 이기 때문이다.
  부정 감성은 E=0 이어도 `bias_hold` 로 후보에 남는다(D6 편향 보존 하드게이트).
- **Don't:** 전언 차단을 프롬프트에만 맡기지 말 것. 같은 입력 4회 호출에 4번 다른 답이 나왔다
  → `extract.drop_hearsay_reviews` 가 `firsthand_evidence` 없는 항목을 코드로 버린다.
  **Note:** 그 게이트는 이제 **네 겹**이다 — 빈 근거 · 지어낸 인용 · 전언 표지 ·
  **구매 예정 표지**(`relevance_rules.is_candidate_span`). 실측: `담았는데 우뗘??` 한 조각이
  사지도 않은 제품 6행을 냈다. **축이 아니다** — 관련성 게이트가 이걸로 조각을 버리면 첨삭
  스레드가 후보에서 빠지는데, 거기 달린 **댓글**엔 남이 쓴 진짜 후기가 있다.
  **Don't:** 그 어휘를 반례 확인 없이 넓히지 말 것. `궁금` 은 저장된 근거 811개 중 유일한
  매칭이 `궁금해서 사본`(긍정 후기)이라 **넣었으면 진짜 후기를 지웠다**. `후보` 단독은 0건인데
  `최애 후보` 처럼 칭찬으로 쓰일 수 있다. 짧은 평점(`잭두콩 썸`)·순위(`1믹스 2허밍`)는
  **보유 후기**라 통과시킨다(스레드 142738 은 `이렇개 만져봤고` 라고 밝힌 평가글이다).
- **Note:** 추출 호출 입력의 **99.4%가 고정 프롬프트**(실측). 비용 레버는 분류 정확도가 아니라
  호출 단위다 → `extract_collected` 는 스레드 배치(`extract_thread`)로 돈다.
- **Important:** 디시 추출 프롬프트는 **제품 어휘**를 머리말 `[제품 후보]` 로 받는다
  (`pipeline.dcinside_product_vocab` → `extract.build_product_vocab` → `vocab_candidates`).
  **왜 linking 으로 안 되나:** linking 은 모델이 **이미 뽑은 것만** 정규화한다. 댓글이
  `ㅇㅇㅈ 아바 좋더라` 면 모델이 먼저 '아바'를 제품으로 **인식**해야 `mentioned_product` 에
  값이 생기고, 그래야 정규화할 대상이 있다 — 인식 실패는 사후 정규화로 못 되살린다
  (사용자 지적 2026-08-09). 실측: 디시 신규 조각의 **44%** 가 초성 토큰을 포함한다.
  **Don't:** 제품 **초성을 생성하지 말 것** — 실측 오탐 9/14(웃음 런·욕설·타제품 초성과 충돌).
  `linking._kb_surface_forms` 가 같은 이유로 이미 뺀다. 약칭은 음절 클리핑이라 생성 불가고,
  재료는 `data/product_aliases.json` 사람 시드뿐이다(→ [../MEMORY.md](../MEMORY.md)).
  **Don't:** 어휘를 통째로 프롬프트에 싣지 말 것 — 1급 규칙('미언급 → null')과 정면으로 닿는다.
  `vocab_candidates` 가 **그 스레드 본문에 등장한 표면형만** 남기므로 목록의 모든 이름은
  텍스트에 근거가 있다(부수 효과로 토큰도 준다 — 마켓당 160여 개 대신 중앙값 2개).
  **Important:** 그래도 강제는 코드다 — `enforce_product_vocab` 이 사후 재검사한다.
  근거 스코프는 **스레드**다(조각 아님): 제품명을 생략한 댓글은 자기 텍스트에 이름이 없는 게
  정상이라, 조각 단위로 좁히면 AC13 이 통째로 죽는다(개발 중 실제로 이 방향으로 잘못 짰다).
  본문에 그대로 있으면 어휘에 없어도 **남긴다** — 1층에 없는 진짜 제품(`빠코폼`)을 지우는
  과잉 보류는 화면에 안 보이는 실패다.
- **Warning:** 스레드 배치는 `THREAD_MAX_TOKENS`(16,384)로 부른다 — 기본 4,096 으로는 **모자란다.**
  GPT-5 계열의 `max_completion_tokens` 는 **추론 토큰까지 포함**이라, 본문이 나오기 전에 예산이
  마르고 JSON 이 문자열 중간에서 잘린다. 파싱 재시도 1회는 같은 지점에서 똑같이 잘려 무의미하다
  (결정성 재시도는 '다른 답'을 위한 게 아니다). 실측(2026-08-09 유료 런): 34앵커 중 **2앵커가
  통째로 죽었다**(`Unterminated string` char 10,803 / 12,383 · 74조각 유실 · 복구 재실행 $0.15).
  ADR-0017 이전엔 게이트가 스레드당 9조각까지만 통과시켜 안 터졌다 — 긴 스레드 청크 문제와 같은 뿌리다.
  **Important:** 그리고 배치 하나의 실패는 **그 배치만** 잃는다(예외 전파 금지). 빈 문서로 자리를
  채워 정렬을 지키고(짧은 리스트는 zip 이 조용히 잘라 뒤쪽 귀속을 민다), 행이 안 남으므로
  **다음 런이 공짜로 재시도**한다 — 원문이 디스크에 있는 값어치가 정확히 이것이다.
- **Important:** `batch_size`(12)를 넘는 스레드는 청크가 갈리는데, **글 본문은 `members[0]` 하나뿐**이라
  둘째 청크부터 글이 빠진다. 그러면 ①프롬프트가 `S0=글 본문` 이라 말하는데 실제 S0 가 댓글이고
  ②제품명을 생략한 댓글의 귀속(AC13)이 그 청크에서만 조용히 죽는다 — 배치의 존재 이유 절반이
  그 문맥이다. 그래서 이어지는 청크마다 글 본문을 **문맥으로만** 앞에 넣고 그 문서는 버린다
  (호출 수 불변, 입력 토큰만 증가).
  ⚠️ 이 분기는 [ADR-0017](../docs/adr/0017-meta-only-drop-authority.md) 전까지 **한 번도 안 돌았다**:
  예전 게이트는 스레드당 최대 9조각만 통과시켰다. M-only 로 바꾸자 같은 저장소에서 10스레드가
  12를 넘고 54조각이 글 본문 없이 판정될 상태가 됐다(최대 스레드 25조각) — 게이트를 느슨하게
  하면 **스레드당 조각 수**가 늘어 배치 경계 버그가 처음으로 깨어난다는 뜻이다.
  게이트: `eval/test_extract_thread.py::test_post_body_reaches_every_chunk_of_a_long_thread`.
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
- **Important:** **디시도 같은 경계를 갖는다**(2026-08-09, kind `dc_thread` ·
  `ingest_dcinside(from_raw=True)`). 여기선 수집이 무과금(직접 HTTP)이라 오래 예외였는데,
  그래서 오히려 경계가 더 필요했다 — 이 경로의 유료 단계가 **추출**이라, 추출 규칙을 고칠 때마다
  HTTP·robots·딜레이를 다시 밟고 나서야 LLM 에 닿았다. 그리고 **갤러리는 변한다**: 지워진 글은
  재수집으로 못 되찾으므로 '공짜니 다시 받으면 된다'가 성립하지 않는다.
  라이브·재처리 공유 지점은 `DCInsideSource._build_candidates` 한 벌이다(인스타의
  `_post_to_seller_review` 와 같은 자리).
  **Note:** `from_raw=True` 면 워터마크가 없다(`min_thread_no=None`) — HTTP 를 안 쓰니 아낄 게
  없다. 대신 `revisit_threads` 가 '다시 받을 글번호'가 아니라 **디스크에서 고를 글번호**로 읽힌다.
  **Note:** 저장 단위가 스레드라 실측 **343KB/스레드**다(HTML 이 대부분). 전량 스윕 전에 디스크 확인.
- **Important:** `ingest_seller_profiles` 는 **원자적이지 않다**(`commit_every=25`, 2026-08-09).
  `from_raw=True` 의 실제 규모가 게시물 1,837건 · `extract_spec` 1,837콜(약 $3.6)이라, 커밋이
  루프 끝 한 곳뿐이면 막바지 실패 하나가 이미 지불한 호출을 통째로 롤백한다 — 위 항목이 Apify
  쪽에서 막은 실패를 LLM 쪽에서 그대로 다시 내는 구조였다(크레딧 소진 429 로 실측).
  원자성을 포기해도 되는 근거는 **재실행이 이어받는다**는 것이다: `specs` 는
  `UNIQUE(market, product)` upsert 라 부분 적재가 중복 행을 안 만들고, 커밋된 게시물은
  `source_permalink` 가 남아 다음 런의 `skip_seen` 컷이 건너뛴다.
  **Don't:** 중단 예외를 그냥 전파시키지 말 것 — `with connect()` 의 `__exit__` 이 롤백해서
  직전 커밋 이후분이 사라진다. 중단 경로에서 한 번 더 커밋하고 **원래 예외를 다시 올린다**
  (정리 실패가 중단 사유를 가리면 안 된다).
  **Don't:** 마켓 경계에서만 커밋하지 말 것 — 실제 분포가 마켓당 수백 건이라(늪지 298 ·
  연찌 229 · 머머 194) 경계 하나가 $0.5 짜리 유실 창이다.
  **Note:** 주기의 단위는 순회 위치가 아니라 **값이 나간 건수**(`paid_posts`)다. 스킵분까지
  세면 기보유가 많은 증분 런에서 빈 커밋만 자주 나가고 유료 구간은 길게 열린 채 남는다.
  **Note:** 중단하면 반환 dict(카운터)는 사라지고 행만 남는다 — 그 런의 지출 정본은 반환값이
  아니라 `llm_ops.LEDGER` 다. 게이트: `eval/test_layer1_collection.py`.
- **Important:** 그 경로의 유료 컷은 **`_seller_posts_to_process` 한 벌**이고, `dry_run` 과 실제
  유료 런이 그걸 **공유한다**. 예전엔 `dry_run` 이 디스크 건수만 세서 '예상 1,913건 → 실제
  1,837콜'로 어긋났다 — 어긋나는 쪽이 **예상치**라, 값을 치르기 전에 확인하려고 만든 숫자가
  정작 값을 예고하지 못했다. `dry_run` 리포트의 키 이름(`paid_posts`·`skipped_seen`·
  `skipped_unknown_handle`)이 유료 런과 같은 것도 같은 이유다(나란히 못 읽으면 대조가 안 된다).
  **Note:** 그래서 `from_raw=True` 의 `dry_run` 은 **DB 를 읽는다**(기보유 URL 조회). 읽기
  전용이고 커밋도 커서도 없다 — 게이트가 `dry_conn.commits == []` 로 잡는다.
  **Note:** `_SPEC_CALL_USD` 는 실측 평균이라 **예상치 전용**이다. 캡션 길이로 흔들리므로
  실제 지출을 이 상수로 역산하지 말 것 — 거기도 `llm_ops.LEDGER` 가 정본이다.
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
- **Important:** `market` 이 빈 행은 **제품명으로 채운다**(`linking.MarketInversion`, 2026-08-10).
  `consolidated_for` 가 (market, product) 둘 다 요구해서, market 이 비면 그 후기는 집계·요약·화면
  어디에도 안 나온다 — 실측 디시 813행 중 365행(45%)이 그랬고, 화면엔 '후기 없음'으로만 보인다.
  **Don't:** `spec` 층과 `registry` 층을 **합집합으로 합치지 말 것** — 1층에서 정확히 하나이던
  판정이 레지스트리 후보 때문에 다중소유(=보류)로 **퇴화**한다. `resolve_product_name` 의 ③′와
  같은 금지이고, 여기선 '키가 없다'와 '값이 None 이다'를 갈라서 강제한다: 1층이 그 이름을
  **알기만 하면**(모호해도) 레지스트리로 내려가지 않는다.
  **Don't:** 발동 조건을 넓히지 말 것. `mentioned_market` 이 **애초에 비어 있던** 경우만이다 —
  원문이 마켓을 말했는데 초성 충돌·미발견으로 보류된 행에서 채우면, 증거를 보고 내린 보류를
  제품명으로 조용히 뒤집는 게 된다(`ㅁㅁ` 의 모호함은 원문의 사실이다).
  **Important:** 전용 `market_confidence`(`INVERSION_CONF_SPEC` 0.80 / `INVERSION_CONF_REGISTRY`
  0.65)가 **롤백의 유일한 열쇠**다. 두 층을 한 값으로 합치면 잡음 층만 골라 되돌릴 수 없고,
  그러면 이 기능을 켤 근거 자체가 없어진다(틀린 마켓은 NULL 보다 나쁘다 — 남의 마켓 후기가
  화면에 붙는 건 곧 1급 기능인 출처 편향의 왜곡이다). 값은 직접 매칭(0.95·0.85)보다 낮고
  `link_abstain_threshold`(0.6)보다는 높다 — 아래로 내리면 '채웠는데 확신도는 보류선 아래'가 된다.
  **Note:** `linking` 은 **DB 무의존**을 유지한다(게이트: `test_linking_stays_db_free`). 1층 쌍은
  `pipeline.market_inversion_index()` 가 주입하고, 인덱스는 **유료 호출 앞에서 런당 한 번**만
  만든다 — 조각마다 만들면 같은 이름이 조각마다 다른 마켓을 받아 귀속이 수집 순서에 의존한다.
  **Note:** 백필은 `backfill_market_from_product`(별도 함수 — 색인은 `ON CONFLICT DO NOTHING`
  멱등이라 재수집으로 안 바뀐다). `render_review` 가 마켓을 검색 텍스트에 **굽기** 때문에
  `evidence`·`tokens`·`embedding` 을 함께 다시 만든다(재임베딩은 로컬 BGE-M3, 무과금).
  원문이 다른 마켓을 가리키면 채우지 않고 `conflict_list` 로 내보낸다 —
  `backfill_review_markets` 와 **같은 충돌 규칙**이어야 한다(둘이 같은 칸을 쓴다).
- **Important:** 제품명에 섞인 **마켓 접두는 떼고 마켓으로 승격한다**(`linking.split_market_prefix`
  → `link`, 2026-08-10). 추출 프롬프트는 '제품명에 마켓 초성을 섞지 마라'고 말만 하고 강제가
  없었다 — 실측 아모스갤 813행에서 맨몸 마켓 표기 21행 + `마켓+제품명` 11행. 그 이름으론
  `specs` 조인도 같은 제품의 다른 조각과의 집계도 영영 안 된다.
  **Don't:** **완성형 음절을 초성으로 환원하지 말 것**(`_market_token` 이 막는다). 허용하면
  `포도`→푸딩(ㅍㄷ) · `배`→봄(ㅂ) · `육쩐`→연찌(ㅇㅉ)로 **멀쩡한 제품명이 마켓 접두로 잘린다.**
  인정하는 건 ①표면형 완전일치 ②**자모만으로 된 토큰**의 KB 초성 일치, 둘뿐이다.
  **Important:** 마켓 근거는 **좁은 것부터** 본다 — ①항목 자신(`reviews[].mentioned_market`)
  ②제품명 접두 ③글/스레드 값(`doc["market"]`). ③을 앞에 두면 스레드 상속이 항목 자신의 표기를
  이겨 `extract_collected` 에서 고친 사고가 개체연결 쪽으로 되살아난다.
  **Important:** 전용 확신도 `PREFIX_CONF_SURFACE`(0.92)·`PREFIX_CONF_CHOSEONG`(0.82)가
  **롤백의 유일한 열쇠**다(`reason` 은 DB 에 안 남는다). `ㅈㄴ` 처럼 마켓 초성과 부사('존나')가
  겹치는 표기가 실재한다. 게이트: `eval/test_market_prefix.py`.
  **Note:** 현재 KB(14마켓)엔 **초성 충돌이 없다** — 위 머리말의 '전체 KB 12그룹'은 낡은 서술이다.
  **Note:** 별칭은 초성으로 환원되지 않는다(`_choseong_forms` 는 `market_word` 만 환원) — 그래서
  `ㅅㅈㄴ`(=`슬지나`=지나)는 스캔에 안 잡힌다. 감사 스크립트의 mismatch 가 과대 보고되는 원인.
- **Important:** 스레드 market 상속은 **채우기 전용**이다(2026-08-10). 자기 마켓을 뽑은 조각은
  그대로 두고, 마켓을 말하지 않은 형제에게만 물려준다. 덮어쓰기 시절 실측 **36행이 자기 본문과
  모순**됐다 — 스레드 200743 은 본문에 마켓이 7개인데 `빈짱` 하나가 19행 전부에 찍혔다.
  **Don't:** 상속 자체를 없애지 말 것 — 마켓을 안 밝힌 댓글은 여전히 글의 값을 받아야 개체연결이 선다.
- **Important:** 종류어·재료어·조각난 이름도 제품이 아니다 — `extract.is_non_product_word`
  (2026-08-10). 실측: `디폼`·`클리어`·`수수깡` 등 12행 · `글루올`·`아마존` 등 8행 ·
  `캔디어쩌구` 류 5행 · 자모뿐인 이름(`ㅅㄱㄷ`) 6행.
  **Don't:** 부분일치로 넓히지 말 것 — `specs` 제품명 1,980개 중 그 단어와 **완전히 같은** 이름은
  0개인데 **품은** 이름은 16개다(`내리꽃디폼`·`베이직우드폼`·`말차초코크런치바`…).
  `is_non_product_label` 이 `나비매듭` 때문에 부분일치를 금지한 것과 같은 자리다.
  **Note:** 라벨 게이트와 **카운터를 가른다** — 판매 형식어와 종류어는 다른 실패다.
- **Important:** 보류(제품명 None) 항목은 **내용이 완전히 같을 때만** 접는다(`_held_fingerprint`).
  이름이 없어 `UNIQUE(source, post_id, product)` 가 못 거르므로(Postgres 는 NULL 을 서로 다른
  값으로 본다) 추출기 말더듬이 그대로 N행이 된다.
  **Don't:** 내용이 다른 보류분을 접지 말 것 — 서로 다른 제품일 수 있고, 합치면 다른 의견이 한
  건으로 사라진다. **내용이 하나도 없는 보류분도 접지 않는다**(구분할 재료가 없다).
  **Don't:** `(source, post_id) WHERE product IS NULL` 부분 유니크 인덱스로 풀지 말 것 —
  한 조각의 **서로 다른** 보류 제품 둘을 충돌시킨다.
- **Important:** 이미 적재된 디시 행은 `pipeline.repair_dc_attribution` 이 같은 규칙으로 제자리
  복구한다(LLM 0회 · `dry_run=True` 기본). 판정 정본은 순수 함수 `pipeline.dc_attribution_target`.
  **Don't:** 그 안에서 **비제품 단어 게이트를 접두 분리보다 앞에 두지 말 것** — `ㅇㅊ`·`ㅁㅁㄴ`
  같은 맨몸 마켓 표기가 '자모뿐인 이름'으로 먼저 걸려 제품만 비워지고 **마켓 신호가 통째로
  버려진다**(실측: 그 순서로 짰다가 마켓 교정 10건 중 8건을 놓쳤다).
  **Note:** 접두 마켓은 저장된 마켓을 **덮는다** — 디시 행의 마켓은 스레드 도장이라 형제 조각에서
  온 값이고, 접두는 항목 자신의 증거다. 빈 칸 채움(`markets_promoted`)과 교정(`markets_corrected`)은
  따로 센다. 게이트: `eval/test_market_backfill.py`.
- **Important:** 백필이 채운 마켓도 확신도를 남긴다 — `BACKFILL_CONF_SPEC`(0.90) /
  `BACKFILL_CONF_CAPTION`(0.70). 예전엔 0.0 을 그대로 둬서 '마켓은 있는데 확신도 0'인 모순 행이
  **273건**(아모스갤 53 · 인스타 220) 남았고, 그중 어느 게 백필분인지 사후에 못 갈랐다.
  유산은 `backfill_market_confidence` 가 근거를 **재도출**해 메운다 — 값을 지어내지 않고,
  재도출이 저장값과 엇갈리면 **건드리지 않는다**(`conflicts`).
  `repair_evidence_headers` 는 마켓을 나중에 채워 `evidence` 가 `[마켓미상 …]` 으로 남은 행을
  다시 렌더한다(27행 → 0). 셋(evidence·tokens·embedding)을 **함께** 만든다.
  **Don't:** `backfill_review_markets` 에 '본문이 다른 마켓을 말했으면 채우지 마라' 가드를
  넣지 말 것 — 넣었다가 되돌렸다(2026-08-10). 비교글은 마켓이 여럿 등장하는 게 정상이고 그중
  하나를 제품명으로만 가리키는 것도 정상이라, 실측 3건이 전부 **맞는 채움**인데 막혔다.
  ⚠️ `market_confidence` 는 `REAL` 이라 동등 비교에 `::real` 캐스트가 필요하다
  (`INVERSION_ROLLBACK_WHERE` 가 정본).
- **Important:** 판매 **형식**을 가리키는 말은 제품명이 아니다 — `extract.is_non_product_label`
  (2026-08-10). `비매품 1번`·`이번차수` 가 제품 행이 됐다(실측 11행 / 8개 이름).
  **Don't:** 부분일치(`'비매' in name`)로 만들지 말 것. 반례가 둘 다 실재한다:
  ① `나비매듭`·`말차수플레` — 진짜 제품명이 라벨어를 품는다 · ② **`연찌비매17`·`푸딩비매품`·
  `웨이즈1월비매` 는 1층 `specs` 에 실재하는 제품**이다(64행 — 연찌·웨이즈는 비매품에 번호를
  붙여 해시태그로 판다). 부분일치였으면 그 제품들이 통째로 사라졌고, 그 손실은 **화면에 안
  보인다**(유령 제품과 반대 방향). 그래서 **맨몸 라벨**은 무조건, **수식된 라벨**은 1층/레지스트리
  증거가 있을 때만 지운다. 증거 미주입이면 **아무것도 안 지운다**(`enforce_product_vocab` ③과 같은 규칙).
  **Don't:** 적용 자리를 해시태그 게이트 **뒤로** 옮기지 말 것 — `repair_product_names` 는 태그가
  없으면 즉시 반환하므로 디시엔 아예 안 돈다(그래서 `비매품 1번` 이 살아남았다). 디시는
  `extract_thread` 가 `enforce_product_vocab` **앞에서** 같은 함수를 부른다: 라벨은 대개 본문에
  그대로 있어 어휘 검사를 통과한다 — 근거는 있는데 제품이 아닌 경우라 두 검사가 서로를 못 대신한다.
  **Note:** 이름만 비우고 **행은 남긴다**. 그 조각의 배송·CS 는 마켓 축(ADR-0015)에 그대로 들어가야
  한다. 백필(`backfill_non_product_labels`)도 접기(fold)를 하지 않는다 — 내용이 다른 두 후기를
  이름이 비었다는 이유로 합치면 진짜 후기가 사라진다.
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
