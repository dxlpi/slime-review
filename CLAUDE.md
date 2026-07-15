# CLAUDE.md — 슬라임 리뷰 RAG (compass)

한국 슬라임 마켓의 **공식 스펙(1층·정형) + 사용자 후기(2층·비정형)** 를 통합해 출처를 인용하며
답하는 근거 기반 RAG. 제논(GenON) AI Product Engineer 지원 포트폴리오 — GenOS(AI Search +
에이전트 + LLM Ops)의 축소판. **소스 편향(인스타 긍정 / 디시 부정)은 보정 대상이 아니라 1급 기능** —
평균내지 말고 소스별 + 갭으로 투명하게.

> 이 파일은 나침반이다. 상세는 아래 문서로 분산되어 있으니 필요한 것만 따라가라.

## 어디를 볼까 (map)
- **전체 흐름·의존성**: [ARCHITECTURE.md](ARCHITECTURE.md) (파이프라인 + mermaid + ripple 표)
- **도메인 규칙·암묵지**: [MEMORY.md](MEMORY.md) (어휘·홍보성·1층 규칙·개체연결·KB 구조)
- **구조적 결정 근거**: [docs/adr/](docs/adr/) (임베딩·소스편향·IG fixture·홍보 캐스케이드·후기단위)
- **모듈별 상세**: [slime_rag](slime_rag/CLAUDE.md) · [app](app/CLAUDE.md) · [sql](sql/CLAUDE.md) ·
  [eval](eval/CLAUDE.md)(단위테스트) · [evals](evals/CLAUDE.md)(pass-rate)
- **빌드 기록·생산성 근거**: [BUILD_LOG.md](BUILD_LOG.md) · **스택 근거**: [README.md](README.md)

## 평가 하드 게이트 (반드시 충족)
1. 배포된 데모 + 리포지토리 + 기술 문서
2. **AI 코딩 도구 생산성 근거**([BUILD_LOG.md](BUILD_LOG.md): 핵심 프롬프트 / AI생성 vs 사람수정 / 시간)
3. **관측성**(로깅·메트릭·비용·장애 추적 — 전 LLM 콜은 `slime_rag/llm_ops.py` 한 곳)

## 현재 상태 & 남은 일
- Phase 0~6 **end-to-end 라이브 검증 완료**. **남은 하드게이트 = 배포(Render)** 뿐.
- 1층은 IG App Review 차단으로 fixture(`data/layer1_fixture.json`, 3마켓 6제품) — [ADR-0003](docs/adr/0003-ig-businessdiscovery-fixture.md).
- 남은 구현: `slime_rag/relevance.py` 관련성 기준(스텁) · 개체연결 정답셋 확장([evals/gold/](evals/gold/)) ·
  제품 약칭 사전(`data/product_aliases.json`) · 유해 필터 기준.

## 자주 쓰는 명령
```bash
source .venv/bin/activate                 # 항상 repo 루트에서 (DB 포트 55432)
docker compose up -d                      # pgvector + schema 초기화
python -m slime_rag.pipeline              # end-to-end 글루
streamlit run app/ui.py                   # UI
python -m eval.test_bias && python -m eval.test_apify_source   # 오프라인 테스트
python -m evals.run --min 1.0             # 평가 pass-rate 게이트
python .github/scripts/validate_context_paths.py               # 컨텍스트 경로 무결성
```

## 절대 규칙 (어기면 스킬이 아님)
- **미언급은 null, 지어내기 금지.** 필드별 근거 스니펫(15자 내외)으로 인용·저작권 회피.
- **소스 편향 라벨링, 평균 금지.** 향 불일치·소스 갭은 LLM 아니라 조인/집계(`consolidated_view.py`)에서.
- **LLM 벤더는 `llm_ops.py` 한 곳에만** 의존. 새 소스/모델은 인터페이스 뒤에.
- **책임 수집**: robots·딜레이·페이지 상한·원문 미재배포(스니펫만).
- 결정성은 structured outputs(strict), 파싱 실패 1회 재시도. ⚠️ GPT-5 계열은 `temperature` 미전송.
