# 슬라임 리뷰 RAG

한국 슬라임 마켓의 **공식 제품 스펙(1층) + 사용자 후기(2층)**를 통합해, 출처를 인용하며 답하는 **근거 기반 RAG 어시스턴트**. 소스 편향(인스타=긍정 쏠림 / 디시=부정 쏠림)을 평균내지 않고 **소스별 + 갭**으로 투명하게 보여준다.

## 구조

```
slime_rag/
  sources.py            수집 레이어 (Source 인터페이스 + DCInside + Instagram 스텁)
  relevance.py          관련성 필터 — 후기 vs 질문/양도/잡담            [Phase 1]
  extract.py            추출 러너 — 비정형 → 정형 JSON                  [Phase 2]
  linking.py            개체연결 — 초성/약칭 → KB + 보류(abstain)       [Phase 3]
  index.py / search.py  임베딩·색인 / 하이브리드 검색·근거 답변          [Phase 4]
  consolidated_view.py  종합뷰 + 소스 편향 집계(소스별·갭·향불일치)
  llm_ops.py            관측성 + LLM 호출 래퍼(로깅·비용·재시도)
  config.py             .env 단일 출처
data/                   KB(마켓 명부 + 초성/약칭)
prompts/                1층/2층 추출 프롬프트 스펙
app/ui.py               Streamlit UI                                   [Phase 6]
```

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # OPENAI_API_KEY 채우기

# Phase 2~3 (LLM 키만 필요)
python -m slime_rag.extract   # 디시 후기 1건 → 2층 추출(제품별 분리)
python -m slime_rag.linking   # 개체연결 셀프테스트(ㅂㅉ→빈짱, 충돌→보류)

# Phase 4 (로컬 pgvector + BGE-M3)
docker compose up -d          # pgvector 기동 + schema.sql 자동 적용
python -m slime_rag.db        # 연결·스키마 스모크 테스트
python -m slime_rag.index     # 골드 후기 임베딩·적재(첫 실행 시 BGE-M3 다운로드)
python -m slime_rag.search    # 하이브리드 검색 + 근거 답변
```

## 스택 — 결정과 근거

| 영역 | 선택 | 근거(요지) |
|---|---|---|
| LLM | **OpenAI** (`llm_ops` 뒤) | 추출=gpt-5.4-mini(싸고 빠름), 판정=gpt-5.4(고난도 추론). 작업별 티어 분리 + 벤더 교체 가능(Anthropic→OpenAI 전환 시 `llm_ops` 한 곳만 수정) |
| 임베딩 | **BGE-M3** | 한국어 친화 + dense/sparse 동시 출력 → 모델 하나로 하이브리드. 로컬 실행(콜당 비용 0) |
| 벡터스토어 | **pgvector** | 1층 스펙 ↔ 2층 후기 **조인** + 메타필터(마켓/종류/속성)를 SQL 로 |
| 한국어 키워드 | **kiwipiepy + BM25** | Postgres FTS 는 한국어 토크나이저가 없음 → 형태소 토큰화 후 BM25, 벡터와 RRF 융합 |
| UI | **Streamlit** | Python 네이티브, 챗+필터+종합뷰를 빠르게 |
| 배포 | **Render**(대안 Fly.io) | 관리형 Postgres(pgvector) + 웹서비스 한 곳 |

> 주의: GPT-5 계열은 추론 모델이라 `temperature` 가 무시/제한될 수 있다 → 추출 JSON 의
> 결정성은 낮은 temperature 가 아니라 **structured outputs(`response_format` json_schema, strict)**로 확보한다.

## 원칙

- **책임 수집**: robots 준수, 요청 딜레이, 페이지 상한, 원문 미재배포(스니펫만).
- **근거 기반 출력**: 미언급은 `null`, 필드별 evidence 스니펫, 명시(작성자) vs 추정(모델) 분리.
- **편향 투명화**: 평균 금지, 소스별 + 갭. 보정보다 라벨링.
- **관측성 내장**: 외부 콜은 전부 `llm_ops` 통과 → 로깅·비용·재시도.

빌드 과정은 [BUILD_LOG.md](BUILD_LOG.md), 전체 설계는 [CLAUDE.md](CLAUDE.md) 참조.
