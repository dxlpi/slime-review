# -*- coding: utf-8 -*-
"""
UI (Phase 6) — Streamlit 챗 + 필터 + 종합뷰.

실행: streamlit run app/ui.py   (docker pgvector + .env OPENAI_API_KEY 필요)
화면:
  ① 챗  — 질문 → search.answer (근거 인용, '모르면 모른다', 소스 평균 금지)
  ② 종합뷰 — 1층 스펙 + 2층 후기 → 소스별 정서·갭·향불일치·소스aware 요약
  ③ 사이드바 — 마켓/소스 메타필터, 1층 스펙 패널

백엔드 글루는 slime_rag.pipeline / slime_rag.search 에 캡슐화. 이 파일은 표시만 담당.
"""

import sys
from pathlib import Path

# `streamlit run app/ui.py` 는 repo 루트가 sys.path 에 없으므로 추가.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from slime_rag import pipeline, search

st.set_page_config(page_title="슬라임 리뷰 RAG", page_icon="🟢", layout="wide")

# 소스 라벨 ↔ DB source 컬럼 값
_SOURCE_LABELS = {"전체": None, "디시 아모스갤(부정쏠림)": "amos",
                  "인스타(긍정쏠림)": "instagram"}


@st.cache_resource(show_spinner="DB 스키마·1층·2층 색인 준비 중…")
def _bootstrap() -> dict:
    """프로세스당 1회: 스키마 적용 + 1층 specs + 2층 골드 색인 + 조인."""
    return pipeline.setup()


def _render_consolidated(view: dict) -> None:
    spec = view.get("official_spec")
    if spec:
        c1, c2, c3 = st.columns(3)
        c1.metric("공식 향료", spec.get("official_scent") or "—")
        c2.metric("풀조합", spec.get("base_combo") or "—")
        c3.metric("종류", spec.get("slime_type") or "—")
        beads = spec.get("beads") or []
        if beads:
            st.caption("비즈: " + ", ".join(beads))
    st.caption(f"후기 {view['n_reviews']}건")

    by_src = view.get("by_source") or {}
    if by_src:
        st.subheader("소스별 정서 (평균 안 냄 — 소스별 + 갭)")
        cols = st.columns(len(by_src))
        for col, (plat, c) in zip(cols, by_src.items()):
            col.metric(f"{plat} · net", f"{c['net']:+.2f}",
                       help=f"{c['bias_label']} · pos{c['pos']}/neu{c['neu']}/neg{c['neg']} (n={c['n']})")

    gap = view.get("sentiment_gap")
    if gap:
        st.info(f"**소스 갭** 인스타 {gap['instagram_net']:+.2f} vs 디시 "
                f"{gap['dcinside_net']:+.2f} → 갭 {gap['gap']:+.2f} · {gap['reading']}")
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

    if view.get("summary"):
        st.subheader("📝 소스aware 요약")
        st.markdown(view["summary"])


# ---------------------------------------------------------------- 레이아웃
st.title("🟢 슬라임 리뷰 RAG")
st.caption("공식 스펙(1층) + 후기(2층)를 출처 인용해 답하는 근거 기반 어시스턴트 · "
           "소스 편향(인스타=긍정/디시=부정)은 평균내지 않고 투명화")

try:
    counts = _bootstrap()
except Exception as e:                       # DB/키 미준비 시 안내
    st.error(f"백엔드 준비 실패: {e}\n\ndocker compose up -d (pgvector 55432) 와 "
             ".env 의 OPENAI_API_KEY 를 확인하세요.")
    st.stop()

markets = pipeline.list_markets()

# --- 사이드바: 필터 + 1층 패널 ---
with st.sidebar:
    st.header("필터")
    market_sel = st.selectbox("마켓", ["전체"] + markets)
    source_label = st.radio("소스", list(_SOURCE_LABELS))
    st.divider()
    st.caption(f"색인 현황 · specs {counts['specs']} · reviews {counts['reviews']}")
    if market_sel != "전체":
        st.subheader(f"1층 스펙 · {market_sel}")
        for p in pipeline.list_products(market_sel):
            beads = ", ".join(p.get("beads") or []) or "—"
            st.markdown(f"**{p['product']}**  \n향: {p['official_scent'] or '—'}  \n"
                        f"풀: {p['base_combo'] or '—'}  \n종류: {p['slime_type'] or '—'}  \n"
                        f"비즈: {beads}")

tab_chat, tab_view = st.tabs(["💬 챗(근거 답변)", "📊 종합뷰(소스 편향)"])

with tab_chat:
    q = st.text_input("질문", placeholder="예: 빈짱 한글과자한줌 향이랑 비즈 어때?")
    if q:
        filters = {}
        if market_sel != "전체":
            filters["market"] = market_sel
        if _SOURCE_LABELS[source_label]:
            filters["source"] = _SOURCE_LABELS[source_label]
        with st.spinner("검색·근거 답변 생성 중…"):
            ans = search.answer(q, filters=filters or None)
        st.markdown(ans.text)
        with st.expander(f"근거 {len(ans.citations)}건"):
            for c in ans.citations:
                st.markdown(f"**[{c['n']}]** `{c['source']}` · {c['market']}/"
                            f"{c['product']}  \n{c['evidence']}")

with tab_view:
    if market_sel == "전체":
        st.info("← 사이드바에서 마켓을 먼저 고르세요.")
    else:
        prods = [p["product"] for p in pipeline.list_products(market_sel)]
        product_sel = st.selectbox("제품", prods)
        if product_sel and st.button("종합뷰 생성", type="primary"):
            with st.spinner("종합·편향 집계 + 요약 중…"):
                view = pipeline.consolidated_for(market_sel, product_sel)
            _render_consolidated(view)
