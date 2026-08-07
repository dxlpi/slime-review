<!--
리뷰어가 사람이든 에이전트든 같은 체크리스트를 쓴다. 항목은 전부 **이 레포에서 실제로 회귀가
난 자리**다 — 일반론은 넣지 않는다(안 읽히는 체크리스트는 게이트가 아니다).
해당 없는 절은 지우지 말고 `N/A + 이유` 로 남긴다. 왜 안 봤는지가 리뷰 기록의 절반이다.
-->

## 무엇을 / 왜

<!-- 무엇이 바뀌었고 어떤 결정이 근거인가. 구조적 결정이면 ADR 을 먼저 쓴다(docs/adr/). -->

## 1급 규칙 (해당하면 반드시 확인)

- [ ] **출처 편향을 평균내지 않았다.** 출처별 + 갭으로 남았는가. 부정 후기가 조용히 빠지지 않았는가
      (`E` 음성은 드롭이 아니라 순위 꼬리, 드롭 사유는 `M` 하나뿐)
- [ ] **미언급은 `null`.** 지어낸 값이 없고, 근거 스니펫이 원문의 부분문자열인가
- [ ] **LLM 호출이 `llm_ops.py` 를 통한다.** 벤더 SDK 를 다른 파일에서 직접 부르지 않았는가
- [ ] **브라우저로 나가는 본문은 서버 발췌다**(ADR-0013). `line-clamp` 로 접은 전문이 아닌가
- [ ] **링크는 참조다.** 식별자가 없으면 링크를 조립하지 않고 텍스트로 두었는가(ADR-0009)
- [ ] 판매자(1층) 문구가 요약 프롬프트로 새지 않았는가 (`official_texture` 는 스펙 카드 전용)

## 컨텍스트 문서

- [ ] 이 변경이 **문서의 상태 서술을 낡게 만들지 않았는가** — 특히 "아직 없다" 류.
      `python .github/scripts/validate_context_claims.py`
- [ ] 경로 무결성 `python .github/scripts/validate_context_paths.py`
- [ ] 새 모듈이면 `CLAUDE.md` 를 함께 넣었는가 / 기존 모듈이면 Key files·Gotcha 를 갱신했는가

## 검증

- [ ] 오프라인 테스트: `python -m eval.test_bias && python -m eval.test_source_links && python -m eval.test_post_columns && python -m eval.test_consolidated_sections`
- [ ] 평가 통과율: `python -m evals.run --min 1.0`
- [ ] 프런트엔드를 건드렸다면 `cd web && npm run build && npm run lint`
- [ ] LLM 을 실제로 호출했다면 **비용**을 적는다(`llm_ops` LEDGER):

<!-- 라이브 호출 결과가 있으면 여기 붙인다. 없으면 "오프라인만" 이라고 적는다. -->

## 리뷰어에게 특히 봐 달라는 곳

<!-- 자신 없는 곳을 지목한다. 지목이 없으면 리뷰는 diff 순서대로 읽히고, 그건 가장 중요한 곳을 마지막에 본다는 뜻이다. -->
