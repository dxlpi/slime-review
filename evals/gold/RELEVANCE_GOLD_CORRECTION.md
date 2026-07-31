# 관련성 골드셋 라벨 교정 가이드 (US-006 / 계획 §8 블로커 #1)

`relevance_gold.json` 은 **자동 초안**입니다(`label.draft: true`). τ 보정(AC4)이 유효하려면
아래 기준으로 **틀린 라벨만** 교정해 주세요(D5: 실텍스트 기반, 추측 금지).

## 왜 필요한가 (현재 상태)
자동 초안 라벨로 보정하면:
- **instagram**: τ=0.55, precision 1.0 / recall 0.75 (홀드아웃) — AC4(recall≥0.80) 근접, 라벨 소폭 교정이면 통과권.
- **dcinside**: precision≥0.90 을 맞추면 recall 이 붕괴(≈0). 포럼 슬랭(짧고 초성 많음)이 앵커에 약하게 임베딩되고,
  초안 라벨이 노이즈라 온토픽 후기와 뉴스/잡담이 점수로 안 갈림. → **라벨/타깃 교정이 관건.**

## 교정 포인트 (각 item)
1. **`label.keep`** — 이 텍스트가 `collected_for` 타깃(마켓+슬라임)에 **관한 실제 후기/온토픽 질문**인가?
   - KEEP: 타깃 제품(또는 그 마켓)의 후기·구매 질문·촉감/향/질감 언급.
   - DROP: 뉴스 블리드(`… 1 / 20 이전 다음`), 갤 운영/메타 잡담, 양도/거래, **다른 제품** 얘기(타깃 무관).
2. **`collected_for`** — 타깃 정의. 지금 `푸냥이` 검색 스레드 다수가 실제론 **봄 마켓의 다른 제품**(그래놀라·카피바라 등)
   얘기라 `{market:봄, slime:허니푸냥이}` 로는 오프타깃입니다. 두 방침 중 택1로 통일:
   - (A) **제품 단위**: 타깃 제품을 실제로 말하는 글만 KEEP, 다른 제품 글은 DROP(엄격, precision↑).
   - (B) **마켓 단위**: `slime` 을 일반 `"슬라임"` 으로 두고 마켓 온토픽이면 KEEP(느슨, recall↑). ← 포럼엔 이게 현실적.
3. **`label.kind`**(디시만) — `review`/`question`/`resale`/`chitchat` 4종. 뉴스·메타=chitchat, 양도=resale.
   인스타는 kind 미사용(비워도 됨).
4. **인스타 name-collision 네거티브** — 지금 골드에 **없음**(실 IG 비슬라임 데이터 부재). 있으면 AC3b 실서비스 보정에 중요:
   슬라임 해시태그로 딸려온 **비슬라임 글**(예: `#사과몽땅` 음식글)을 `keep:false`로 추가해 주세요.

## 교정 후 실행
```bash
source .venv/bin/activate
python evals/calibrate_relevance.py --report     # 소스별 τ·precision·recall 확인
python evals/calibrate_relevance.py --write      # AC4 충족 시 τ 를 relevance_tau.json 에 기입
python -m eval.test_relevance_gate               # 게이트 회귀 재확인
```
`--write` 하면 `relevance.py` 가 import 때 `RELEVANCE_CONF` 의 τ 를 보정값으로 반영합니다(하드코딩 근거=이 리포트).

## 참고
- 원시 시드: `data/dcinside_sample_raw.json`(gitignore, 스니펫 200자). 생성: `evals/seed_dcinside_relevance.py`(라이브 1회).
- 초안 생성: `evals/bootstrap_relevance_gold.py` — ⚠️ **교정 후 재실행 금지**(교정본 덮어씀).
- 목표(AC4): 홀드아웃 KEEP **precision ≥ 0.90, recall ≥ 0.80**(소스별).
