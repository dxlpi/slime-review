# slime_rag/sources/ — 수집 레이어 (플러그인 패키지)

## Purpose (이 모듈이 소유하는 것)
소스별 수집기. `Source` 인터페이스 하나로 디시/인스타/Apify 를 동일하게 다루고, 각 구현체는
`RawReview` 만 내보낸다. 추출·연결·집계는 하류가 담당. 과거 단일 `sources.py`(791줄)를
소스별로 분할한 것 — **import 경로는 불변**(`from slime_rag.sources import X` 그대로).

## Key files
| 파일 | 역할 |
|---|---|
| `base.py` | `RawReview`, `Source` ABC, `Throttle`, `robots_allowed`, `get`, 노이즈/유해 필터 |
| `dcinside.py` | `DCInsideSource` — 아모스갤 본문+댓글(AJAX), 2층 백본 |
| `instagram.py` | `InstagramSource` — 1층 fixture / Graph API 스텁 |
| `apify.py` | `ApifyHashtagSource`(2층 해시태그) · `InstagramProfileSource`(1층 판매자) |
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
- **Don't:** 공개 심볼을 서브모듈에서 직접 import 하지 말 것 — 항상 `slime_rag.sources` 패키지 표면에서.
- **Warning:** 토큰/패키지 없으면 소스는 예외 없이 `[]` 반환(회복력) — `collect_all` 이 스킵 로깅.

## Cross-module dependencies
- 상위 [`../CLAUDE.md`](../CLAUDE.md) 의 코어 패키지. 소비처: `extract`, `bias`, `relevance`, `pipeline`
- 소싱 결정 근거: [ADR-0003](../../docs/adr/0003-ig-businessdiscovery-fixture.md) · [ADR-0004](../../docs/adr/0004-promo-gate-llm-cascade.md)
