# -*- coding: utf-8 -*-
"""
UI (Phase 6) — Streamlit 리뷰 요약 + 챗.

실행: streamlit run app/ui.py   (docker pgvector + .env OPENAI_API_KEY 필요)
화면:
  ① 상단 선택 — 마켓 드롭다운(타이핑/스크롤) → 범위(마켓 전체 / 특정 제품) → 제품 타이핑 검색
  ② 리뷰 요약 — 1층 스펙 + 2층 후기 → 소스별 정서·갭·향불일치·소스aware 요약
  ③ 챗 — 질문 → search.answer (근거 인용, '모르면 모른다', 소스 평균 금지)
  ④ 사이드바 — 소스 메타필터, 색인 현황, 1층 스펙 패널

백엔드 글루는 slime_rag.pipeline / slime_rag.search 에 캡슐화. 이 파일은 표시만 담당.
"""

import sys
from pathlib import Path

# `streamlit run app/ui.py` 는 repo 루트가 sys.path 에 없으므로 추가.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from slime_rag import pipeline, search
from slime_rag.linking import choseong

# 소스 라벨 ↔ DB source 컬럼 값
_SOURCE_LABELS = {"전체": None, "디시 아모스갤": "amos", "인스타": "instagram"}

SCOPE_MARKET, SCOPE_PRODUCT = "마켓 전체", "특정 제품"


@st.cache_resource(show_spinner="DB 스키마·1층·2층 색인 준비 중…")
def _bootstrap() -> dict:
    """프로세스당 1회: 스키마 적용 + 1층 specs + 2층 골드 색인 + 조인."""
    return pipeline.setup()


# 리뷰 요약 블록 라벨(플랫폼 → 표시). 통합은 소스 갭 reconciliation.
_REVIEW_TITLES = {"instagram": "🟣 인스타 리뷰", "dcinside": "🔵 디시 리뷰",
                  "integrated": "🔀 통합 리뷰 (인스타+디시)"}


# ---------------------------------------------------------------- 검색 헬퍼(순수)
def _norm(s: str) -> str:
    """비교용 정규화 — 공백 제거 + 소문자. '한글 과자 한줌' ↔ '한글과자한줌'."""
    return "".join(s.split()).lower()


def filter_products(products: list[dict], query: str) -> list[dict]:
    """제품 타이핑 검색 — 부분일치(공백/대소문자 무시) + 초성 검색('ㅎㄱㄱㅈ' → 한글과자한줌).

    순수 함수(위젯·DB 무관) — 헤드리스 테스트 대상. 빈 질의는 전체를 그대로 돌려준다.
    """
    q = _norm(query or "")
    if not q:
        return list(products)
    q_cho = choseong(q)                       # 질의가 초성으로만 이뤄졌을 때만 의미 있음
    q_is_cho = bool(q_cho) and q_cho == q     # choseong() 은 자모를 그대로 통과시킨다
    out = []
    for p in products:
        name = _norm(p["product"])
        if q in name or (q_is_cho and q_cho in choseong(name)):
            out.append(p)
    return out


def _render_review_block(title: str, block: dict | None, meta: str | None = None) -> None:
    """향/질감/배송·CS/장단점 섹션 렌더. 언급 없는 섹션(None/빈)은 통째로 생략 — 빈칸 유지, 패딩 없음."""
    if not block:
        return
    st.markdown(f"#### {title}" + (f"  ·  {meta}" if meta else ""))
    if block.get("scent"):
        st.markdown(f"**향** — {block['scent']}")
    if block.get("texture"):
        st.markdown(f"**질감** — {block['texture']}")
    if block.get("shipping"):
        st.markdown(f"**배송·CS** — {block['shipping']}")
    pros, cons = block.get("pros") or [], block.get("cons") or []
    if pros or cons:
        st.markdown("**장단점**")
        for p in pros:
            st.markdown(f"- ➕ {p}")
        for c in cons:
            st.markdown(f"- ➖ {c}")


def _render_spec(spec: dict) -> None:
    """1층 공식 스펙 카드 — specs 행 그대로의 결정적 표시. 후기(2층) 요약과 완전 분리."""
    c1, c2, c3 = st.columns(3)
    c1.metric("공식 향료", spec.get("official_scent") or "—")
    c2.metric("풀조합", spec.get("base_combo") or "—")
    c3.metric("종류", spec.get("slime_type") or "—")
    beads = spec.get("beads") or []
    if beads:
        st.caption("비즈: " + ", ".join(beads))
    url = spec.get("source_permalink")
    if url:
        st.caption(f"공식 스펙 출처: [인스타 게시물]({url})")


def _render_consolidated(view: dict) -> None:
    spec = view.get("official_spec")
    if spec:                                     # 마켓 단위 뷰는 spec 없음(제품 단위 개념)
        st.subheader("📋 공식 스펙 (1층 · 판매자 공식 정보)")
        _render_spec(spec)
        st.divider()
    st.caption(f"후기 {view['n_reviews']}건")

    by_src = view.get("by_source") or {}
    if by_src:
        st.subheader("소스별 정서 (평균 안 냄 — 소스별 + 갭)")
        cols = st.columns(len(by_src))
        for col, (plat, c) in zip(cols, by_src.items()):
            col.metric(f"{plat} · net", f"{c['net']:+.2f}",
                       help=f"pos{c['pos']}/neu{c['neu']}/neg{c['neg']} (n={c['n']})")

    gap = view.get("sentiment_gap")
    if gap:
        st.info(f"**소스 갭** 인스타 {gap['instagram_net']:+.2f} vs 디시 "
                f"{gap['dcinside_net']:+.2f} → 갭 {gap['gap']:+.2f}")
    else:
        st.warning("두 소스(인스타·디시)가 모두 있어야 갭 계산 — 현재 샘플은 한쪽 소스만 존재.")

    div = view.get("scent_divergence")
    if div:
        st.subheader("향 불일치 (공식향 vs 체감향)")
        st.write(f"공식: **{div['official']}** · 불일치 비율 "
                 f"**{div['diverged_ratio']:.0%}** (n={div['n']})")
        st.write("자주 체감된 향: " +
                 ", ".join(f"{p}({n})" for p, n in div["top_perceived"]))

    colp, colc = st.columns(2)
    with colp:
        st.subheader("👍 호평 속성")
        st.write(view.get("praised") or "—")
    with colc:
        st.subheader("👎 지적 속성")
        st.write(view.get("criticized") or "—")

    rs = view.get("review_summaries") or {}
    promo = view.get("promo_view")
    if any(rs.values()) or promo:
        st.subheader("📝 리뷰 요약 (향·질감·장단점, 언급 없으면 빈칸)")
        by = view.get("by_source") or {}

        def _meta(plat: str) -> str | None:
            c = by.get(plat)
            return f"{c['n']}건" if c else None

        _render_review_block(_REVIEW_TITLES["instagram"], rs.get("instagram"), _meta("instagram"))
        _render_review_block(_REVIEW_TITLES["dcinside"], rs.get("dcinside"), _meta("dcinside"))
        _render_review_block(_REVIEW_TITLES["integrated"], rs.get("integrated"))
        if promo:                                    # 서포터(홍보성) — 실사용과 분리해 별도 표시
            _render_review_block("🎁 서포터 리뷰 (협찬·무상 제공)", promo,
                                 f"{promo.get('n_promo', 0)}건")


# ---------------------------------------------------------------- 선택 컨트롤
def render_selection(markets: list[str], products_of) -> dict:
    """상단 선택 UI — 마켓 드롭다운 → 범위 → 제품 타이핑 검색.

    products_of: market → list[dict] (기본은 `pipeline.list_products`; 테스트는 가짜 주입).
    반환: {market, product, scope, products} — scope 는 위젯 값을 그대로 돌려준다
    (session_state 로 되읽지 말 것: 라벨은 위젯 키가 아니다).
    """
    c_market, c_scope = st.columns([2, 3])
    with c_market:
        # selectbox 는 타이핑하면 옵션이 필터되는 콤보박스 — '입력 또는 스크롤' 둘 다 지원.
        market = st.selectbox("마켓", markets, index=None,
                              placeholder="마켓 이름을 입력하거나 목록에서 고르세요")
    if not market:
        st.info("마켓을 먼저 고르세요 — 그 다음 '마켓 전체' 또는 '특정 제품' 요약을 만들 수 있습니다.")
        return {"market": None, "product": None, "scope": SCOPE_MARKET, "products": []}

    products = products_of(market)
    with c_scope:
        scope = st.radio("요약 범위", [SCOPE_MARKET, SCOPE_PRODUCT], horizontal=True,
                         help=f"{SCOPE_MARKET} = 이 마켓에서 추적 중인 제품들의 후기 집계 · "
                              f"{SCOPE_PRODUCT} = 그 제품 후기만 + 공식 스펙 카드")
    sel = {"market": market, "product": None, "scope": scope, "products": products}
    if scope == SCOPE_MARKET:
        return sel

    c_q, c_pick = st.columns([2, 3])
    with c_q:
        q = st.text_input("제품 검색", placeholder="제품명 일부 또는 초성 (예: 한줌, ㅎㄱㄱㅈ)")
    matches = filter_products(products, q)
    if not matches:
        st.warning(f"'{q}' 와 맞는 제품이 없습니다 — {market} 의 1층 제품 {len(products)}개 중 검색.")
        return sel
    with c_pick:
        sel["product"] = st.selectbox(f"제품 ({len(matches)}개)",
                                      [p["product"] for p in matches], index=None,
                                      placeholder="제품을 고르세요")
    return sel


# ---------------------------------------------------------------- 요약 탭
def _summary_key(market: str, product: str | None) -> str:
    return f"{market}\x00{product or '*'}"


def _render_summary_tab(market: str | None, product: str | None, scope_is_product: bool) -> None:
    """'요약 생성' 버튼 → pipeline 종합뷰. 결과는 session_state 에 남겨 리런에도 유지."""
    if not market:
        return
    if scope_is_product and not product:
        st.info("제품을 고르면 그 제품의 리뷰 요약을 만듭니다 — 또는 범위를 '마켓 전체'로 바꾸세요.")
        return

    if scope_is_product:
        st.markdown(f"**{market} / {product}** 의 후기 요약")
    else:
        st.markdown(f"**{market}** 전체 후기 요약")
        st.caption("범위: 이 마켓에서 추적 중인 제품들의 후기 집계 — 수집이 제품 앵커라 "
                   "'마켓 전체 여론'이 아닙니다(ADR-0007). 제품별 공식 스펙은 사이드바 1층 패널 참조.")

    cache = st.session_state.setdefault("summaries", {})
    key = _summary_key(market, product)
    label = "리뷰 요약 생성" if key not in cache else "다시 생성"
    if st.button(label, type="primary", key="btn_summary"):
        with st.spinner("종합·편향 집계 + 요약 중…"):
            cache[key] = (pipeline.consolidated_for(market, product) if scope_is_product
                          else pipeline.consolidated_for_market(market))
    view = cache.get(key)
    if view:
        _render_consolidated(view)


# ---------------------------------------------------------------- 레이아웃
def main() -> None:
  st.set_page_config(page_title="슬라임 리뷰 RAG", page_icon="🟢", layout="wide")
  st.title("🟢 슬라임 리뷰 RAG")
  st.caption("공식 스펙(1층) + 후기(2층)를 출처 인용해 답하는 근거 기반 어시스턴트 · "
             "소스 편향(인스타=긍정/디시=부정)은 평균내지 않고 투명화")

  try:
    counts = _bootstrap()
  except Exception as e:                       # DB/키 미준비 시 안내
    st.error(f"백엔드 준비 실패: {e}\n\ndocker compose up -d (pgvector 55432) 와 "
             ".env 의 OPENAI_API_KEY 를 확인하세요.")
    st.stop()

  sel = render_selection(pipeline.list_markets(), pipeline.list_products)
  market_sel, product_sel = sel["market"], sel["product"]
  market_products = sel["products"]
  scope_is_product = sel["scope"] == SCOPE_PRODUCT
  st.divider()

  # --- 사이드바: 소스 필터 + 색인 현황 + 1층 패널 ---
  with st.sidebar:
    st.header("필터")
    source_label = st.radio("소스", list(_SOURCE_LABELS), help="챗 검색에 적용되는 소스 필터")
    st.divider()
    st.caption(f"색인 현황 · specs {counts['specs']} · reviews {counts['reviews']}")
    if market_sel:
        st.subheader(f"1층 스펙 · {market_sel}")
        for p in market_products:
            beads = ", ".join(p.get("beads") or []) or "—"
            line = (f"**{p['product']}**  \n향: {p['official_scent'] or '—'}  \n"
                    f"풀: {p['base_combo'] or '—'}  \n종류: {p['slime_type'] or '—'}  \n"
                    f"비즈: {beads}")
            if p.get("source_permalink"):
                line += f"  \n[공식 스펙 게시물]({p['source_permalink']})"
            st.markdown(line)

  tab_view, tab_chat = st.tabs(["📊 리뷰 요약(소스 편향)", "💬 챗(근거 답변)"])

  with tab_view:
    # 마켓 미선택이면 아무것도 안 그린다 — 안내는 바로 위 선택 UI 가 이미 한 번 한다.
    _render_summary_tab(market_sel, product_sel, scope_is_product)

  with tab_chat:
    # '특정 제품' 범위: 공식 스펙(1층)은 결정적 카드로 따로 표시 — 챗 답변(2층 후기만
    # 근거)과 완전 분리. 스펙이 검색·답변 프롬프트에 섞이지 않는다.
    if product_sel:
        st.subheader(f"📋 공식 스펙 (1층) · {market_sel} / {product_sel}")
        spec = next((p for p in market_products if p["product"] == product_sel), None)
        if spec:
            _render_spec(spec)
        else:
            st.caption("이 제품의 1층 스펙이 아직 없습니다.")
        st.caption("아래 챗 답변은 2층(후기)만 근거로 합니다 — 공식 스펙 질문은 위 카드 참조.")
        st.divider()
    q = st.text_input("질문", placeholder="예: 빈짱 한글과자한줌 향이랑 비즈 어때?")
    if q:
        filters = {}
        if market_sel:
            filters["market"] = market_sel
        if product_sel:
            filters["product"] = product_sel
        if _SOURCE_LABELS[source_label]:
            filters["source"] = _SOURCE_LABELS[source_label]
        with st.spinner("검색·근거 답변 생성 중…"):
            ans = search.answer(q, filters=filters or None)
        st.markdown(ans.text)
        with st.expander(f"근거 {len(ans.citations)}건"):
            for c in ans.citations:
                st.markdown(f"**[{c['n']}]** `{c['source']}` · {c['market']}/"
                            f"{c['product']}  \n{c['evidence']}")


if __name__ == "__main__":            # streamlit run 시 __name__=='__main__' → 실행.
    main()                            # import(app.ui) 시엔 미실행 → 렌더 헬퍼 side-effect-free 테스트.
