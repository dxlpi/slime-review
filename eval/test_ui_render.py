# -*- coding: utf-8 -*-
"""
UI 렌더·선택 헤드리스 테스트 — DB/LLM 미호출. streamlit AppTest 로 예외 0 + 요소 확인.

app/ui.py 는 app body 를 main() 가드(__name__=='__main__') 뒤에 둬서, import 시 부트스트랩
(DB/키)을 타지 않는다 → 렌더 헬퍼(_render_consolidated)·선택 UI(render_selection)를
목 데이터로 검증할 수 있다(제품 목록은 콜러블 주입이라 DB 불필요).

검증:
  - 향/질감/장단점 3블록(인스타/디시/통합)이 렌더된다.
  - texture=None 등 미언급 섹션은 렌더 안 됨(빈칸).
  - review_summaries 가 전부 None 이면 '리뷰 요약' 헤더 자체 생략.
  - 공식 스펙 source_permalink 가 있으면 링크 캡션 렌더.
  - 근거 원문 목록: 링크 있으면 실사용/서포터 버킷 분리 렌더, 없으면 섹션 통째 생략.
  - 정직성 캡션 2종(evidence≠인용 · 건수 단위)이 실제로 렌더.
  - 제품 타이핑 검색: 부분일치·초성·공백무시·무매치 경고.
  - 선택 흐름: 마켓 미선택 → 안내 / 마켓 선택 → 범위 / '특정 제품' → 검색+제품 드롭다운.

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
                "instagram": {"scent": "레몬향 진하고 상큼", "texture": "말랑·텐션 좋음",
                              "shipping": "포장 꼼꼼하다는 언급", "pros": ["향 지속력", "포장"], "cons": []},
                # 디시는 배송 섹션만 있고 질감은 빈칸 — 소스별로 채워지는 섹션이 다름을 검증
                "dcinside": {"scent": "인공적 레몬향 지적", "texture": None,
                             "shipping": "배송 3일 지연 반복 지적", "pros": [], "cons": ["배송 지연", "흐름성 강함"]},
                "integrated": {"scent": "양쪽 레몬 인지, 인스타 호평/디시 인공향(gap 0.8)", "texture": "인스타만 평가",
                               "shipping": "인스타는 포장, 디시는 지연 — 갈림",
                               "pros": ["[공통] 향 존재감"], "cons": ["[디시] 배송·흐름성"]},
            },
            # 서포터(홍보성) 버킷도 향/질감/배송·CS/장단점 섹션 형태로 포함(소수라도 표시).
            "promo_view": {"n_promo": 1, "scent": "레몬마들렌향 은은", "texture": "매트·포닥",
                           "shipping": None, "pros": ["발색·향"], "cons": []},
            # 근거 원문 — 실사용/서포터 분리. 디시 그룹은 앵커가 없어 글+댓글이 한 줄로 묶인다.
            "sources": {
                "genuine": [{"url": "https://gall.dcinside.com/mgallery/board/view/?id=amos&no=201513",
                             "platform": "dcinside", "n_pieces": 3, "n_posts": 1,
                             "n_comments": 2, "label": "글 1건 + 댓글 2건"}],
                "promo": [{"url": "https://www.instagram.com/p/PROMO1/", "platform": "instagram",
                           "n_pieces": 1, "n_posts": 1, "n_comments": 0, "label": "글 1건"}],
            },
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
        "market_mode": {   # 마켓 단위 뷰 — official_spec 없음 → 스펙 블록 자체 생략
            "official_spec": None,
            "n_reviews": 4,
            "by_source": {"dcinside": {"net": -0.5, "pos": 0, "neu": 2, "neg": 2, "n": 4}},
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
    assert any("**배송·CS**" in m and "배송 3일 지연 반복 지적" in m for m in mds), "배송·CS 섹션 미렌더"
    # promo_view.shipping=None · single_source 는 shipping 키 자체 없음 → 빈 섹션은 통째 생략
    assert sum("**배송·CS**" in m for m in mds) == 3, "빈 배송 섹션이 패딩으로 렌더됨"
    assert any("공식 스펙 출처" in c and "instagram.com/p/ABC" in c for c in caps), "스펙 URL 링크 미렌더"
    assert any("🎁 서포터 리뷰" in m for m in mds), "서포터 리뷰 블록 미렌더"
    assert any("레몬마들렌향 은은" in m for m in mds), "서포터 향 섹션 미렌더"

    # --- 근거 원문 목록: 링크 있는 뷰(full)에만, 실사용/서포터 버킷 분리 ---
    assert sum("🔗 근거 원문" in s for s in subs) == 1, "근거 원문 섹션은 sources 있는 뷰에만 떠야 한다"
    assert any("글 1건 + 댓글 2건" in m and "no=201513" in m for m in mds), \
        "디시 근거 그룹(글+댓글 구성 라벨 + 원문 링크) 미렌더"
    assert any("서포터 후기" in m and "원문 1건" in m for m in mds), "서포터 근거 버킷 미렌더"
    assert any("실사용 후기" in m and "원문 1건" in m for m in mds), "실사용 근거 버킷 미렌더"
    # 정직성 캡션 2종 — 링크를 다는 순간 필요해지는 고지라 링크와 같은 화면에 있어야 한다.
    assert any("원문 인용이 아니라" in c for c in caps), "evidence≠인용 캡션 미렌더"
    assert any("근거 목록은 원문 조각 수" in c and "댓글" in c for c in caps), "건수 단위 캡션 미렌더"
    # 편향 라벨(긍정/부정 쏠림)은 제거됨 — 어디에도 나오면 안 됨.
    alltext = mds + caps + subs
    assert not any("쏠림" in t for t in alltext), "쏠림 라벨이 아직 렌더됨"
    # 요약 헤더는 요약/서포터 있는 뷰 2개(full, single_source)에만, no_summaries 뷰엔 생략.
    n_hdr = sum("리뷰 요약" in s for s in subs)
    assert n_hdr == 2, f"요약 헤더 {n_hdr}개 (기대 2)"
    # 스펙(1층) 블록은 별도 헤더로 분리 렌더 — spec 있는 뷰 3개에만, market_mode(스펙 None)엔 생략.
    n_spec_hdr = sum("공식 스펙 (1층" in s for s in subs)
    assert n_spec_hdr == 3, f"스펙 헤더 {n_spec_hdr}개 (기대 3 — market_mode 는 생략)"
    print("✓ UI 렌더: 예외 0 · 3블록 · 서포터 블록 · URL · 스펙 분리 헤더 · 쏠림 라벨 제거 OK")


# ---------------------------------------------------------------- 제품 타이핑 검색
def _products() -> list[dict]:
    """1층 fixture 와 같은 모양의 제품 리스트(빈짱 마켓)."""
    return [{"product": "한글과자한줌", "official_scent": "과자향", "base_combo": "플레인",
             "slime_type": "버터", "beads": [], "source_permalink": None},
            {"product": "빠삭귤", "official_scent": "귤향", "base_combo": None,
             "slime_type": "클라우드", "beads": ["6mm"], "source_permalink": None}]


def test_filter_products():
    from app.ui import filter_products
    names = lambda q: [p["product"] for p in filter_products(_products(), q)]

    assert names("") == ["한글과자한줌", "빠삭귤"], "빈 질의는 전체"
    assert names("한줌") == ["한글과자한줌"], "부분일치 실패"
    assert names("한글 과자") == ["한글과자한줌"], "공백 무시 매칭 실패"
    assert names("ㅎㄱㄱㅈ") == ["한글과자한줌"], "초성 검색 실패"
    assert names("ㅃㅅ") == ["빠삭귤"], "쌍자음 초성 검색 실패"
    assert names("없는제품") == [], "무매치인데 결과가 나옴"
    print("✓ 제품 검색: 부분일치·공백무시·초성·무매치 OK")


# ---------------------------------------------------------------- 선택 흐름
def _selection_script() -> None:
    """AppTest 스크립트: DB 없이 render_selection 만 — 제품 조회는 가짜 콜러블 주입."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import streamlit as st
    from app.ui import render_selection
    from eval.test_ui_render import _products
    sel = render_selection(["빈짱", "봄슬라임"], lambda market: _products())
    st.markdown(f"SEL market={sel['market']} product={sel['product']} scope={sel['scope']}")


def test_selection_flow():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_selection_script).run(timeout=30)
    assert not at.exception, at.exception

    # ① 마켓 미선택 → 안내만, 범위/제품 위젯 없음
    assert at.selectbox[0].label == "마켓"
    assert len(at.selectbox) == 1 and not at.radio, "마켓 전이라 범위·제품이 이미 떠 있음"
    assert any("마켓을 먼저" in i.value for i in at.info), "마켓 선택 안내 없음"

    # ② 마켓 선택 → 범위 라디오(기본 '마켓 전체'), 제품 위젯은 아직 없음
    at = at.selectbox[0].select("빈짱").run(timeout=30)
    assert not at.exception, at.exception
    assert at.radio[0].value == "마켓 전체"
    assert not at.text_input, "'마켓 전체' 범위인데 제품 검색창이 떴음"
    assert any("market=빈짱 product=None" in m.value for m in at.markdown), "마켓 선택 미반영"

    # ③ '특정 제품' → 검색창 + 제품 드롭다운(전체 2개)
    at = at.radio[0].set_value("특정 제품").run(timeout=30)
    assert not at.exception, at.exception
    assert at.text_input[0].label == "제품 검색"
    assert at.selectbox[1].label == "제품 (2개)", at.selectbox[1].label

    # ④ 초성 타이핑 → 후보 1개로 좁힘 → 선택하면 그 제품이 반환값에 실린다
    at = at.text_input[0].set_value("ㅎㄱㄱㅈ").run(timeout=30)
    assert at.selectbox[1].label == "제품 (1개)", at.selectbox[1].label
    at = at.selectbox[1].select("한글과자한줌").run(timeout=30)
    assert not at.exception, at.exception
    assert any("product=한글과자한줌" in m.value for m in at.markdown), "제품 선택 미반영"

    # ⑤ 무매치 → 경고 + 제품 드롭다운 없음(선택 불가 상태를 숨기지 않는다)
    at = at.text_input[0].set_value("없는제품").run(timeout=30)
    assert not at.exception, at.exception
    assert any("맞는 제품이 없습니다" in w.value for w in at.warning), "무매치 경고 없음"
    assert len(at.selectbox) == 1, "무매치인데 제품 드롭다운이 남아 있음"
    print("✓ 선택 흐름: 마켓→범위→제품 검색(초성)→선택→무매치 경고 OK")


if __name__ == "__main__":
    test_ui_render_no_exception_and_blocks()
    test_filter_products()
    test_selection_flow()
    print("\nUI 렌더·선택 헤드리스 테스트 통과 ✅")
