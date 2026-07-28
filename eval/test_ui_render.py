# -*- coding: utf-8 -*-
"""
UI 종합뷰 렌더 헤드리스 테스트 — DB/LLM 미호출. streamlit AppTest 로 예외 0 + 요소 확인.

app/ui.py 는 app body 를 main() 가드(__name__=='__main__') 뒤에 둬서, import 시 부트스트랩
(DB/키)을 타지 않는다 → 렌더 헬퍼(_render_consolidated)만 목 view 로 검증 가능.

검증:
  - 향/질감/장단점 3블록(인스타/디시/통합)이 렌더된다.
  - texture=None 등 미언급 섹션은 렌더 안 됨(빈칸).
  - review_summaries 가 전부 None 이면 '리뷰 요약' 헤더 자체 생략.
  - 공식 스펙 source_permalink 가 있으면 링크 캡션 렌더.

실행:  python -m eval.test_ui_render   (repo 루트에서)
"""
from __future__ import annotations


def _views() -> dict:
    return {
        "full": {   # 두 소스 + 통합, IG texture 있음/장점만, DC texture 빈칸/단점 있음
            "official_spec": {"official_scent": "레몬마들렌향", "base_combo": "플레인+쁠루모",
                              "slime_type": "지글리", "beads": ["6mm", "퍼즐비즈"],
                              "source_permalink": "https://instagram.com/p/ABC"},
            "n_reviews": 13,
            "by_source": {"instagram": {"net": 0.6, "pos": 8, "neu": 0, "neg": 0, "n": 8},
                          "dcinside": {"net": -0.2, "pos": 1, "neu": 1, "neg": 3, "n": 5}},
            "sentiment_gap": {"instagram_net": 0.6, "dcinside_net": -0.2, "gap": 0.8},
            "scent_divergence": {"official": "레몬", "diverged_ratio": 0.4, "top_perceived": [["인공레몬", 2]], "n": 5},
            "praised": {"instagram": [["scent", 3]]}, "criticized": {"dcinside": [["shipping_cs", 2]]},
            "review_summaries": {
                "instagram": {"scent": "레몬향 진하고 상큼", "texture": "말랑·텐션 좋음", "pros": ["향 지속력", "가성비"], "cons": []},
                "dcinside": {"scent": "인공적 레몬향 지적", "texture": None, "pros": ["가성비"], "cons": ["배송 지연", "흐름성 강함"]},
                "integrated": {"scent": "양쪽 레몬 인지, 인스타 호평/디시 인공향(gap 0.8)", "texture": "인스타만 평가",
                               "pros": ["[공통] 향 존재감"], "cons": ["[디시] 배송·흐름성"]},
            },
            # 서포터(홍보성) 버킷도 향/질감/장단점 섹션 형태로 포함(소수라도 표시).
            "promo_view": {"n_promo": 1, "scent": "레몬마들렌향 은은", "texture": "매트·포닥",
                           "pros": ["발색·향"], "cons": []},
        },
        "single_source": {   # 디시만 → 통합 None, 질감 빈칸, URL 없음
            "official_spec": {"official_scent": None, "base_combo": None, "slime_type": None, "beads": [], "source_permalink": None},
            "n_reviews": 3,
            "by_source": {"dcinside": {"net": -0.3, "pos": 0, "neu": 1, "neg": 2, "n": 3}},
            "sentiment_gap": None, "scent_divergence": None, "praised": {}, "criticized": {},
            "review_summaries": {"instagram": None,
                                 "dcinside": {"scent": "비누향 지적", "texture": None, "pros": [], "cons": ["배송"]},
                                 "integrated": None},
            "promo_view": None,
        },
        "no_summaries": {   # 요약 전부 None → 요약 헤더 생략
            "official_spec": {"official_scent": "라벤더", "base_combo": None, "slime_type": None, "beads": [], "source_permalink": None},
            "n_reviews": 1,
            "by_source": {"dcinside": {"net": -1.0, "pos": 0, "neu": 0, "neg": 1, "n": 1}},
            "sentiment_gap": None, "scent_divergence": None, "praised": {}, "criticized": {},
            "review_summaries": {"instagram": None, "dcinside": None, "integrated": None},
            "promo_view": None,
        },
    }


def _script() -> None:
    """AppTest 가 실행할 스크립트: 목 view 들을 렌더."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import streamlit as st
    from app.ui import _render_consolidated
    from eval.test_ui_render import _views
    for name, v in _views().items():
        st.header(name)
        _render_consolidated(v)


def test_ui_render_no_exception_and_blocks():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_script).run(timeout=30)
    assert not at.exception, at.exception            # 어떤 view 에서도 예외 0

    mds = [m.value for m in at.markdown]
    caps = [c.value for c in at.caption]
    subs = [s.value for s in at.subheader]

    assert any("레몬향 진하고 상큼" in m for m in mds), "인스타 향 섹션 미렌더"
    assert any("➖ 배송 지연" in m for m in mds), "디시 장단점 미렌더"
    assert any("🔀 통합" in m for m in mds), "통합 블록 미렌더"
    assert any("**질감**" in m for m in mds), "full 뷰 질감 섹션 미렌더"
    assert any("공식 스펙 출처" in c and "instagram.com/p/ABC" in c for c in caps), "스펙 URL 링크 미렌더"
    assert any("🎁 서포터 리뷰" in m for m in mds), "서포터 리뷰 블록 미렌더"
    assert any("레몬마들렌향 은은" in m for m in mds), "서포터 향 섹션 미렌더"
    # 편향 라벨(긍정/부정 쏠림)은 제거됨 — 어디에도 나오면 안 됨.
    alltext = mds + caps + subs
    assert not any("쏠림" in t for t in alltext), "쏠림 라벨이 아직 렌더됨"
    # 요약 헤더는 요약/서포터 있는 뷰 2개(full, single_source)에만, no_summaries 뷰엔 생략.
    n_hdr = sum("리뷰 요약" in s for s in subs)
    assert n_hdr == 2, f"요약 헤더 {n_hdr}개 (기대 2)"
    print("✓ UI 렌더: 예외 0 · 3블록 · 서포터 블록 · URL · 쏠림 라벨 제거 OK")


if __name__ == "__main__":
    test_ui_render_no_exception_and_blocks()
    print("\nUI 렌더 헤드리스 테스트 통과 ✅")
