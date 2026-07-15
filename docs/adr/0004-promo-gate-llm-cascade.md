# ADR-0004 — 홍보성 판정: 게이트 → LLM 캐스케이드

**상태:** Accepted

## 맥락
IG 후기에는 협찬·서포터 등 홍보성 글이 섞인다. 전량 LLM 판정은 비싸고, 순수 키워드 판정은 오탐이 많다.

## 결정
2단 캐스케이드:
1. **게이트**(`promo_gate`, 결정적 어휘 `GATE_LEXICON`) — recall 전용, false positive 허용. '홍보 의심'만 통과.
2. 통과분만 **LLM verdict**(`promo_verdict`) 로 정밀 판정. 명백한 실사용은 즉시 genuine(LLM 미호출).

우선순위: **판매자 > 홍보성**. 판매자(KB 핸들=ownerUsername)는 후기가 아니라 1층 스펙으로 라우팅.

## 근거
- **Why:** 값싼 게이트가 명백한 실사용을 단락시켜 LLM 호출 5~10× 절감.
- **Why 순수 구매어 제외:** 할인·비매·서비스·세일은 단독으론 구매 맥락 → 게이트에서 뺀다(recall 손실 0).
  서포터·체험단·협찬·무상제공·PPL 만 홍보성. 상세 규칙 [../../MEMORY.md](../../MEMORY.md).

## 영향
- `slime_rag/bias.py`: `promo_gate` → `promo_verdict`, `make_gated_llm_promo_detector`(인터페이스 불변).
- 홍보성은 `review_class='promo'` 로 분리 집계 — 종합뷰 `promo_view`([ADR-0002](0002-source-bias-first-class.md)).
- 게이트 통과율/절감 호출을 `counts`(`gate_suspect`/`llm_calls_saved`)로 노출.
