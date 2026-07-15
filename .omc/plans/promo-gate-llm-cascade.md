# Plan — 홍보성 판정 캐스케이드 (싸구려 게이트 recall + LLM verdict)

**Status:** `implemented` (2026-07-15 — bias/config/pipeline/sources/tests/docs; eval.test_bias 전량 통과)
**Date:** 2026-07-15
**Scope:** 인스타 해시태그 후기의 홍보성(promo) 판정을 2단 캐스케이드로 바꾼다. (1) **싸구려 어휘 게이트**가
높은 recall 로 '홍보 의심' 캡션만 통과시키고(무비용), (2) 통과분만 **LLM verdict** 로 정밀 판정한다.
명백한 실사용 캡션은 게이트에서 즉시 genuine 으로 단락(short-circuit) → LLM 호출을 5~10× 줄인다.

> 근거 대화: 키워드 seed 는 precision 실패(`서포터분들은…내가 만졌을 땐` 인용문 오탐 + `할인/비매/서비스`
> 구매 오탐)로 verdict 에 부적합. 하지만 recall 은 값싸고 강함. → 키워드를 **decider 가 아니라 gate** 로
> 강등하면, gate 는 recall 만 책임지고 precision 은 LLM 이 책임진다. cost 는 이미 낮지만($0.0008/캡션),
> 게이트는 관측성·비용 서사(제논 하드게이트)와 다음 ML 단계(§7)의 라벨 축적을 동시에 얻는다.

---

## 1. Requirements Summary

1. **게이트(값싼, 결정적)** — 캡션에 홍보 인접 어휘(서포터/체험단/협찬/무상/제공/광고/PPL/
   증정/나눔/원고/대가/무료/이벤트/당첨…)가 하나라도 있으면 '홍보 의심' → LLM 로. 하나도 없으면 즉시
   `genuine`(LLM 미호출). 순수 구매어(비매/서비스/할인/세일)는 어휘에서 제외(D7). 게이트는
   **false positive 를 허용**한다(LLM 이 최종 판정하므로).
2. **LLM verdict(정밀)** — 게이트 통과분만 기존 `promo_verdict`(`bias.PROMO_LLM_SYSTEM`)로 판정.
3. **인터페이스 불변** — 캐스케이드는 기존 `PromoDetector = Callable[[str], (bool, marker)]` 계약을 그대로
   반환한다. `partition`/`ingest_hashtag`/CLI 는 detector 를 갈아끼우기만 하고 로직 변화 없음.
4. **관측성** — 게이트 통과율(수집 N / 게이트통과 M / LLM호출 M / genuine 단락 N-M)과 절감된 LLM 호출 수를
   로깅. 무음 상한/무음 단락 금지(§10 관측성).
5. **회귀 없음** — 게이트가 홍보 캡션을 놓치지 않는 한(높은 recall), 최종 라벨은 순수 LLM 방식과 동일.

프로젝트 정합성(CLAUDE.md §10): 관측성 기본 내장, 벤더/모델을 인터페이스 뒤에 두기. 게이트는
`bias.py` 안에 두어 소스/파이프라인 결합도 유지.

---

## 2. 설계 결정 (Decision Drivers)

| # | 결정 | 근거 |
|---|---|---|
| D1 | 게이트는 **키워드/부분문자열(결정적)**, recall 전용 | 무비용·오프라인 테스트 가능·감사가능. precision 은 LLM 이 책임지므로 게이트는 넓게(false positive 허용). |
| D2 | 게이트 어휘는 `PROMO_SEED` **의 상위집합(superset)** | seed 는 'LLM 없을 때의 fallback decider'(정밀 지향, 좁음). 게이트는 recall 지향(넓음) — `이벤트/당첨/체험/제공/증정` 등 **애매어까지** 포함해야 LLM 이 판정 기회를 얻는다. |
| D7 | **순수 구매어(`비매`·`서비스`·`할인`·`세일`)는 게이트에서 제외** | 이들은 단독으로 홍보를 시사하지 않는다(비매품·구매 사은품·할인 = 전부 구매 맥락). 실제 홍보글은 서포터/협찬/무상 제공 등 별도 신호를 동반하므로 게이트에 남는다 → 제외해도 recall 손실 0. 오히려 순수 구매글이 즉시 genuine 으로 단락돼 LLM 호출·비결정 flip 감소. (도메인 근거: 메모 `promo-vs-purchase-domain-rules`) |
| D3 | 캐스케이드는 **합성 래퍼** `make_gated_llm_promo_detector` 로 | 기존 `make_llm_promo_detector`(무게이트)는 보존. 게이트 유무를 주입/교체 가능. 단위 테스트에서 stub LLM 호출 0건을 단락 증거로 검증. |
| D4 | 게이트 미통과 = **즉시 `(False, None)`**(genuine) | LLM 미호출로 비용/지연 0. 홍보성은 '분리 버킷'이라 실사용 오판정의 손실이 낮지만, 그래서 게이트 recall 을 보수적으로 넓게 잡는다(놓치면 그게 유일한 손실 지점). |
| D5 | 게이트 어휘 config `data/promo_gate_terms.json`(선택) | 코드 수정 없이 튜닝(`promo_markers.json` 관례). 없으면 코드 기본 `GATE_LEXICON`. |
| D6 | 게이트 통과분(text+verdict+evidence)을 **선택적 라벨 싱크**로 적재 | §7 ML 단계의 학습셋을 캐스케이드 운영 중 자동 축적(distillation 준비). 이 플랜에선 **훅만**(옵션), ML 학습은 범위 밖. |

---

## 3. Implementation Steps (파일별)

### Step 1 — `slime_rag/bias.py` · 게이트 코어
- `GATE_LEXICON: list[str]` 상수 — `PROMO_SEED` 상위집합. 추가 애매어(recall용):
  `증정`, `나눔`, `무료`, `이벤트`, `당첨`, `원고`, `대가`,
  `체험`, `제공`, `광고`, `sponsored`, `ad`(단어경계 주의), `gifted` 등. (⚠️ 너무 흔한 일반어는
  게이트 통과율을 100%로 만들어 절감 효과를 죽이므로, '홍보 인접'에 한정 — `좋아요/재구매` 류 제외.)
  - ⛔ **순수 구매어 `비매`·`서비스`·`할인`·`세일`은 게이트에서 제외**(D7): 단독으로는 홍보를 시사하지
    않고(비매품·구매 사은품·할인은 전부 구매 맥락), 실제 홍보글은 `서포터`/`협찬`/`무상 제공` 등
    별도 신호를 반드시 동반해 게이트에 남는다. 따라서 이들을 빼도 recall 손실 0이며, 오히려 순수 구매글
    (예: "비매때부터 갈망…산 건데", "추가 할인이라 산 건데")이 즉시 genuine 으로 단락돼 LLM 호출·비결정
    flip(직전 `@angdduslm`)을 줄인다.
- `load_gate_terms(path=None) -> list[str]` — `data/promo_gate_terms.json`(선택) 로더, 없으면 `GATE_LEXICON`
  (`load_promo_markers` 패턴 재사용).
- `promo_gate(text, terms=None) -> bool` — 소문자 비교로 어휘 1개라도 등장하면 True('홍보 의심 → LLM').
  결정적·무비용. 초성단독/빈문자열은 False.
- `make_gated_llm_promo_detector(llm, model=None, terms=None, on_label=None) -> PromoDetector`:
  ```
  def detect(text):
      if not promo_gate(text, terms):
          return (False, None)          # genuine — LLM 미호출
      is_promo, marker = promo_verdict(text, llm, model)   # 실패 시 promo_verdict 내부/래퍼가 처리
      if on_label: on_label(text, is_promo, marker)        # 선택적 라벨 싱크(D6)
      return (is_promo, marker)
  ```
  - LLM 실패 회복력: `make_llm_promo_detector` 의 try/except 폴백을 재사용하도록 내부에서
    `make_llm_promo_detector` 를 감싸거나 동일 try/except 를 복제(게이트 통과분만 호출).
- (관측성) 모듈 레벨 카운터 대신, 통과율 로깅은 호출부(pipeline)가 집계(순수 함수 유지).

### Step 2 — `slime_rag/pipeline.py` · `ingest_hashtag` 게이트 연결 + 관측성
- `promo_detector = bias.make_gated_llm_promo_detector(llm, settings.model_extract)` 로 교체.
- 게이트 통과율 집계: `partition` 전에 `n_suspect = sum(promo_gate(r.text) for r in non_seller_raws)` 를
  세거나, detector 를 감싸 호출 카운트. 로그: `수집 N / 게이트통과 M / genuine단락 N-M / 절감 LLM호출 N-M`.
  (판매자 글은 게이트 이전에 분리되므로 게이트 통계는 non-seller 대상.)
- `counts` 에 `gate_suspect`, `llm_calls_saved` 추가.

### Step 3 — `slime_rag/sources.py` · CLI 게이트 적용(선택)
- CLI 편향 태깅에서 `make_llm_promo_detector` → `make_gated_llm_promo_detector` 로 교체.
  게이트로 단락된 캡션은 LLM 없이 `[실사용]`, 통과분만 LLM 판정. `[bias]` 라인에 게이트 모드 표기.

### Step 4 — `data/promo_gate_terms.json`(선택) · 게이트 어휘 config
- `{"_note": ..., "terms": [...]}` — recall용 상위집합. `_` 접두 키 무시. 없으면 코드 기본.

### Step 5 — `eval/test_bias.py` · 게이트 오프라인 테스트(무비용)
- `test_promo_gate_recall`: 모든 홍보 예시(서포터/체험단/협찬/무상제공) + 애매어(체험/이벤트/증정)
  → `promo_gate` True(= LLM 로 넘어감). '서포터분들은…' 인용문도 True(게이트는 통과, precision 은 LLM).
- `test_promo_gate_excludes_purchase_terms`(D7): 순수 구매어만 있는 글
  (`비매때부터 갈망이었는데 산 건데`, `3개 사면 서비스로 하나 더 줌`, `추가 할인이라 산 건데`,
  `세일해서 샀어요`) → `promo_gate` False(LLM 미대상).
  + `set(PROMO_SEED) ⊆ set(GATE_LEXICON)` 이지만 `비매`/`서비스`/`할인`/`세일` 은 둘 다에 없음을 단언.
- `test_promo_gate_skips_plain`: 순수 실사용("말랑하고 향 좋아요 재구매각") → `promo_gate` False.
- `test_gated_detector_shortcircuits`: stub LLM 주입 → plain 캡션에서 detector 가 `(False,None)` 반환 +
  `stub.calls == []`(LLM 미호출 증거). 홍보의심 캡션에선 `len(stub.calls)==1`.
- `test_gated_detector_precision_preserved`: '서포터분들은…매트했어요'(게이트 True) + genuine 판정 stub
  → 최종 `(False, None)`(인용문 오탐 안 됨 — 게이트가 precision 을 해치지 않음).
- `test_gate_terms_config_load`: `promo_gate_terms.json` 있으면 로드, `서포터`·`체험` 포함,
  `비매`·`서비스`·`할인`·`세일` **미포함**(D7), `_` 무시.

### Step 6 — 문서
- `CLAUDE.md` §5/§7: 홍보성 판정을 '게이트(recall) → LLM(verdict) 캐스케이드'로 갱신.
- `BUILD_LOG.md`: 캐스케이드 항목(게이트 통과율/절감 LLM 호출 실측 포함) + AI생성 vs 사람수정.

### Step 7 — (범위 밖·후속) ML distillation 브리지
- D6 의 `on_label` 싱크로 게이트 통과분(text/verdict/evidence)을 `data/promo_labels.jsonl` 에 append 하면,
  캐스케이드 운영이 곧 학습셋 축적이 된다. **이 플랜은 훅 지점만 남기고**, BGE-M3+로지스틱회귀 distillation
  (라벨 ~수백 개 확보 후, 동일 `PromoDetector` 뒤로 drop-in)은 별도 플랜으로 분리.

---

## 4. Acceptance Criteria (testable)

1. `bias.promo_gate("말랑하고 향 좋아요 재구매각")` == `False`(순수 실사용은 LLM 미대상).
2a. `bias.promo_gate` == `True`(홍보 의심 → LLM): `서포터 게시물입니다`, `무상으로 제공받았습니다`,
   `체험단으로 받은`, `서포터분들은 얄랑하다 했지만`(인용문도 게이트는 통과, precision 은 LLM).
2b. `bias.promo_gate` == `False`(순수 구매어 → 즉시 genuine·LLM 미대상): `비매때부터 갈망이었는데 산 건데`,
   `3개 사면 서비스로 하나 더 줌`, `추가 할인이라 산 건데`, `세일해서 샀어요`.
   (`비매`·`서비스`·`할인`·`세일` 제외의 직접 증거 — D7)
3. `make_gated_llm_promo_detector(stub)("말랑 향 좋아요")` == `(False, None)` **이고** stub LLM 호출 0건.
4. `make_gated_llm_promo_detector(stub_genuine)("서포터분들은 얄랑… 제가 만졌을 땐 매트")` == `(False, None)`
   (게이트 통과 → LLM 이 인용문을 genuine 판정 → precision 보존).
5. `make_gated_llm_promo_detector(stub_promo)("서포터 게시물입니다")` == `(True, <marker>)` 이고 호출 1건.
6. `ingest_hashtag` 로그에 `게이트통과`·`절감 LLM호출` 카운트가 찍히고 `counts` 에 노출.
7. `eval/test_bias.py` 전량 통과(기존 + 신규 게이트 테스트, 오프라인·무비용).
8. 기존 테스트/셀프테스트 회귀 없음. 게이트 미주입 경로(`make_llm_promo_detector`, keyword `detect_promo`)는 불변.

---

## 5. Risks & Mitigations

| 위험 | 완화 |
|---|---|
| **게이트 false negative**(홍보인데 어휘 없음 → LLM 미대상 → genuine 오판) | recall 전용이라 어휘를 보수적으로 넓게(애매어 포함). 이게 유일한 손실 지점이므로 **게이트를 verdict 보다 항상 넓게** 유지(D2). `비매`/`서비스`/`할인`/`세일` 제외(D7)는 FN 을 늘리지 않는다 — 실제 홍보글은 서포터/협찬/무상 제공 등 진짜 신호를 반드시 동반해 게이트에 남기 때문. 운영 시 게이트 단락 캡션 표본 감사. 홍보성은 '분리 버킷'이라 개별 오판의 파급도 제한적(§10). |
| **게이트가 너무 넓어 절감 0** | 통과율을 로깅해 측정(Step2). '좋아요/재구매' 같은 초고빈도 일반어는 어휘에서 제외. 목표는 '홍보 인접'만. |
| **게이트 어휘 ↔ `PROMO_SEED` 드리프트** | 게이트=상위집합 규약(D2) 문서화. 테스트에서 `set(PROMO_SEED) ⊆ set(GATE_LEXICON)` 단언. |
| **`ad`/`광고` 부분매칭 오게이트**('광고 아님'도 게이트 통과) | 게이트는 통과시켜도 무방(LLM 이 '광고 아님'을 genuine 으로 판정). precision 은 LLM 몫이라 게이트 오통과는 비용만 약간 늘 뿐 정확도 손실 없음. |
| **LLM 판정 비결정성**(직전 `@angdduslm` flip) | 이 플랜의 범위 밖(게이트는 호출 수만 줄임). 결정성은 프롬프트 강화(완료분)·judge 모델·§7 ML distillation 로 별도 대응. |

---

## 6. Verification Steps

1. `python -m eval.test_bias` → 전량 통과(게이트 recall/단락/precision 보존/ config 로드 포함).
2. `python -m slime_rag.bias` 셀프테스트 회귀 없음.
3. (라이브·저비용, 사용자 승인 시) `python -m slime_rag.sources 레몬커드쉘도넛` → `[bias] 게이트→LLM` 표기,
   순수 실사용 캡션은 LLM 없이 `[실사용]`, `@wavvyslm`(서포터) 통과→`[홍보성]`. httpx LLM 호출 수가
   7 미만(단락 발생)인지 로그로 확인.
4. (라이브) `python -m slime_rag.pipeline` 또는 `ingest_hashtag` 데모 → 게이트 통과율/절감 LLM호출 로깅,
   종합뷰 `promo_view` 회귀 없음.

---

## 7. Out of Scope (이번 작업 아님)

- ML distillation 분류기 학습·평가(§7 브리지의 훅만 남김. 별도 플랜: 라벨 ~수백 개 확보 후).
- LLM verdict 비결정성 자체 해소(프롬프트 강화는 완료, judge/self-consistency 는 별도).
- 판매자(seller) 라우팅 로직(identity 기반, 게이트와 무관 — 불변).
- Render 배포(마지막 하드게이트, 별도).

---

## 8. Files Touched

| 파일 | 변경 |
|---|---|
| `slime_rag/bias.py` | `GATE_LEXICON`/`load_gate_terms`/`promo_gate`/`make_gated_llm_promo_detector`(+선택 `on_label`) |
| `data/promo_gate_terms.json` | **신규(선택)** — 게이트 recall 어휘 config |
| `slime_rag/pipeline.py` | `ingest_hashtag` 게이트 detector + 통과율/절감 관측성 |
| `slime_rag/sources.py` | CLI 게이트 detector(선택) |
| `eval/test_bias.py` | 게이트 recall/단락/precision 보존/config 테스트 |
| `CLAUDE.md` / `BUILD_LOG.md` | 캐스케이드 문서화 |

---

## 9. 관측성 목표치(측정 대상, 하드코딩 아님)
- `gate_passthrough_rate = 게이트통과 / non-seller 수집` — 데모 태그(#레몬커드쉘도넛 7건)에서 **< 0.6** 이면
  절감 유효(4/7↑ 단락). 실측값을 BUILD_LOG 에 기록(목표가 아니라 관측).
- `llm_calls_saved = non-seller 수집 - 게이트통과` — 로그·`counts` 노출.
