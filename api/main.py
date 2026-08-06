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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from slime_rag import pipeline, source_links

app = FastAPI(title="Slime Search API", version="0.1.0")

# 개발 중 Vite 개발서버(5173)만 허용. 배포 시 실제 오리진으로 교체한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
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


def _paragraph(section: dict | None) -> str | None:
    """6기준 요약 → 카드에 넣을 **문단**. 없으면 None(=아직 미생성).

    저장된 요약은 기준별 문장 6개 + pros/cons 구조다. 디자인의 요약 카드는 '4–6문장 문단'을
    요구하므로 **미언급(null)이 아닌 기준 문장을 순서대로 잇는다** — 새로 쓰지 않는다.
    문장을 생성하면 근거 없는 문장이 섞이고, 그 순간 6기준 표와 카드가 서로 다른 말을 한다.
    """
    if not section:
        return None
    from slime_rag.consolidated_view import CRITERIA
    parts = [section.get(c["key"]) for c in CRITERIA]
    joined = " ".join(p.strip() for p in parts if p)
    return joined or None


def _by_criterion(payload: dict) -> dict:
    """{기준키: {ig, dc, all}} — 디자인의 '평가 기준별 요약' 표가 그대로 먹는 모양.

    키는 `consolidated_view.CRITERIA` 하나가 정한다(스키마·프롬프트·화면표 공유, ADR-0011).
    """
    from slime_rag.consolidated_view import CRITERIA
    out = {}
    for c in CRITERIA:
        k = c["key"]
        out[k] = {"ig": (payload.get("instagram") or {}).get(k),
                  "dc": (payload.get("dcinside") or {}).get(k),
                  "all": (payload.get("integrated") or {}).get(k)}
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

    payload = (stored or {}).get("payload") or {}
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
        "summary": {k: _paragraph(payload.get(k)) for k in ("integrated", "instagram", "dcinside")},
        "byCriterion": _by_criterion(payload),
        "prosCons": {k: {"pros": (payload.get(k) or {}).get("pros") or [],
                         "cons": (payload.get(k) or {}).get("cons") or []}
                     for k in ("integrated", "instagram", "dcinside")},
        "summaryMeta": {"generatedAt": (stored or {}).get("generated_at"),
                        "model": (stored or {}).get("model"),
                        "nReviews": (stored or {}).get("n_reviews")},
        "nReviews": n_reviews,
        "igReviews": ig,
        "dcReviews": dc,
        "igCount": len(ig),
        "dcCount": len(dc),
    }
