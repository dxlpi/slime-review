# -*- coding: utf-8 -*-
"""KB **미등재** 마켓 토큰 오버레이 — *채우지 않고 막는다*.

아모스갤에서 마켓 자리로 실제 쓰이는데 KB 에 없는 초성이 코퍼스의 큰 덩어리다(감사 D3 105행).
지금 벌어지는 일은 '통째로 NULL' 이 아니라 **더 나쁜 쪽**이다: 제품명이 우연히 다른 마켓의
1층과 겹치는 행만 그 마켓으로 채워져 **한 글 안에서 마켓이 갈린다**(ROW#1190 `진저크런키` →
지나). 오버레이의 일은 그 추론을 끄는 것뿐이다.

지키는 문장 넷:
  · 미등재 접두는 **떼어지되** 마켓은 NULL 이다.
  · 미등재 조각에서 **역인덱스가 안 돈다**(이름 충돌 차단).
  · 미등재 제목 스레드에서 **상속이 안 걸린다**.
  · 완성형 항목은 **거부**된다 — 아니면 진짜 제품명이 접두로 잘려 나간다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_unregistered_markets`
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slime_rag import linking                        # noqa: E402

OVERLAY = ROOT / "data" / "unregistered_market_tokens.json"
UNREG = frozenset({"ㅅㄱㄷ", "ㅍㅍㄹ", "ㅋㅋㅁ"})


def _kb() -> linking.KB:
    def m(word, cho):
        return {"market": word + "슬라임", "market_word": word, "handle": word,
                "handles_alt": [], "aliases": [], "choseong": cho,
                "choseong_aliases": [], "products": []}
    return linking.KB({"markets": [m("예찬", "ㅇㅊ"), m("지나", "ㅈㄴ"), m("베이퍼", "ㅂㅇㅍ")]})


KB = _kb()
INV = linking.build_market_inversion([("지나", "진저크런키")], {}, excludes=[])


def test_the_canonical_overlay_file_is_wellformed():
    """정본 파일이 존재하고 **자모 전용**이며 원문 바이트가 없다(커밋 대상, ADR-0013)."""
    data = json.loads(OVERLAY.read_text(encoding="utf-8"))
    tokens = data["tokens"]
    for tok, meta in tokens.items():
        assert linking._ALL_JAMO_RE.match(tok), f"완성형 항목: {tok}"
        assert set(meta) <= {"rows", "pieces", "confirmed", "why"}, meta
    print(f"✓ 오버레이 파일 형식 OK ({len(tokens)}토큰)")


def test_the_overlay_never_lists_a_token_the_kb_already_knows():
    """⛔ **이미 KB 마켓인 초성을 '미등재'라고 적어 두지 말 것.**

    실측(2026-08-11): 씨앗으로 삼은 감사 스크립트의 `UNKNOWN_MARKET_TOKENS` 23개 중
    **22개가 이미 KB 마켓**이었다 — 그 목록은 KB 가 14마켓이던 시점에 손으로 모은 것이고,
    지금 KB 는 38마켓이다. 남겨 두면 두 가지가 깨진다:
      ① 사람 검수 큐가 이미 끝난 일로 오염된다.
      ② '이 토큰은 미등재'라는 파일의 **주장 자체가 거짓**이 된다 — 그리고 코드상으로는
        아무 일도 안 일어나므로(`markets_in_text` 는 KB 를 먼저 본다) 조용히 틀린 채 남는다.
    실측 결과는 `now_registered` 에 남긴다 — 지우면 다음 사람이 같은 조사를 다시 한다.
    """
    data = json.loads(OVERLAY.read_text(encoding="utf-8"))
    kb = linking.load_kb()
    cho, _surf = linking._scan_tables(kb)
    stale = sorted(set(data["tokens"]) & set(cho))
    assert not stale, f"KB 가 이미 아는 초성이 미등재 목록에 있다: {stale}"
    assert data["now_registered"], "실측 기록(now_registered)이 비었다"
    print(f"✓ 미등재 목록에 KB 마켓 없음 OK "
          f"(등록 확인 {len(data['now_registered'])} · 잔여 미등재 {len(data['tokens'])})")


def test_unregistered_prefix_is_split_but_the_market_stays_null():
    """`ㅅㄱㄷ UFO머핀` — 접두는 떨어지고 제품명은 살고, **마켓은 NULL**(D7 4행이 여기서 끝난다)."""
    assert linking.split_market_prefix("ㅅㄱㄷ UFO머핀", KB, UNREG) == ("ㅅㄱㄷ", "UFO머핀")
    # 오버레이가 없으면 접두가 안 떨어진다 — 이 케이스가 무언 통과가 아님을 고정한다.
    assert linking.split_market_prefix("ㅅㄱㄷ UFO머핀", KB) == (None, "ㅅㄱㄷ UFO머핀")
    r = linking.link(None, "ㅅㄱㄷ UFO머핀", kb=KB, unregistered=UNREG)
    assert r.product == "UFO머핀", r
    assert r.market is None, f"미등재 토큰이 마켓을 채웠다: {r.market}"
    print("✓ 미등재 접두 분리 + 마켓 NULL OK")


def test_the_overlay_never_fills_a_market():
    """⛔ 오버레이가 마켓을 **채우는** 일은 절대 없다 — 채우려면 KB 등록(사람 입력)이 필요하다."""
    for name in ("ㅋㅋㅁ 진저크런키", "ㅍㅍㄹ 애플크림머핀", "ㅅㄱㄷ"):
        r = linking.link(None, name, kb=KB, unregistered=UNREG, inversion=INV)
        assert r.market is None, f"{name} → {r.market} (오버레이가 채웠다)"
    print("✓ 오버레이는 채우지 않는다 OK")


def test_a_name_collision_no_longer_leaks_through_the_inversion():
    """**D2c 진저크런키 재현.** 스레드는 `ㅋㅋㅁ` 후기인데 이름이 지나 1층과 겹친다.

    ⚠️ **접두 분리로는 못 막힌다** — 그 행의 제품명엔 접두가 없었다(`진저크런키` 뿐). 미등재
      신호는 조각 본문/제목에만 있으므로 차단도 **스레드 스코프**여야 한다. 차단이 없으면
      그 행만 지나로 채워지고 같은 글의 다른 제품은 전부 NULL 이라 **한 글 안에서 마켓이
      갈린다** — 화면엔 원인이 안 보인다.
    """
    def _doc():
        return {"market": None, "_thread_title": "ㅋㅋㅁ 첫굼 후기",
                "reviews": [{"mentioned_product": "진저크런키"},
                            {"mentioned_product": "말차수플레"}]}

    leaked = [r.market for r in linking.link_post(_doc(), kb=KB, inversion=INV,
                                                  unregistered=frozenset())]
    assert leaked == ["지나", None], f"전제 붕괴 — 차단 없이도 안 새어 나간다: {leaked}"
    doc = _doc()
    blocked = [r.market for r in linking.link_post(doc, kb=KB, inversion=INV,
                                                   unregistered=UNREG)]
    assert blocked == [None, None], f"이름 충돌이 새어 나갔다: {blocked}"
    assert doc.get("_inversion_blocked_by_unregistered") == ["ㅋㅋㅁ"], doc
    print("✓ 이름 충돌 → 스레드 스코프 역인덱스 차단 OK")


def test_the_thread_block_does_not_fire_when_a_kb_market_is_named():
    """⚠️ **과잉 차단 금지.** 미등재 얘기가 스쳐 지나가도 KB 마켓을 지목했으면 차단하지 않는다.

    되돌린 행 스코프 가드의 실패 모드가 정확히 이것이다 — 비교글은 마켓이 여럿 등장하는 게
    정상인데 그걸 모순으로 읽었다. 막는 쪽 손실은 화면에 안 보인다.
    """
    # ⚠️ 제목의 KB 표기는 **모호 토큰이 아니어야** 한다 — `ㅈㄴ` 은 부사 '존나'와 겹쳐
    #   단독으로는 `noisy` 이지 `unique` 가 아니다(그 자체가 별도 계약이다).
    doc = {"market": None, "_thread_title": "ㅂㅇㅍ 후기인데 ㅋㅋㅁ 것도 껴 있음",
           "reviews": [{"mentioned_product": "진저크런키"}]}
    got = linking.link_post(doc, kb=KB, inversion=INV, unregistered=UNREG)[0]
    assert got.market == "지나", f"KB 마켓을 지목했는데 차단됐다: {got.reason}"
    assert "_inversion_blocked_by_unregistered" not in doc
    print("✓ KB 마켓 동반 시 차단 미발동(과잉 차단 방지) OK")


def test_an_unregistered_title_stops_the_thread_inheritance():
    """미등재 토큰만 지목하는 제목에서는 상속이 안 걸린다(ROW#1111 `ㅍㅍㄹ 애플크림머핀`→예찬)."""
    doc = {"market": "예찬", "_market_inherited": True,
           "_thread_title": "ㅍㅍㄹ 첫 후기임", "reviews": [{"mentioned_product": "애플크림머핀"}]}
    r = linking.link_post(doc, kb=KB, unregistered=UNREG)[0]
    assert r.market is None, f"미등재 제목 스레드에서 상속이 걸렸다: {r.market}"
    assert doc.get("_inherit_blocked_by_title") == "예찬"
    print("✓ 미등재 제목 → 상속 미발동 OK")


def test_the_loader_rejects_precomposed_entries_and_says_so():
    """⚠️ **완성형 거부.** 통과시키면 `_market_token` 이 그것을 접두로 인정해 제품명을 자른다.

    `split_market_prefix` 가 완성형 초성 환원을 금지한 이유(`포도`→푸딩 · `배`→봄 ·
    `육쩐`→연찌 사고)를 오버레이가 우회하는 구멍이 된다. 조용히 버리지 않고 경고한다.
    """
    import logging
    original = linking.UNREGISTERED_MARKET_TOKENS_PATH
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "tokens.json"
        p.write_text(json.dumps({"tokens": {"ㅅㄱㄷ": {}, "포도": {}, "ㅇㅊ 감": {}}}),
                     encoding="utf-8")
        linking.UNREGISTERED_MARKET_TOKENS_PATH = p
        try:
            records = []
            handler = logging.Handler()
            handler.emit = records.append
            linking.log.addHandler(handler)
            try:
                got = linking.load_unregistered_market_tokens()
            finally:
                linking.log.removeHandler(handler)
        finally:
            linking.UNREGISTERED_MARKET_TOKENS_PATH = original
    assert got == frozenset({"ㅅㄱㄷ"}), got
    assert any("자모 전용" in r.getMessage() for r in records), \
        "완성형 거부가 조용히 일어났다(경고 없음)"
    print("✓ 완성형 항목 거부 + 경고 OK")


def test_a_missing_or_broken_file_degrades_safely():
    """파일이 없거나 깨져도 죽지 않는다 — 수집 경로 한복판에서 불린다.

    ⚠️ 여기서 빈 집합은 '오버레이 없음' = **예전 동작**이다. `load_market_inversion_excludes`
      와 달리 안전한 방향인데(막던 게 안 막히는 게 아니라, 떼던 게 안 떨어질 뿐), 그래도
      조용히 넘기지 않는다.
    """
    original = linking.UNREGISTERED_MARKET_TOKENS_PATH
    with tempfile.TemporaryDirectory() as d:
        linking.UNREGISTERED_MARKET_TOKENS_PATH = Path(d) / "nope.json"
        try:
            assert linking.load_unregistered_market_tokens() == frozenset()
            broken = Path(d) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            linking.UNREGISTERED_MARKET_TOKENS_PATH = broken
            assert linking.load_unregistered_market_tokens() == frozenset()
        finally:
            linking.UNREGISTERED_MARKET_TOKENS_PATH = original
    print("✓ 파일 부재/파손 안전 degrade OK")


def test_the_overlay_does_not_widen_the_precomposed_prefix_ban():
    """오버레이가 있어도 완성형 제품명은 여전히 안 잘린다(`포도`≠푸딩 회귀)."""
    wide = UNREG | {"ㅍㄷ"}
    assert linking.split_market_prefix("포도", KB, wide) == (None, "포도")
    assert linking.split_market_prefix("요아곰 밀키크림파르페", KB, wide)[0] is None
    print("✓ 완성형 접두 금지 유지 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
