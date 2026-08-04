# ADR-0007: `collected_for` 타깃 방침 — 플랫폼별 scope(product/market)

- 상태: 승인(정책 판정) / 활성화 보류(AC4 재판정 대기)
- 관련: [ADR-0002 소스 편향 1급](0002-source-bias-first-class.md) ·
  [ADR-0005 후기 vs 제품 단위](0005-review-vs-product-unit.md) ·
  [ADR-0006 M/Q/E 3축](0006-mqe-three-axis-relevance.md)
- 영향: `slime_rag/relevance.py`, `slime_rag/sources/base.py`, `evals/calibrate_relevance.py`,
  `evals/simulate_c_prime.py`, `evals/gold/thread_map.json`, `data/product_aliases.json`
  (`evals/gold/` 아래 `relevance_tau.json` 은 **존재하지 않는다** — 아래 결과 참조)

## 맥락

ADR-0006(M/Q/E) 는 관련성 게이트의 축(axis) 문제를 풀었지만, dcinside 의 `τ_topic` 이 AC4
(precision ≥ 0.90 ∧ recall ≥ 0.80)를 못 맞추는 진짜 원인은 라벨 품질이 아니라 앵커의
**`collected_for` 타깃 단위**가 미결이라는 것이었다: 앵커를 "제품"으로 좁히면 스레드 잡담이
드롭되고, "마켓"으로 넓히면 타깃 외 잡담이 후보에 섞인다. 이 ADR 은 그 방침을 정한다.

## 결정

**Policy C — 플랫폼별 scope**: dcinside 는 **market** 이 정책상 옳다(D1), 인스타는 **product**
유지. `RELEVANCE_CONF[platform]["target_scope"]` 데이터 한 줄로 플랫폼별 앵커 단위를 결정한다.

| 플랫폼 | 판정된 scope (D1) | 실제 ACTIVE scope | 비고 |
|---|---|---|---|
| dcinside | market | **product**(되돌림) | AC4 재보정 실패로 활성화 보류(아래 결과) |
| instagram | product | product | 변경 없음. τ 는 잠정(provisional) — 아래 IG 발산 참조 |

**메커니즘은 완전히 구현돼 있다** — 정책이 "market" 으로 재승인되면 코드 변경 없이 데이터 한
줄(`target_scope`)만 바꾸면 된다:
- `slime_rag/relevance.py` 의 `build_anchor(target, *, domain, scope)` 가 scope × domain
  **2×2 행렬**을 직교로 처리한다: `scope="product"`(기본, 약칭 정규화 포함) / `scope="market"`
  (앵커 = `"{market} 슬라임"`, 제품명은 버림) 을 `domain=True/False`(인스타 name-collision 접미)와
  독립으로 조합한다.
- **dedupe 가드**: `domain=True` 접미는 base 가 이미 `"슬라임"` 으로 끝나면 중복 부착하지
  않는다(`"봄 슬라임 슬라임"` 방지) — market scope 는 이미 `"슬라임"` 로 끝나므로 이 가드가
  실제로 발동하는 경로다.
- **`market=None` → product 폴백**: `scope="market"` 인데 타깃에 마켓이 없거나 빈 문자열이면
  product scope 로 폴백한다. 이건 예외 처리가 아니라 **필수 보존 경로**다 — dcinside 하드네거티브
  `dc-132`/`dc-133`/`dc-134`(`evals/gold/relevance_gold.json`)와 인스타의 키워드 폴백 타깃이
  마켓 없는 target 에 의존하기 때문에, 폴백이 없으면 이 항목들의 앵커가 무너진다.

## 근거 — C′(keep/rank 분리) 시뮬레이션과 기각

Policy C 를 앵커로 쓰되(KEEP 판정) 순위 점수만 제품 단위 코사인으로 따로 매기는 **C′** 안을
`evals/simulate_c_prime.py` 로 오프라인 시뮬레이션했다(스레드 조인 71/71, `evals/gold/thread_map.json`
에 고정, 9 스레드). 후보 집합은 **출고된 게이트를 통과한 것**(topic τ + M 드롭 + e_union 드롭 이후)
으로 만들었다 — 게이트 앞을 시뮬레이션하면 이득이 부풀려지기 때문이다.

측정 결과:
- 예산(budget) 30(= post-gate 후보 31건)에서 market 앵커와 product 앵커가 **바이트 단위로 동일한
  집합**을 뽑는다(target-product 비율 17/17 = 1.000 양쪽 동일).
- 예산 10 에서도 5/5, 비율 1.000 — 동일.
- 예산 1..31 전 구간 스윕: product 앵커가 더 나은 예산이 2개(+1 마진), 더 나쁜 예산이 5개
  (−1 마진)뿐이고, `≥1.5×` **그리고** never-fewer 규칙을 모두 통과하는 건 예산 3·7(노이즈
  구간)뿐이다.
- tie 그룹 15개, 싱글턴 47%, 8개 스레드 중 4개만 순서가 바뀐다.

**C′ 는 기각한다.** 순위 재분리가 만드는 차이가 노이즈 수준(전 구간의 대부분에서 무차이 또는
역전)이라 별도 랭킹 경로를 유지할 근거가 없다. `RawReview.meta["relevance"]["rank_score"]`
필드는 계획대로 **항상 null** 로 유지한다(`base.py` `RelevanceGate.filter`).

## 근거 — 골드 재라벨(market scope 판정 반영)

market scope 판정(D1)을 골드에 반영하며 dcinside `label.keep` 이 **24건** false→true 로 뒤집혔다:
- 9건 — product-scope 라벨 결함(제품명을 직접 거명하는데도 product 앵커 좁음 탓에 잘못 drop 처리).
- 10건 — market scope 로 봐야 맞는 진짜 flip.
- 5건 — market 기준 재평가가 필요한 경계(borderline) 판정.

dcinside 골드는 이제 keep 64 / drop 59. `evals/check_gold_integrity.py` 에 **differing-keep
하드 단언**을 추가해 이 재라벨이 회귀하지 않도록 잠갔다. M/Q/E 라벨은 이 재라벨의 대상이
아니다(건드리지 않음).

## 결과 — AC4 는 어떤 τ 로도 달성 불가능 (결정적 발견)

**이것이 이 ADR 의 핵심 미결 항목이다.** `evals/calibrate_relevance.py` 를 **운용 게이트와
동형**으로 다시 모델링했다 — 예전 리포트는 `rules.is_meta` 단독으로 keep 을 예측했지만, 실제
런타임은 `relevance.mqe_signals` 가 만드는 `keep = (topic ≥ τ) ∧ ¬M ∧ candidate` 다(D2 판정:
"런타임 e_union 드롭은 의도된 동작" — `relevance.py` `mqe_signals`/`_verdict`). 앵커도
scope-aware(`build_anchor(..., scope=conf["target_scope"])`)로 맞췄고, 리포트는 full-gate(주 수치)
와 M-only(연속성용) 를 함께 낸다.

실측(market scope, dcinside holdout):
- **recall 상한이 0.500** 이다(홀드아웃 22개 KEEP 중 11개만 `¬M ∧ candidate`; 전체 항목
  기준으로도 33/64 = 0.516). 이 상한은 **scope 와 무관**하다 — τ(코사인 컷)를 아무리 조정해도
  넘을 수 없다. 원인은 threshold 문제가 아니라 `e_union` 후보 집합 정의와 relevance 골드의 keep
  정의가 애초에 어긋나 있다는 것이다.
- market 앵커("봄 슬라임") 자체의 판별력이 거의 없다: keep 평균 코사인 0.402, drop 평균 0.370
  (차이 0.032). grid 를 0.20 까지 낮춰봐도 이 판별력 문제는 풀리지 않는다(하한이 안 걸린다).
- 선택된 τ(0.375)에서 F1 0.286 — **keep-all 베이스라인 F1 0.698 보다 나쁘다.** 홀드아웃에서
  precision 0.667, recall 0.182(tp=4, fp=2, fn=18), 홀드아웃 negatives=19.

**τ 는 기록하지 않았다.** `evals/gold/` 아래 `relevance_tau.json` 파일은 존재하지 않는다 — 판별력 없는
τ 를 "보정됐다"고 기록하는 것 자체가 D5(추측 금지) 위반이기 때문이다.

## 완화 조치 — dcinside ACTIVE scope 를 product 로 되돌림

AC4 가 시뮬레이션 방침(market)으로는 달성 불가능하므로, dcinside 의 **ACTIVE** `target_scope`
는 `"product"` 로 되돌렸다(`slime_rag/relevance.py` `RELEVANCE_CONF["dcinside"]`, 되돌린 근거를
그 자리 주석에 남겨 둠). 이로써 런타임은 WS1 이전 동작으로 복원됐고, `python -m
eval.test_relevance_gate` 가 그린이다(신규 `e_bucket` 내 불변식 테스트 `test_kax_ac8b` 포함).

**"market" 을 다시 켜려면** 아래 세 갈래 중 사용자 재판정이 선행돼야 한다(이 ADR 은 그중
하나를 고르지 않는다 — 판단은 사용자 몫):

1. D2 를 재판정해 CLAUDE.md 의 명시된 절대 규칙("오직 M 만 드롭")대로 `candidate` 를 keep 정의에서
   빼고 `e_union` 은 순위에만 관여시킨다 — 즉 `keep = (topic ≥ τ) ∧ ¬M` 으로 되돌린다.
2. relevance-only 골드 라벨과의 정합을 포기하고, AC4 의 keep 모델/바닥(floor) 자체를 candidate
   정의에 맞춰 재정의한다.
3. 더 풍부한 market 앵커 설계(현재의 `"{market} 슬라임"` 단일 앵커보다 판별력 있는 표현)를
   만든다.

셋 중 하나로 재판정한 뒤에만 `keep` 재라벨 + 재보정을 다시 수행한다.

**⚠ 잠복 위험(명시적 기록):** 골드의 dcinside `label.keep` 은 위 재라벨로 **market scope 기준
판정**을 담고 있는데, ACTIVE scope 는 product 다. 즉 지금 평가는 product 앵커를 market 기준
keep 에 대고 재며, 이 상태에서 `python evals/calibrate_relevance.py --write` 를 돌리면 파일에
`target_scope: "product"` 가 찍혀 **fail-loud 검사를 깨끗이 통과하는 잘못 보정된 τ** 가
만들어진다. 이를 막기 위해 골드에 `keep_scope_ruling`(플랫폼→keep 판정 기준 scope) 메타데이터를
두고, `--write` 는 판정 scope ≠ ACTIVE scope 인 플랫폼이 있으면 **즉시 거부**한다
(`evals/calibrate_relevance.py`). 재판정 후 `keep` 재라벨 시 이 메타데이터도 함께 갱신한다.

## 근거 — 인스타 eval/runtime 앵커 발산 (기록, 은폐 아님)

런타임 인스타 앵커는 `"<키워드> 슬라임"` 이고 `market=None` 이다 — `slime_rag/pipeline.py` 의
`ingest_hashtag` 가 `src.collect(keywords, limit=limit)` 를 target 없이 호출하고,
`slime_rag/sources/base.py` 의 `resolve_target` 이 keywords[0] 폴백으로 채운다. 반면 인스타
골드는 실제 마켓을 가지고 있어서, 평가는 약칭 정규화가 있는 브랜치(`relevance.py`
`build_anchor` 의 `market and slime` 분기, 예: `"머머 <제품> 슬라임"`)를 타지만 런타임은 이
브랜치에 도달하지 않는다.

τ 파일이 아예 없으므로(위 결과) 두 플랫폼 모두 잠정 기본값 **0.45** 로 동작한다 —
`τ_instagram` 도 provisional 이다. 약칭 브랜치는 **ACTIVE 설정(product scope)에서는 dcinside
런타임이 실제로 도달한다** — `ingest_dcinside("허니푸냥이", market="봄")` 이 product 앵커를
만들 때 `data/product_aliases.json` 의 유일한 엔트리(`봄: 푸냥이 → 허니푸냥이`)가 정확히 이
브랜치에서 발동한다. (판정된 market scope 가 활성화되면 dcinside 에서는 도달 불가가 되고 —
`slime="슬라임"` 경로 — 인스타 런타임은 `market=None` 이라 지금도 도달하지 않는다.) eval
경로와 런타임 양쪽에서 살아 있으므로 **가비지 컬렉션하지 않는다**(memory:
`index-ignored-alias-product` — index.py 가 과거 이 약칭을 무시하던 버그의 재발 방지 교훈).

`data/product_aliases.json` population 상태: 정확히 **1건**(`봄: 푸냥이 → 허니푸냥이`).

## 근거 — centroid ripple

`label.keep` 은 `load_domain_prototypes`(`slime_rag/relevance.py`)의 입력이다 — 골드 재라벨이
Axis-0(domain) centroid 를 이동시켰다. 현재는 **휴면 상태**다: 어떤 플랫폼도
`domain_gate: "centroid"`(문자열)를 쓰지 않는다 — 인스타는 `domain_gate: True`(불리언)이라
`_verdict` 의 `if domain_gate == "centroid":` 분기에 걸리지 않는다. centroid 모드를 활성화하는
어떤 후속 작업이든, **그 전에 프로토타입 재도출이 필수**다.

## 근거 — τ-file scope 바인딩 semantics (fail-loud)

`slime_rag/relevance.py` 의 τ 로더가 `relevance_tau.json` 의 `target_scope`(플랫폼→scope
dict) 도 함께 읽는다:
- `target_scope` 키가 없는 **레거시 파일**은 로드된 각 플랫폼을 `"product"` scope 로 취급하고
  경고를 남긴 뒤 τ 를 그대로 적용한다(보정이 도입되기 전엔 product 가 유일한 scope 였으므로
  안전한 가정이다).
- 파일의 scope 가 `RELEVANCE_CONF[platform]["target_scope"]`(운용 scope)와 **다르면** 그
  플랫폼을 모듈 전역 `TAU_SCOPE_MISMATCH`(platform → 파일 scope)에 기록하고 에러를 로깅하며
  **그 플랫폼의 τ 는 적용하지 않는다**(잠정 기본값 유지) — 다른 scope 로 보정된 τ 를 조용히
  돌리는 게 바로 이 검사가 막으려는 실패 모드다.
- `slime_rag/sources/base.py` 의 `RelevanceGate.__init__` 은 게이트가 **활성화**될 때
  (`self.active` 참 & 해당 플랫폼이 `TAU_SCOPE_MISMATCH` 에 있을 때) `RuntimeError` 를 낸다 —
  플랫폼명·양쪽 scope·필요 조치(재판정 + 재보정)를 메시지에 명시한다. scope 를 되돌리려면
  이 재판정 + 재보정을 거쳐야 한다.

세 가지 시나리오로 검증했다(임시로 `evals/gold/` 아래 `relevance_tau.json` 생성 후 곧바로 삭제, 커밋 안 함):
1. 레거시 형식 `{"tau_topic":{"dcinside":0.5}}` → 경고 로그 + product scope 로 τ=0.5 적용.
2. `{"tau_topic":{"dcinside":0.5},"target_scope":{"dcinside":"market"}}` → 에러 로그 +
   `TAU_SCOPE_MISMATCH={"dcinside":"market"}`(τ 미적용, 0.45 유지) + `RelevanceGate("dcinside",
   ...)` 생성 시 `RuntimeError`.
3. 파일 삭제 후 재-import → `TAU_SCOPE_MISMATCH == {}`, τ 는 잠정 기본값(0.45).

## 그 외 확인

- **AC16**: 인스타의 **결정 관련 수치는 전부 동일**하다 — 모든 grid τ 에서 전/후 tp/fp/fn 이
  동일하고, 선택 τ=0.55 에서는 tp=8/fp=0/fn=4. precision/recall/AC4 판정 불변. 단 `keep_score_mean` 은 0.609→0.612 로
  **실제로 움직였다**: dedupe 가드(위 결정 절)가 `collected_for.slime == "슬라임"` 인 인스타
  골드 5건(in-120…in-124)의 도메인 접미를 억제해 앵커가 `"머머 슬라임 슬라임"` →
  `"머머 슬라임"` 으로 바뀐, **의도되고 유계인** 부수 효과다(항목별 델타 −0.0055…+0.0334,
  keep 12건 평균 +0.0028). 드리프트가 아니며, 계획이 명시한 가드의 정확한 결과다.
- **WS2**(별도 워크스트림): `relevance_meta` JSONB 영속화가 이미 배포됐다 — `target`/
  `target_scope`/`tau`/`rank_score` 필드(`target_scope` 는 conf 값을 그대로 기록). `rank_score`
  는 위 C′ 기각에 따라 항상 null.
- **배치 크기 결정**(별도 항목, 교차 참조만): 스레드 경로 캐시 측정이 예산 하드스탑에 걸려
  `MAX_THREAD_SOURCES` 는 12 로 유지, `Settings` 로 구동한다(근거는 `slime_rag/extract.py` 주석).

## 대안과 기각 사유

- **market scope 를 지금 바로 활성화한다** — 기각. AC4 가 어떤 τ 로도 달성 불가능한 상태에서
  활성화하면 "정밀도/재현율을 보정했다"는 거짓 신호를 내보내는 것과 같다(D5 추측 금지 위반).
- **C′(keep/rank 분리)** — 기각. 위 시뮬레이션에서 전 예산 구간 대부분이 무차이/역전이라
  별도 랭킹 경로를 정당화할 근거가 없다.
- **골드 keep 정의를 지금 바로 candidate(e_union) 기준으로 재정의한다** — 기각(보류). 이건
  D2 를 뒤집는 결정이고 CLAUDE.md 의 명시적 절대 규칙과 맞물려 있어 사용자 재판정 없이
  단독으로 정할 수 없다.

## 후속

- 사용자가 위 "결과" 절의 세 갈래(D2 재판정 / AC4 모델 재정의 / market 앵커 재설계) 중 하나를
  고르면, `keep` 재판정 → 재보정 → `relevance_tau.json` scope 바인딩 순으로 재개한다.
- 인스타: `ingest_hashtag` 가 실제 마켓 타깃을 넘기도록 하는 후속 결정(현재는 follow-up 로만
  기록, 이 ADR 범위 밖).
- `data/product_aliases.json` 확장(현재 1건) — 약칭 커버리지가 늘면 eval/runtime 앵커 발산
  폭도 줄어든다.
- domain centroid 모드를 켜기 전 프로토타입 재도출 필수(위 centroid ripple 절).
