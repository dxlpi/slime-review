# -*- coding: utf-8 -*-
"""HTTP API — `web/` 프런트엔드가 백엔드를 보는 유일한 창구.

설계:
- **얇게 유지한다.** DB SQL·표시 정책·집계는 전부 `slime_rag` 안에 있다. 여기서 하는 일은
  `pipeline` 호출과 JSON 직렬화뿐이다. 여기에 로직이 자라기 시작하면 백엔드가 두 벌이 된다.
- 응답 shape 은 `web/src/data/mock.ts` 와 **같은 모양**으로 맞춘다 — 화면 마크업을 안 고치고
  목 데이터만 갈아끼우기 위해서다(디자인 원본과 픽셀 대조가 가능한 상태를 유지).
- 본문은 이미 `pipeline.list_reviews` 가 **서버에서 자른 발췌**다(ADR-0013 §3). 여기서 전문을
  다시 꺼내오지 말 것 — 자르는 자리는 한 곳이어야 공개 전환 때 빠뜨리지 않는다.

실행: uvicorn api.main:app --reload --port 8000   (repo 루트에서, .venv 활성화)
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from slime_rag import pipeline, source_links
from slime_rag.consolidated_view import CRITERION_SCOPE as CRITERION_SCOPE_OF

app = FastAPI(title="Slime Search API", version="0.1.0")

# 1층 스펙 사람 검수 도구(ADR-0016) — **기본이 꺼짐**이고 로컬 전용이다.
# 왜 환경변수 게이트인가: ① 결과물(`data/spec_overrides.json`)이 git 추적 파일이라 배포 서버의
# 임시 디스크에 쓰면 재배포 때 증발한다. ② 정적 프런트에 박은 토큰은 비밀이 아니다.
# 검수자는 1명이고 대상은 39행이라, 인증을 만드는 것보다 배포에서 아예 빼는 게 정직하다.
# ⚠️ 라우트는 등록 자체를 안 한다(핸들러 안에서 403 을 던지는 게 아니라). 켜지 않은 서버에서
#    `/api/admin/*` 는 존재하지 않는 경로 = 404 다.
ADMIN = os.getenv("ADMIN_ENABLED") == "1"

# 개발 중 Vite 개발서버(5173)만 허용. 배포 시 실제 오리진으로 교체한다.
# ⚠️ 쓰기 메서드는 검수 도구를 켰을 때만 연다 — 공개 배포는 GET-only 라는 성질을 유지한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"] if ADMIN else ["GET"],
    allow_headers=["*"],
)

# 화면 탭 라벨 → 백엔드 플랫폼 키. `list_reviews` 는 모르는 값에 예외를 던지므로(소스 미평균
# 규칙이 조용히 깨지는 걸 막는 가드) 변환은 **여기 한 곳**에서 한다.
PLATFORM_BY_TAB = {"instagram": "instagram", "dcinside": "dcinside"}


def _resolve_market(text: str | None) -> str | None:
    """사용자 입력 → **조회 키(`market_word`)**. 못 찾으면 입력 그대로(=결과 0건으로 정직하게).

    화면은 상호(슬라임지나)를 보여주는데 조회 키는 짧은 표시어(지나)다. 입력을 그대로 넘기면
    **화면에 보이는 이름을 그대로 쳤을 때 0건**이 나온다(2026-08-06 사용자 신고).
    `KB.resolve_market` 이 표면형(상호·표시어·핸들·별칭)과 초성을 모두 아는 유일한 해석기이므로
    여기서 재구현하지 않고 그걸 부른다 — 규칙이 두 벌이 되면 조용히 갈라진다.

    ⚠️ 후보가 2개 이상이면(초성 충돌 등) **보류하고 입력을 되돌린다**. 임의로 하나를 고르면
       엉뚱한 마켓의 후기를 그 마켓 것처럼 보여주게 된다 — 틀린 귀속 < 결과 없음.
    """
    from slime_rag import linking
    if not text or not text.strip():
        return None
    hits, _conf, _why = linking.load_kb().resolve_market(text.strip())
    if len(hits) == 1:
        return hits[0].get("market_word") or text.strip()
    return text.strip()


def _market_label(market_word: str | None) -> str | None:
    """조회 키(`market_word`) → **화면에 쓸 상호**. 예) '지나' → '슬라임지나'.

    KB 는 둘을 나눠 갖는다: `market_word` 는 조인·조회용 짧은 키(`reviews.market` 과 같은 값),
    `market` 은 마켓이 실제로 쓰는 상호다. 화면에 키를 그대로 내보내면 브랜드명이 잘려 보인다.
    """
    from slime_rag import linking
    if not market_word:
        return None
    e = next((m for m in linking.load_kb().markets
              if market_word in (m.get("market_word"), m.get("market"))), None)
    return (e or {}).get("market") or market_word


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/markets")
def markets() -> list[str]:
    return pipeline.list_markets()


@app.get("/api/markets/{market}/products")
def products(market: str) -> list[dict]:
    return pipeline.list_products(market)


def _logo(market: str | None) -> dict | None:
    """마켓 로고 → 프런트가 쓸 수 있는 형태. 파일 경로는 그대로 주지 않는다(서버 로컬 경로).

    `rel` 은 repo 상대 경로라 `/api/logo/{handle}` 로 서빙할 수도 있지만, 지금은 링크백과
    모노그램 폴백만 있으면 화면이 성립하므로 최소로 준다(ADR-0010 의 '삭제=철회' 성질 보존).
    """
    if not market:
        return None
    a = pipeline.market_logo(market) or {}
    if a.get("kind") == "image":
        return {"kind": "image", "handle": a.get("handle"), "url": a.get("url"),
                "src": f"/api/logo/{a.get('handle')}"}
    return {"kind": "monogram", "text": (a.get("text") or (market or "")[:2]),
            "url": a.get("url"), "handle": a.get("handle")}


@app.get("/api/logo/{handle}")
def logo(handle: str):
    """마켓 로고 파일 서빙. 경로이탈은 `logo_asset` 의 게이트가 이미 막는다(ADR-0010)."""
    from fastapi.responses import FileResponse
    from slime_rag import linking
    entry = next((m for m in linking.load_kb().markets if m.get("handle") == handle), None)
    asset = source_links.logo_asset(entry) if entry else {}
    if asset.get("kind") != "image" or not asset.get("path"):
        raise HTTPException(404, "로고 파일 없음")
    return FileResponse(asset["path"], media_type="image/jpeg")


def _cell(section: dict | None, key: str) -> dict:
    """기준 한 칸 → `{verdict, minority}`. 구 스키마(문자열 한 칸)도 그대로 읽는다.

    ADR-0014 이전에 저장된 요약이 DB 에 남아 있다. 재생성은 유료 호출이라 강제하지 않고,
    읽는 쪽에서 문자열을 verdict 로 승격해 화면이 깨지지 않게 한다(다수/소수 구분은 없다).
    """
    v = (section or {}).get(key)
    if isinstance(v, str):
        return {"verdict": v, "minority": None}
    if isinstance(v, dict):
        return {"verdict": v.get("verdict"), "minority": v.get("minority")}
    return {"verdict": None, "minority": None}


def _pick(payload: dict, market_payload: dict, key: str, card: str) -> dict:
    """기준 한 칸을 **그 기준의 축이 소유한 payload** 에서 꺼낸다(ADR-0015).

    고객 응대·배송은 마켓 단위 행(`product IS NULL`)에 있고, 제품 기준 넷은 제품 행에 있다.

    ⚠️ 폴백: 마켓 행이 아직 없으면 제품 행의 같은 키를 읽는다. ADR-0015 이전에 저장된
       요약은 여섯 기준이 **전부 제품 축**에 들어 있고, 재생성은 유료라 강제하지 않는다
       (ADR-0014 의 구 스키마 승격과 같은 정책). 마켓 행이 한 번 생기면 그쪽이 이긴다.
    """
    if CRITERION_SCOPE_OF.get(key) == "market":
        cell = _cell(market_payload.get(card), key)
        if cell["verdict"]:
            return cell
        return _cell(payload.get(card), key)        # 구 payload 폴백(제품 축에 섞여 있던 값)
    return _cell(payload.get(card), key)


def _summary_rows(payload: dict, market_payload: dict, card: str) -> list[dict]:
    """요약 카드 한 벌 → **축별 줄 목록**. 없으면 [](=아직 미생성이거나 언급 0).

    예전엔 기준 문장 6개를 공백으로 이어 한 문단으로 만들었다. 질감·향·소리·지속력이 한
    덩어리로 붙어 축 경계가 안 보였고, 읽는 사람이 매 문장 어느 축 얘긴지 되짚어야 했다.
    이제 축 라벨이 줄마다 붙는다 — 문장을 새로 쓰지 않는 원칙은 그대로다(표와 카드가 다른
    말을 하면 안 된다).

    verdict 가 빈 기준은 **줄 자체를 뺀다.** 화면의 '언급 없음'은 표가 맡는다.

    ⚠️ 여기에 배지·라벨을 다시 붙이지 말 것(사용자 결정 2026-08-07). 건수(`인스타 27 · 아모스갤 6`)·
       정서 분포(`갈림 19:5`)·`인스타만` 을 만들어 화면에 띄워 봤고, 전부 걷어냈다 — 다수/소수는
       이미 `verdict`/`minority` 두 칸이 **문장으로** 말하고 있어서 같은 말이 줄마다 두 번 붙었다.
       집계(`criterion_stats`)는 그대로 살아 있고, 프롬프트의 다수 판정 재료로 계속 쓰인다.

    ⚠️ 줄에 `scope` 를 함께 싣는다(ADR-0015). 화면이 '이 마켓 전체 기준'이라고 말해 줘야
       하는 줄이 어느 것인지는 백엔드가 안다 — 프런트가 키 이름으로 다시 판단하면 규칙이
       두 벌이 된다.
    """
    from slime_rag.consolidated_view import CRITERIA
    if not payload.get(card) and not market_payload.get(card):
        return []
    rows = []
    for c in CRITERIA:
        cell = _pick(payload, market_payload, c["key"], card)
        if not cell["verdict"]:
            continue
        rows.append({"key": c["key"], "ko": c["ko"], "en": c["en"],
                     "scope": c["scope"], **cell})
    return rows


def _by_criterion(payload: dict, market_payload: dict) -> dict:
    """{기준키: {ig, dc, all, scope}} — 디자인의 '평가 기준별 요약' 표가 그대로 먹는 모양.

    키는 `consolidated_view.CRITERIA` 하나가 정한다(스키마·프롬프트·화면표 공유, ADR-0011).
    각 칸은 `{verdict, minority}` 다 — 다수 의견과 소수 반론이 구조로 갈린다(ADR-0014).
    기준마다 `scope` 가 붙는다 — 고객 응대·배송 두 줄은 제품이 아니라 **마켓** 것이다(ADR-0015).
    """
    from slime_rag.consolidated_view import CRITERIA
    out = {}
    for c in CRITERIA:
        k = c["key"]
        out[k] = {"ig": _pick(payload, market_payload, k, "instagram"),
                  "dc": _pick(payload, market_payload, k, "dcinside"),
                  "all": _pick(payload, market_payload, k, "integrated"),
                  "scope": c["scope"]}
    return out


@app.get("/api/page")
def page(market: str | None = Query(None), product: str | None = Query(None)) -> dict:
    """제품 페이지 1벌 — 히어로 + 1층 스펙 + 요약 + 커뮤니티 리뷰.

    `market` 은 선택이다. 디시 이용자는 마켓을 언급하지 않는 경우가 많아 개체연결이 보류하는데
    (2026-08-06 실측), 마켓 필수로 조회하면 후기가 있어도 0건이 된다 — `pipeline.list_reviews`
    의 계약과 같은 이유다.
    """
    if not market and not product:
        raise HTTPException(400, "market 또는 product 중 하나는 필요하다")
    # 화면에 보이는 상호/초성/핸들 어느 쪽으로 쳐도 조회 키로 환원한다.
    market = _resolve_market(market)

    spec, stored = None, None
    n_reviews = 0
    if market and product:
        # with_summary=False — 요약은 **미리 생성해 저장**한 것만 쓴다(사용자 결정 2026-08-06).
        # 페이지 로드마다 LLM 을 부르면 열 때마다 과금된다. 생성은 `pipeline.generate_summaries`.
        view = pipeline.consolidated_for(market, product, with_summary=False)
        spec = view.get("official_spec")
        n_reviews = view.get("n_reviews") or 0
        stored = pipeline.stored_summaries(market, product)

    # 주문 축(고객 응대·배송)은 **마켓당 한 벌**이다(ADR-0015) — 제품이 안 정해져도 읽는다.
    stored_market = pipeline.stored_market_summaries(market) if market else None

    payload = (stored or {}).get("payload") or {}
    market_payload = (stored_market or {}).get("payload") or {}
    reviews = pipeline.list_reviews(market=market, product=product, limit=100)
    ig = [r for r in reviews if r["platform"] == "instagram"]
    dc = [r for r in reviews if r["platform"] == "dcinside"]

    return {
        "market": market,                       # 조회 키(조인용) — 화면 표시는 marketLabel
        "marketLabel": _market_label(market),   # 상호(예: 슬라임지나)
        "marketLogo": _logo(market),
        "product": product,
        "spec": spec,
        "embedUrl": source_links.embed_url(spec) if spec else None,
        "summary": {k: _summary_rows(payload, market_payload, k)
                    for k in ("integrated", "instagram", "dcinside")},
        "byCriterion": _by_criterion(payload, market_payload),
        # 장단점은 **제품 축에만** 있다(ADR-0015) — 주문 축 스키마엔 칸 자체가 없다.
        # 한 화면에 장단점 목록이 둘이면 어느 쪽을 읽어야 하는지 알 수 없다.
        "prosCons": {k: {"pros": (payload.get(k) or {}).get("pros") or [],
                         "cons": (payload.get(k) or {}).get("cons") or []}
                     for k in ("integrated", "instagram", "dcinside")},
        "summaryMeta": {"generatedAt": (stored or {}).get("generated_at"),
                        "model": (stored or {}).get("model"),
                        "nReviews": (stored or {}).get("n_reviews")},
        # 주문 축은 근거 모집단이 다르다 — 제품 후기 수가 아니라 **접은 주문 조각 수**다.
        # 같은 '건수'라는 말로 두 값을 섞으면 배송 줄이 제품 후기 수만큼 있는 것처럼 읽힌다.
        "marketSummaryMeta": {"generatedAt": (stored_market or {}).get("generated_at"),
                              "model": (stored_market or {}).get("model"),
                              "nOrders": (stored_market or {}).get("n_reviews")},
        "nReviews": n_reviews,
        "igReviews": ig,
        "dcReviews": dc,
        "igCount": len(ig),
        "dcCount": len(dc),
    }


# ---------------------------------------------------------------- 1층 사람 검수(로컬 전용)
class SpecOverrideIn(BaseModel):
    """검수 화면이 보내는 한 건. `fields` 는 `{칸: 값}`, `unknown_fields` 는 '보고도 모름'.

    칸 이름 검증은 여기서 하지 않는다 — `spec_overrides.OVERRIDABLE` 한 곳이 정본이고
    `pipeline.save_spec_override` 가 그걸로 거른다(규칙이 두 벌이 되면 조용히 갈라진다).
    """
    market: str
    product: str
    fields: dict = {}
    unknown_fields: list[str] = []
    note: str | None = None


if ADMIN:
    @app.get("/api/admin/spec-queue")
    def spec_queue() -> list[dict]:
        """사람이 봐야 하는 1층 스펙 행 목록(빈 칸 · 임베드 URL 포함)."""
        return pipeline.spec_review_queue()

    @app.post("/api/admin/spec-override")
    def spec_override(body: SpecOverrideIn) -> dict:
        """검수 1건 저장 → 오버레이 파일 + `specs` 그 한 행 + 다음 항목."""
        try:
            return pipeline.save_spec_override(
                body.market, body.product, body.fields,
                unknown_fields=tuple(body.unknown_fields), note=body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
