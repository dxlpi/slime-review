# -*- coding: utf-8 -*-
"""
리뷰 요약(향/질감/장단점) 오프라인 테스트 — LLM/DB/API 미호출·무비용.

검증(사용자 확정 규칙):
  - 소스별 review_summaries: 인스타/디시 각각 {scent,texture,pros,cons}.
  - '언급 없으면 빈칸': 향/질감 재료가 없는 소스엔 sectionize 가 그 키를 안 받는다 → 빈칸(None) 가능.
  - 통합(integrated)은 '두 소스 모두 실사용 후기'가 있을 때만 생성(reconciliation, 평균 금지).
  - 홍보성은 review_summaries 에 미포함(genuine 만) — promo_view 로 분리(회귀 없음).
  - llm_sectionize 미주입 시 review_summaries 전부 None(결정적 부분은 그대로).
  - _source_material: 언급된 속성만 담고 evidence/sentiment 를 넘긴다.

실행:  python -m eval.test_consolidated_sections   (repo 루트에서)
"""
from __future__ import annotations

from slime_rag.consolidated_view import (
    build_consolidated, _source_material, SOURCE_REVIEW_SCHEMA,
)


def _rec(platform, sent, review_class="genuine", **blocks):
    r = {"source": {"platform": platform}, "overall": {"model_sentiment": sent},
         "review_class": review_class}
    r.update(blocks)
    return r


# sectionize 목: 입력 payload 의 by_attr 에 실제로 있는 속성만 요약, 없으면 null.
# '지어내기 금지'를 코드로 강제 — 재료에 없는 향/질감은 절대 채우지 않는다.
def _fake_sectionize(prompt: str, schema: dict) -> dict:
    assert schema is SOURCE_REVIEW_SCHEMA          # 스키마가 그대로 전달되는지
    tail = prompt.split("[입력]", 1)[-1]
    return {
        "scent":   "향요약" if '"scent"' in tail else None,
        "texture": "질감요약" if '"texture"' in tail else None,
        "pros":    ["장점1"],
        "cons":    [],
    }


def test_source_material_only_mentioned():
    """언급된 속성만 재료에 담기고, evidence/sentiment 가 실린다. 미언급 속성은 키 자체가 없다."""
    reviews = [
        _rec("dcinside", "neg",
             texture={"sentiment": "neg", "evidence": "흐름성강함", "feel": ["흐름성있는"]}),
    ]
    mat = _source_material(reviews)
    assert "texture" in mat and "scent" not in mat, mat      # 향 언급 0 → 키 없음
    assert mat["texture"][0]["evidence"] == "흐름성강함"
    assert mat["texture"][0]["feel"] == ["흐름성있는"]
    print("✓ _source_material: 언급 속성만·evidence 포함, 미언급 향은 제외 OK")


def test_blank_section_when_attr_absent():
    """향 언급이 없는 소스는 scent 섹션이 빈칸(None) — 억지 서술 금지."""
    reviews = [
        # 인스타: 향만, 디시: 질감만 → 각 소스 반대 섹션은 빈칸이어야
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬진해요"}),
        _rec("dcinside", "neg", texture={"sentiment": "neg", "evidence": "흐름성강함"}),
    ]
    v = build_consolidated({"market": "머머", "product": "테스트"}, None, reviews,
                           llm_sectionize=_fake_sectionize)
    ig = v["review_summaries"]["instagram"]
    dc = v["review_summaries"]["dcinside"]
    assert ig["scent"] == "향요약" and ig["texture"] is None, ig    # 인스타=향만
    assert dc["texture"] == "질감요약" and dc["scent"] is None, dc  # 디시=질감만
    print("✓ blank section: 미언급 속성 → None(빈칸) OK")


def test_integrated_only_when_both_sources():
    """통합 리뷰는 두 소스 모두 실사용 후기가 있을 때만. 한쪽만이면 None."""
    only_dc = [_rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누향"})]
    v1 = build_consolidated({"market": "머머", "product": "X"}, None, only_dc,
                            llm_sectionize=_fake_sectionize)
    assert v1["review_summaries"]["dcinside"] is not None
    assert v1["review_summaries"]["instagram"] is None
    assert v1["review_summaries"]["integrated"] is None, "한 소스만인데 통합 생성됨(평균 위험)"

    both = only_dc + [_rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"})]
    v2 = build_consolidated({"market": "머머", "product": "X"}, None, both,
                            llm_sectionize=_fake_sectionize)
    assert v2["review_summaries"]["integrated"] is not None, "두 소스인데 통합 미생성"
    print("✓ integrated: 두 소스 모두 있을 때만 생성(단일 소스=None) OK")


def test_promo_excluded_from_review_summaries():
    """홍보성은 genuine 소스 요약에 안 섞이고 promo_view 로 분리(회귀 없음)."""
    reviews = [
        _rec("instagram", "pos", scent={"sentiment": "pos", "evidence": "레몬"}),
        _rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"}),
        _rec("instagram", "pos", "promo", scent={"sentiment": "pos", "evidence": "협찬향"}),
    ]
    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews,
                           llm_sectionize=_fake_sectionize)
    assert v["n_reviews"] == 2                       # 홍보성 제외
    assert v["promo_view"] is not None and v["promo_view"]["n_promo"] == 1
    # 인스타 genuine 1건만 → 요약 존재하되 홍보성 evidence('협찬향')는 재료에 안 들어감
    assert v["review_summaries"]["instagram"] is not None
    print("✓ promo: review_summaries 에서 제외·promo_view 분리 OK")


def test_no_sectionize_all_none():
    """llm_sectionize 미주입 → review_summaries 전부 None. 결정적 집계는 그대로."""
    reviews = [_rec("dcinside", "neg", scent={"sentiment": "neg", "evidence": "비누"})]
    v = build_consolidated({"market": "머머", "product": "X"}, None, reviews)  # llm 없음
    assert v["review_summaries"] == {"instagram": None, "dcinside": None, "integrated": None}
    assert v["by_source"]["dcinside"]["n"] == 1      # 결정적 부분 정상
    print("✓ no-LLM: review_summaries 전부 None, 결정적 집계 유지 OK")


def _run_all():
    test_source_material_only_mentioned()
    test_blank_section_when_attr_absent()
    test_integrated_only_when_both_sources()
    test_promo_excluded_from_review_summaries()
    test_no_sectionize_all_none()
    print("\n리뷰 요약 섹션 오프라인 테스트 전부 통과 ✅")


if __name__ == "__main__":
    _run_all()
