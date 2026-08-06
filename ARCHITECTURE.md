# ARCHITECTURE — 슬라임 리뷰 RAG

한국 슬라임 마켓의 **공식 스펙(1층, 정형)** + **사용자 후기(2층, 비정형)** 를 통합해
출처를 인용하며 답하는 근거 기반 RAG. 소스 편향(인스타=긍정 / 디시=부정)은 보정 대상이
아니라 **1급 기능** — 평균내지 않고 소스별 + 갭으로 투명하게 노출한다.

모듈별 상세는 각 `CLAUDE.md`: [slime_rag](slime_rag/CLAUDE.md) · [app](app/CLAUDE.md) ·
[eval](eval/CLAUDE.md) · [evals](evals/CLAUDE.md) · [sql](sql/CLAUDE.md).
도메인 결정 근거는 [MEMORY.md](MEMORY.md) · [docs/adr/](docs/adr/).

## 데이터 흐름 (파이프라인)

```mermaid
flowchart LR
  subgraph 수집["수집 (sources/)"]
    DC[DCInsideSource<br/>디시 아모스갤·부정편향]
    AP[ApifyHashtagSource<br/>인스타 해시태그·긍정편향]
    IG[InstagramSource<br/>1층 스펙·fixture]
  end
  DC --> REL
  AP --> REL
  REL[relevance.py<br/>후기 vs 잡담·스텁] --> BIAS
  BIAS[bias.partition<br/>판매자 / 홍보성 / 실사용] -->|판매자| SPEC
  BIAS -->|실사용·홍보성| EX2
  IG -->|fixture| L1
  L1[layer1.py<br/>seed_kb_products] --> SPEC
  SPEC[extract.extract_spec<br/>1층 스펙] --> DBs[(specs)]
  EX2[extract.py<br/>2층 후기] --> LINK
  LINK[linking.py<br/>KB 개체연결·abstain] --> IDX
  IDX[index.py<br/>BGE-M3 임베딩] --> DBr[(reviews)]
  DBs -. spec_id 조인 .-> DBr
  DBr --> SEARCH[search.py<br/>하이브리드 RRF + 메타필터]
  DBs --> CONS[consolidated_view.py<br/>소스별 정서·갭·향불일치]
  DBr --> CONS
  SEARCH --> ANS[근거 답변]
  CONS --> UI
  ANS --> UI[app/ui.py<br/>Streamlit]
```

## 모듈 의존성 (누가 누구를 import 하나)

```mermaid
flowchart TD
  ui[app/ui.py] --> pipeline
  ui --> search
  pipeline --> sources
  pipeline --> bias
  pipeline --> extract
  pipeline --> index
  pipeline --> consolidated_view
  extract --> sources
  extract --> llm_ops
  bias --> sources
  bias --> llm_ops
  relevance --> sources
  index --> db
  search --> db
  index --> config
  llm_ops --> config
  linking --> config
  evals[evals/run.py] --> extract
  evals --> linking
  eval_tests[eval/*.py] --> bias
  eval_tests --> sources
```

## 아키텍처 원칙 (경계와 그 이유)
- **LLM 벤더 격리**: 모든 LLM 호출은 `slime_rag/llm_ops.py` 한 곳. 벤더 교체가 파이프라인
  무변경으로 끝난다(Anthropic→OpenAI 전환이 증거). 로깅·토큰·비용·재시도도 여기 집중.
- **소스 플러그인**: 수집은 `Source` 인터페이스 뒤. 소스 추가 = `sources/` 에 구현체 하나.
- **UI 는 표시만**: `app/` 는 데이터 접근을 갖지 않는다 — 전부 `pipeline` 이 캡슐화(테스트·재사용).
- **DB 가 조인·집계의 자리**: 향 불일치·소스 갭은 LLM 이 아니라 SQL 조인/집계에서 계산.
- **2층 데이터 아키텍처**: 1층(specs, 객관) ↔ 2층(reviews, 주관)을 `spec_id` 로 조인.

## Cross-module ripple (변경 영향)
| 바꾸는 것 | 파급 |
|---|---|
| `sql/schema.sql` 컬럼 | `db.py` · `index.py` · `search.py` · `consolidated_view.py` |
| `extract.LAYER2_SCHEMA` | `index.py`(비정규화 승격) · `sql`(메타 컬럼) · `evals` 골드 |
| 임베딩 모델 | `index.py` · `search.py` · `schema.sql`(벡터 차원·인덱스 재생성) |
| 새 소스 | `sources/` 구현체 + `pipeline.collect` 등록만 — 하류 무변경 |
| LLM 벤더/모델 | `llm_ops.py` 만 |
| 마켓 로고 자산 | KB `markets[].logo` · `data/market_logos/` · `source_links.logo_asset` · `app/ui.py` |

## 배포 (마지막 하드게이트)
Render(관리형 Postgres+pgvector) → `schema.sql` 적용 → `streamlit run app/ui.py` 호스팅.
로컬은 `.venv` + `docker compose up -d`(포트 55432).
