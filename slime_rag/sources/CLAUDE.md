# slime_rag/sources/ — 수집 레이어 (플러그인 패키지)

## Purpose (이 모듈이 소유하는 것)
소스별 수집기. `Source` 인터페이스 하나로 디시/인스타/Apify 를 동일하게 다루고, 각 구현체는
`RawReview` 만 내보낸다. 추출·연결·집계는 하류가 담당. 과거 단일 `sources.py`(791줄)를
소스별로 분할한 것 — **import 경로는 불변**(`from slime_rag.sources import X` 그대로).

## Key files
| 파일 | 역할 |
|---|---|
| `base.py` | `RawReview`, `Source` ABC, `Throttle`, `robots_allowed`, `get`, 노이즈/유해 필터 |
| `dcinside.py` | `DCInsideSource` — 아모스갤 본문+댓글(AJAX), 2층 백본 · **스레드 원문 디스크 저장**(`dc_thread`) + `collect_from_raw`(HTTP 0회 재처리) |
| `instagram.py` | `InstagramSource` — 1층 fixture / Graph API 스텁 |
| `apify.py` | `ApifyHashtagSource`(2층 해시태그) · `ApifyPostUrlSource`(2층 URL 직접) · `InstagramProfileSource`(1층 판매자 최신 ~12) · `ApifyProfileFeedSource`(1층 판매자 피드 전량 · 원문 디스크 저장) |
| `orchestration.py` | `expand_queries`, `collect_all` |
| `__init__.py` | 공개 API 재수출(`__all__`) — 외부는 항상 여기서 import |
| `__main__.py` | `python -m slime_rag.sources [해시태그...]` CLI 데모 |

## Common patterns (workflow)
```bash
source .venv/bin/activate
python -m slime_rag.sources                    # 기본 데모(디시 + #슬라임후기 스모크)
python -m slime_rag.sources 머머슬라임 레몬커드쉘도넛   # 애드혹 해시태그 검색(APIFY_TOKEN 필요)
python -m eval.test_apify_source               # 오프라인 매핑 검증
```
- **새 소스 추가 = 파일 하나** 추가 → `base.Source` 구현 → `__init__.py` 의 재수출/`__all__` 에 등록. 하류 무변경.

## Non-obvious (주의 / Gotcha)
- **Important:** 서브모듈은 패키지 한 단계 아래라 상위 모듈 접근은 `from ..config`/`..bias`/`..linking`/`..layer1`(더블닷). base 내부는 `from .base`.
- **Note:** `_run`(apify) 이 유일한 네트워크 경계 — 오프라인 테스트는 여기 샘플 주입으로 무비용 검증.
- **Important:** 1층 판매자 수집기가 **둘**이고 창 크기가 다르다. `InstagramProfileSource`
  (profile-scraper)는 최신 **~12개**가 상한이다 — 액터에 `resultsLimit` 이 아예 없다.
  `ApifyProfileFeedSource`(instagram-scraper, `directUrls`=프로필 URL)는 N개까지 내려간다.
  마켓의 **제품 목록**을 만들려면 후자여야 한다: 12개 창은 최신순이라 들어오는 게 신제품인데
  후기 코퍼스는 옛 제품 얘기를 한다(실측 — 1층 커버리지 10→12 마켓인데 어휘 갭 76은 그대로).
- **Important:** `ApifyProfileFeedSource` 는 **핸들 하나당 액터 호출 하나**다. 몰아 넣으면
  9번째에서 실패했을 때 1~8번의 **유료 결과까지** 날아간다. 그리고 저장은 `_run` **안에서**
  응답 직후에 한다(`rawstore.save_run`) — 호출부에 두면 호출부가 죽는 순간 방금 산 원문이
  사라진다. 테스트가 `_run` 을 통째로 주입하므로 오프라인 검증은 디스크를 안 건드린다.
- **Important:** `ApifyHashtagSource` 도 이제 **가공 전에** 원문을 남긴다(`_save_raw`,
  kind `ig_hashtag`). 오래 이 경로만 예외였고, 그래서 매핑 직후 첫 LLM(홍보성 캐스케이드)이
  죽으면 방금 산 결과가 통째로 사라졌다(실측: OpenAI 크레딧 소진 429). 재처리는
  `pipeline.ingest_hashtag(from_raw=True)` — **Apify $0**, 네트워크 경계만 갈아 끼우므로
  매핑·중복접기·관련성 게이트는 라이브와 같은 코드가 돈다.
  **Don't:** 런 하나를 봉투 하나로 뭉치지 말 것 — 키는 **요청 태그별**이다. 0건이 태그 단위로
  보여야 `ingest_post_urls` 로 보낼 대상을 고르고(`#깡수박화채` 실측), 워터마크도 앵커별이라야
  한다(디시 워터마크를 갤러리 전체 최댓값으로 두면 새 제품의 과거가 잘리는 것과 같은 실패).
  요청 태그에 안 걸린 아이템은 `_unmatched` 키로 남긴다 — 무음 드롭 금지.
  **Note:** 봉투의 `usage_total_usd` 는 런 실사용액을 **건수 비례로 나눈 값**이다. 액터가
  PAY_PER_EVENT 단일 이벤트(`result`)라 나눗셈이 근사가 아니라 정확하다. 런 총액을 봉투마다
  적으면 합계가 태그 수만큼 부푼다(게이트: `test_hashtag_raw_usage_is_split_not_duplicated`).
- **Important:** 디시도 이제 **가공 전에** 원문을 남긴다(2026-08-09, kind `dc_thread`).
  오래 인스타만 저장소를 가졌는데, 이유가 '디시는 액터가 아니라 직접 HTTP 라 당장 나가는 돈이
  없다'였다. 그 논리가 틀린 지점이 셋이다: ① 이 경로의 유료 단계는 수집이 아니라 **추출**이라
  추출 규칙을 고치면 HTTP 부터 다시 밟았다(인스타가 `from_raw` 로 건너뛰는 그 단계) ② **갤러리는
  변한다** — 지워진 글은 재수집으로 못 되찾는다 ③ 재수집도 robots·딜레이·페이지 상한을 다시 쓴다.
  키는 **글번호**다(인스타는 요청 태그) — 값이 나가는 단위도, 사이트에서 사라지는 단위도
  스레드이기 때문이다. 요청 키워드는 봉투의 `requested.keyword` 로 남고 재처리 선택이 그걸 읽는다.
  대신 '이 키워드는 0건'은 이 저장소가 아니라 수집 로그·게이트 카운터가 갖는다(목록 단계의 사실).
  **Don't:** 저장을 키워드 루프 끝으로 미루지 말 것 — 스레드 단위로 값이 나가므로 중간에 죽으면
  이미 받은 스레드가 통째로 날아간다(`ApifyProfileFeedSource` 가 핸들 단위로 저장하는 것과 같은 규칙).
  **Note:** 봉투의 `usage_total_usd` 는 **None** 이다. `0.0` 으로 적으면 '무과금'이 아니라
  '측정해서 0'처럼 읽힌다.
  **Note:** 실측 **스레드당 약 343KB**(HTML 이 대부분) — 56스레드 ≈ 19MB, 1,000스레드 ≈ 340MB.
  gitignore 대상이라 레포엔 안 들어가지만 전량 스윕 전에 디스크를 볼 것.
- **Important:** 그래서 순서가 **받기 → 저장 → 만들기**다(`_candidates_for`). 파싱이 죽어도
  원문은 남아야 한다 — 이 갤의 셀렉터는 아직 `[ADJUST]` 표시가 붙어 있고(실측: `.title_subject`
  가 실제 글에서 안 잡혀 `raw_title` 이 None), 셀렉터 하나 깨진 날 그 런의 HTTP 를 통째로 버리는
  게 정확히 이 저장소가 막는 손실이다. 게이트: `eval/test_dcinside_rawstore.py`.
- **Important:** 라이브와 재처리는 **`_build_candidates` 한 벌**을 공유한다(네트워크를 모르는
  순수 함수). 재처리 쪽에서 저장 dict 를 따로 풀면 `meta` 모양이 갈려 `extract.thread_key`
  (스레드 문맥)·`source_links.build_source_ref`(원문 링크)·`index.post_columns`(작성자)가
  소스마다 다른 값을 받는다 — 인스타 재처리가 `_post_to_seller_review` 를 공유하는 것과 같은 규칙.
  **Don't:** 댓글을 `_parse_comments` 통과분만 저장하지 말 것 — 디시콘/빈 댓글 제외는 **처리
  규칙**이라, 그걸 저장 단계에 섞으면 규칙이 바뀔 때 원문에서 다시 못 뽑는다. 텍스트 0건인
  댓글 페이지도 남기고 거기서 페이징을 끊는다('안 받았다'와 '받았는데 비었다'는 다른 사실).
- **Warning:** 태그 이름이 **일상어면 무관 게시물을 산다.** 실측(2026-08-09 프로브):
  `#첫눈오는밤` 91건 중 슬라임 어휘가 있는 건 **28건(31%)** 이고 나머지는 '강남역 첫눈' 류다.
  `#빠코볼` 은 95건 중 92건(97%). 관련성 게이트가 처리 단계에서 걸러 주지만 **스크랩 값은
  이미 나갔다** — 제품명이 일상어인 태그는 창을 넓힐 때 비용 대비 수확이 나쁘다.
- **Don't:** 판매자 게시물에 `review_class` 를 달지 말 것. 2층 매퍼(`_item_to_review`)와
  1층 매퍼(`_post_to_seller_review`)를 **합치지 않는 이유가 그것**이다 — 저쪽은 홍보성 라벨을
  달아야 하고 이쪽은 달면 안 된다. 붙이는 순간 마켓 본인 글이 '홍보성 후기' 버킷으로 새고
  1층 스펙 경로가 끊긴다(게이트: `eval/test_apify_source.py::test_feed_never_labels_review_class`).
- **Note:** `ownerUsername` 이 비면 `inputUrl` 에서 핸들을 되찾는다(`_owner_from_input_url`).
  소유자를 잃으면 `bias.partition` 이 판매자로 라우팅하지 못해 1층 글이 2층으로 샌다.
  `/p/<code>/` 를 핸들 `p` 로 읽지 않게 게시물 경로는 명시적으로 뺀다.
- **Don't:** 공개 심볼을 서브모듈에서 직접 import 하지 말 것 — 항상 `slime_rag.sources` 패키지 표면에서.
- **Warning:** 토큰/패키지 없으면 소스는 예외 없이 `[]` 반환(회복력) — `collect_all` 이 스킵 로깅.

## Cross-module dependencies
- 상위 [`../CLAUDE.md`](../CLAUDE.md) 의 코어 패키지. 소비처: `extract`, `bias`, `relevance`, `pipeline`
- 소싱 결정 근거: [ADR-0003](../../docs/adr/0003-ig-businessdiscovery-fixture.md) · [ADR-0004](../../docs/adr/0004-promo-gate-llm-cascade.md)
