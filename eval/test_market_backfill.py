# -*- coding: utf-8 -*-
"""마켓 백필 위생 계약(계획 Phase 1.4) — **채운 흔적을 남기는가**.

`market_confidence` 는 이 저장소에서 confidence 이자 **provenance** 다. `reason` 은 DB 에
안 남으므로, 어떤 경로가 그 마켓을 채웠는지 사후에 아는 방법이 이 숫자 하나뿐이다.
그래서 지켜야 할 문장이 셋이다:
  · 채우면 **반드시** 확신도를 함께 쓴다(0.0 으로 두면 '채웠는데 표식 없음'이 된다).
  · 경로마다 **값이 겹치지 않는다**(겹치면 그 층만 골라 되돌릴 수 없다).
  · 마켓을 바꾸면 `evidence`·`tokens`·`embedding` 을 **함께** 다시 만든다
    (`render_review` 가 마켓을 검색 텍스트에 굽는다).

무네트워크·무LLM·무DB(소스 검사 + 상수 검사). 실행: `python -m eval.test_market_backfill`
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slime_rag import linking, pipeline                # noqa: E402


# ⚠️ **열거 게이트의 목록은 여기 한 곳이다.** 신규 복구 함수를 여기 안 넣으면 그 함수만
#   규율(dry_run 기본 · LLM 0회 · 마켓 변경 시 3종 재생성) 밖에 남고, 그 사실이 아무 데도
#   안 드러난다. 목록을 함수마다 흩어 두면 셋 중 하나에만 빠뜨리는 일이 반드시 생긴다.
DRY_RUN_FNS = (
    pipeline.backfill_review_markets, pipeline.backfill_market_confidence,
    pipeline.repair_evidence_headers, pipeline.backfill_market_from_product,
    pipeline.repair_dc_attribution,
    pipeline.backfill_dc_market_priority, pipeline.revert_dc_market_priority,
    pipeline.revert_overlay_blocked_fills, pipeline.revert_market_inversion,
    pipeline.backfill_product_aliases, pipeline.backfill_review_bodies,
    pipeline.backfill_sentiment_axis, pipeline.derive_dc_canonical_candidates,
)
# 마켓이 바뀌는 경로만. `backfill_market_confidence` 는 마켓을 안 건드리므로 제외이고
# (`render_review` 는 확신도를 굽지 않는다), 사이드카·원문 백필도 마켓 밖이다.
RE_RENDER_FNS = (
    pipeline.backfill_review_markets, pipeline.repair_evidence_headers,
    pipeline.backfill_market_from_product, pipeline.backfill_dc_market_priority,
    pipeline.revert_dc_market_priority, pipeline.revert_overlay_blocked_fills,
    pipeline.revert_market_inversion, pipeline.backfill_product_aliases,
)


def test_backfill_confidences_are_distinct_from_every_other_path():
    """⛔ **값 충돌 금지.** 겹치면 `WHERE market_confidence = ANY(...)` 로 그 층만 못 고른다."""
    others = {0.95, 0.85,                                   # 직접 매칭(표면형·초성)
              *linking.PREFIX_CONFS,                        # 제품명 접두
              *linking.INVERSION_CONFS,                     # 제품→마켓 역인덱스
              *linking.REPAIR_PIECE_CONFS,                  # 조각 본문 스캔
              pipeline.REVERT_SENTINEL_CONF}                # 되돌리기 센티널
    for why, conf in pipeline.BACKFILL_CONFS.items():
        assert conf not in others, f"{why}({conf}) 가 다른 경로와 값이 겹친다"
    assert len(set(pipeline.BACKFILL_CONFS.values())) == len(pipeline.BACKFILL_CONFS), \
        "백필 근거끼리도 값이 겹친다 — 근거별로 못 되돌린다"
    print("✓ 백필 확신도가 모든 경로와 구분됨 OK")


def test_backfill_confidences_sit_above_the_abstain_line():
    """채웠는데 확신도가 보류선 아래면 '보류가 아닌데 보류값'이라는 모순 행이 남는다."""
    from slime_rag.config import settings
    for why, conf in pipeline.BACKFILL_CONFS.items():
        assert conf > settings.link_abstain_threshold, f"{why}({conf}) 가 보류선 이하"
        assert conf < 0.95, f"{why}({conf}) 가 직접 매칭 이상 — 근거가 더 약한데 값이 더 높다"
    # 1층 유일소유가 캡션 언급보다 강한 근거다(판매자 본인 글 vs 같은 글의 여러 마켓 중 하나).
    assert pipeline.BACKFILL_CONF_SPEC > pipeline.BACKFILL_CONF_CAPTION
    print("✓ 백필 확신도 눈금 배치(보류선 위 · 직접 매칭 아래 · 1층>캡션) OK")


def test_market_fill_writes_the_confidence_column():
    """마켓을 채우는 UPDATE 가 `market_confidence` 를 **같이** 쓴다.

    예전엔 안 썼다 — 그래서 273행이 '마켓은 있는데 확신도 0' 이라는 모순 상태로 남았고,
    그중 어느 것이 백필분인지 사후에 가를 수 없었다.
    """
    src = inspect.getsource(pipeline.backfill_review_markets)
    assert "market_confidence=%s" in src, "마켓 백필이 확신도를 안 쓴다(표식 없는 행이 생긴다)"
    assert "BACKFILL_CONFS[" in src, "확신도를 근거별 상수로 안 고른다"
    print("✓ 마켓 백필이 확신도를 함께 기록 OK")


def test_market_change_always_re_renders_evidence_tokens_and_embedding():
    """마켓이 바뀌는 경로는 셋을 **함께** 다시 만든다 — 하나만 고치면 인용과 검색이 갈린다."""
    for fn in RE_RENDER_FNS:
        src = inspect.getsource(fn)
        for col in ("evidence=%s", "tokens=%s", "embedding=%s"):
            assert col in src, f"{fn.__name__} 가 {col} 를 다시 안 만든다"
        assert "render_review" in src and "embed(" in src, \
            f"{fn.__name__} 가 렌더·임베딩을 다시 안 한다"
    print("✓ 마켓 변경 시 evidence·tokens·embedding 동시 재생성 OK")


def test_confidence_only_backfill_does_not_touch_the_market():
    """확신도 백필은 **값을 지어내지 않는다** — 저장된 마켓을 그대로 두고 근거만 재도출한다.

    ⚠️ 재도출이 저장값과 엇갈리면 건드리지 않는다. 확신도 백필이 오귀속에 승인 도장을
      찍어 주면, provenance 칸이 거짓말을 시작한다.
    """
    src = inspect.getsource(pipeline.backfill_market_confidence)
    assert "SET market_confidence=%s WHERE id=%s" in src, \
        "확신도 백필이 다른 칸까지 건드린다"
    assert "market=" not in src.split("UPDATE reviews")[1].split(";")[0], \
        "확신도 백필이 market 을 덮어쓴다"
    assert "conflicts" in src and "no_evidence" in src, \
        "엇갈림·무근거를 카운트로 안 드러낸다(무음 스킵 금지)"
    print("✓ 확신도 백필: 마켓 무변경 · 엇갈림 미승인 OK")


def test_every_repair_defaults_to_dry_run():
    """유료도 아니고 되돌리기도 어려운 쓰기는 **기본이 계획 출력**이어야 한다."""
    for fn in DRY_RUN_FNS:
        sig = inspect.signature(fn)
        assert sig.parameters["dry_run"].default is True, f"{fn.__name__} 의 dry_run 기본값이 True 가 아니다"
        assert sig.parameters["dry_run"].kind is inspect.Parameter.KEYWORD_ONLY, \
            f"{fn.__name__} 의 dry_run 이 위치 인자다(실수로 켜질 수 있다)"
    print("✓ 모든 복구 함수 dry_run 기본 · 키워드 전용 OK")


def test_repairs_spend_no_llm_calls():
    """이 경로는 전부 무과금이어야 한다 — 재임베딩은 로컬 BGE-M3 다."""
    for fn in DRY_RUN_FNS:
        src = inspect.getsource(fn)
        for paid in ("LLM(", "llm.complete", "extract_review", "extract_spec"):
            assert paid not in src, f"{fn.__name__} 가 유료 호출을 한다: {paid}"
    print("✓ 복구 경로 LLM 0회 OK")


def test_the_body_scan_conflict_guard_stays_out():
    """⛔ **되돌린 자리 — 옛 소비처에서의 부활은 계속 금지.**

    되돌린 것은 `backfill_review_markets` 안의 ***행 스코프 부정 가드***다: '본문이 다른
    마켓을 말했으면 이미 정해진 채움을 거부하라'. 실패 모드는 "비교글에 마켓이 여럿 나오는
    건 정상인데 그중 하나를 제품명으로만 가리킨 것을 모순으로 읽었다"이고, 실측 3건 전부
    채운 값이 **맞았다**(`빠코볼, ㅂㅇㅍ 빨대 이런거였음` · `ㅅㅈㄴ` = 별칭 `슬지나` 의
    초성이라 그 글이 곧 지나 글). 막는 쪽 손실은 화면에 안 보인다.

    2026-08-11 에 **조각 스코프 긍정 스캐너**(`linking.markets_in_text`)가 생겼다. 스코프도
    방향도 다르고, 무엇보다 **기수성**이 그 구분을 기계적으로 보장한다 — 그 결과는 `unique`
    가 정확히 하나일 때만 근거가 되므로, 옛 가드를 깨뜨린 바로 그 입력(마켓을 여럿 나열하는
    비교글)에서 **아무것도 만들지 않고 그냥 통과한다**(게이트:
    `eval/test_market_scan.py::test_a_comparison_post_yields_no_single_market`).

    그래서 이 게이트는 셋을 지킨다:
      ① 옛 소비처에서의 부활 금지 — 되돌린 판정은 여전히 유효하다.
      ② `hasattr` 금지는 해제하되 **죽은 코드 금지의 의도는 소비처 강제로 대체**한다.
      ③ 되돌림의 진짜 교훈 — "**막는 판단을 조용히 하지 마라**" — 을 집행한다.
    """
    for fn in (pipeline.backfill_review_markets, pipeline.backfill_market_from_product):
        assert "markets_in_text" not in inspect.getsource(fn), \
            f"{fn.__name__} 에 되돌린 본문 스캔 가드가 되살아났다"
    assert hasattr(linking, "markets_in_text"), "조각 스코프 스캐너가 없다"
    for fn in (linking.link_post, pipeline.dc_market_target):
        assert "markets_in_text" in inspect.getsource(fn), \
            f"{fn.__name__} 가 스캐너를 안 쓴다 — 스캐너가 소비처 없이 남았다(죽은 코드)"
    # ③ 조용한 차단 금지 — 충돌은 목록으로도 나가고 로그로도 남는다.
    src = inspect.getsource(pipeline.backfill_dc_market_priority)
    assert "conflict_list" in src and "log." in src, \
        "충돌 판정이 사람 목록으로 안 나온다 — 막는 쪽 손실은 화면에 안 보인다"
    print("✓ 본문 스캔: 옛 소비처 미부활 · 새 스캐너 소비처 강제 · 조용한 차단 금지 OK")


def test_no_revert_path_writes_a_confidence_any_backfill_selector_matches():
    """⛔ **되돌리기가 되돌려지지 않는 선재 결함** — 되돌린 행이 다음 백필에 재무장된다.

    `revert_market_inversion` 은 `market_confidence=0` 으로 되돌렸는데
    `backfill_market_from_product` 의 선택자가 `coalesce(market_confidence,0)=0::real` 이다.
    실측(2026-08-11): amos 에서 그 선택자에 걸리는 행이 171개다. 되돌리기가 셋으로 늘어나면
    표면적도 3배가 된다 — 그래서 **센티널**을 쓴다.
    """
    revert_fns = (pipeline.revert_market_inversion, pipeline.revert_overlay_blocked_fills,
                  pipeline.revert_dc_market_priority)
    for fn in revert_fns:
        src = inspect.getsource(fn)
        assert "market_confidence=0," not in src and "market_confidence = 0" not in src, \
            f"{fn.__name__} 가 0 으로 되돌려 백필 선택자를 재무장한다"
        assert "REVERT_SENTINEL_CONF" in src, f"{fn.__name__} 가 센티널을 안 쓴다"
    from slime_rag.config import settings
    assert pipeline.REVERT_SENTINEL_CONF != 0
    assert pipeline.REVERT_SENTINEL_CONF < settings.link_abstain_threshold, \
        "센티널이 보류선 위면 '마켓을 못 정했다'는 뜻이 사라진다"
    # 선택자와의 비교차 — 센티널이 어떤 백필의 기본 WHERE 에도 안 걸린다.
    sel = inspect.getsource(pipeline.backfill_market_from_product)
    assert "include_reverted" in sel, "되돌린 행 재백필이 명시 인자로 안 막혀 있다"
    assert inspect.signature(pipeline.backfill_market_from_product) \
        .parameters["include_reverted"].default is False, "기본이 재무장이다"
    print("✓ 되돌리기 센티널(재무장 금지) OK")


def test_rollback_predicate_casts_to_real_including_the_sentinel():
    """⚠️ `0.01` 은 REAL 로 **정확히 표현되지 않는다** — `::real` 캐스트가 빠지면 0행이다.

    `INVERSION_ROLLBACK_WHERE` 가 겪은 함정과 같은 자리이고, 조용히 빈 결과라
    '되돌릴 게 없다'와 구분되지 않는다.
    """
    import struct
    assert struct.unpack("f", struct.pack("f", pipeline.REVERT_SENTINEL_CONF))[0] \
        != pipeline.REVERT_SENTINEL_CONF, \
        "센티널이 REAL 로 정확히 표현된다 — 이 케이스의 전제가 바뀌었으니 다시 볼 것"
    assert "::real" in pipeline.INVERSION_ROLLBACK_WHERE
    sel = inspect.getsource(pipeline.backfill_market_from_product)
    assert "%s::real" in sel, "센티널 비교에 캐스트가 없다 — 조용히 0행"
    print("✓ 센티널 ::real 캐스트 OK")


def test_repair_ledgers_live_outside_dot_omc():
    """⛔ **원장이 `.omc/` 아래면 세션 수명이다** — `.gitignore` 가 통째로 무시한다.

    그러면 '되돌릴 수 있다'는 주장이 실제로는 거짓이다(기실행된
    `backfill_non_product_labels` 의 롤백 주장도 이미 그 상태였다). 원장은 커밋되는
    `data/repair_ledgers/` 에만 산다. 담는 건 id·이전값·이름·시각뿐이라 ADR-0013 안전이다.
    """
    from slime_rag import repair_ledger
    p = str(repair_ledger.LEDGER_DIR)
    assert "/.omc/" not in p and not p.endswith("/.omc"), f"원장이 .omc 아래다: {p}"
    assert p.endswith("data/repair_ledgers"), p
    assert repair_ledger.FORBIDDEN_FIELDS & {"body", "evidence", "attributes"}, \
        "원장이 원문 칸을 막지 않는다"
    for fn in (pipeline.backfill_dc_market_priority, pipeline.revert_overlay_blocked_fills,
               pipeline.revert_market_inversion, pipeline.repair_dc_attribution):
        assert "repair_ledger" in inspect.getsource(fn), f"{fn.__name__} 가 원장을 안 남긴다"
    print("✓ 원장 위치·내용 계약 OK")


# ------------------------------------------------------------------ 디시 귀속 제자리 복구
def _kb() -> linking.KB:
    """가짜 KB — 디스크 KB 를 읽지 않는다(게이트가 데이터 변경에 흔들리면 안 된다)."""
    def m(word, cho):
        return {"market": word + "슬라임", "market_word": word, "handle": word,
                "handles_alt": [], "aliases": [], "choseong": cho,
                "choseong_aliases": [], "products": []}
    return linking.KB({"markets": [m("베이퍼", "ㅂㅇㅍ"), m("예찬", "ㅇㅊ"),
                                   m("모모네", "ㅁㅁㄴ"), m("봄", "ㅂ")]})


DC_KB = _kb()


def _target(product, market=None):
    return pipeline.dc_attribution_target(product, market, DC_KB)


def test_market_prefix_is_split_before_the_word_gate():
    """⛔ **순서 회귀 — 개발 중 실제로 뒤집어 짰던 자리.**

    `ㅇㅊ`·`ㅁㅁㄴ` 같은 맨몸 마켓 표기는 '자모뿐인 이름' 이기도 하다. 단어 게이트를 먼저
    걸면 제품만 비워지고 **그 안의 마켓 신호가 통째로 버려진다** — 실측으로 마켓 교정
    10건 중 8건을 놓쳤다. 접두 분리가 먼저고, 단어 게이트는 **뗀 나머지**에 건다.
    """
    target, why, market, conf = _target("ㅇㅊ")
    assert target is None, target
    assert market == "예찬", f"맨몸 마켓 표기에서 마켓을 못 건졌다: {market}"
    assert conf == linking.PREFIX_CONF_CHOSEONG
    assert why == "market_token_bare", why
    print("✓ 접두 분리 → 단어 게이트 순서 OK")


def test_split_remainder_still_goes_through_the_word_gate():
    """`ㅂㅇㅍ 빨대` 는 마켓 + **종류어**다 — 떼고 나서도 제품이 되면 안 된다."""
    target, why, market, _conf = _target("ㅂㅇㅍ 빨대")
    assert (target, market) == (None, "베이퍼"), (target, market)
    assert why == "market_token_split+non_product_word", why
    # 진짜 제품명은 떼고 나서 살아남는다.
    assert _target("ㅂㅇㅍ 프로즌딸기송이")[:1] == ("프로즌딸기송이",)
    print("✓ 접두 제거 후 나머지에도 단어 게이트 적용 OK")


def test_item_prefix_overrides_a_thread_stamped_market():
    """항목 접두는 **저장된 마켓보다 강하다** — 디시 행의 마켓은 스레드 도장이다.

    실측(2026-08-10): 스레드 201175 는 댓글 여섯이 각자 다른 마켓(`ㅇㅇㅈ`·`ㅁㅁㄴ`·
    `ㅂㅇㅍ`)을 말하는데 전부 `봄` 으로 찍혀 있었다. 남의 마켓 후기로 집계되는 건
    출처 편향(1급 기능)의 왜곡이라, NULL 보다 나쁘다.
    """
    _t, _w, market, conf = _target("ㅁㅁㄴ", market="봄")
    assert market == "모모네", f"스레드 도장이 항목 증거를 이겼다: {market}"
    assert conf in linking.PREFIX_CONFS, "덮어쓴 행에 되돌릴 표식이 없다"
    # 이미 같은 마켓이면 아무것도 안 쓴다(무의미한 UPDATE 금지).
    assert _target("ㅁㅁㄴ", market="모모네")[2] is None
    print("✓ 항목 접두 > 스레드 도장 마켓(전용 확신도 동반) OK")


def test_clean_names_are_left_alone():
    """접두도 종류어도 아닌 이름은 한 글자도 안 바뀐다."""
    assert _target("빠코볼", market="봄") == ("빠코볼", "unchanged", None, None)
    assert _target(None) == (None, "unchanged", None, None)
    print("✓ 정상 제품명 무변경 OK")


def test_dc_repair_never_deletes_rows_except_folds():
    """제품명은 NULL 이 되고 **행은 남는다** — 그 조각의 배송·CS 는 마켓 축에 남아야 한다.

    유일한 DELETE 는 접기(`drop_ids`)뿐이어야 한다.
    """
    src = inspect.getsource(pipeline.repair_dc_attribution)
    deletes = [ln for ln in src.splitlines() if "DELETE" in ln]
    assert len(deletes) == 1 and "drop_ids" in "".join(deletes), \
        f"접기 외의 삭제 경로가 있다: {deletes}"
    assert "if drop_ids:" in src, "삭제가 접기 목록으로 안 막혀 있다"
    print("✓ 디시 복구: 접기 외 삭제 없음 OK")


def test_dc_fold_key_matches_the_db_constraint():
    """접기 키는 `UNIQUE(source, post_id, product)` 와 **같아야** 한다.

    마켓을 키에 넣으면 제약이 안 보는 축으로 접게 되어 갱신이 제약 위반으로 죽는다.
    """
    src = inspect.getsource(pipeline.repair_dc_attribution)
    assert '(p["post_id"], p["to"])' in src, "이름 있는 접기 키가 제약 키와 다르다"
    assert '_held_fingerprint' in src, "보류분을 내용 지문으로 안 접는다(말더듬이 남는다)"
    assert 'p["score"]' in src, "내용 없는 보류를 접기 후보에서 안 뺐다(다른 의견이 합쳐진다)"
    print("✓ 접기 키 = DB 제약 키 · 보류분은 내용 지문 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
