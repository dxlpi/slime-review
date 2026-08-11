# -*- coding: utf-8 -*-
"""조각 스코프 마켓 스캐너(`linking.markets_in_text`) 계약.

이 함수는 **명시적으로 되돌려진 자리** 근처에 산다. 되돌린 것은
`backfill_review_markets` 안의 *행 스코프 **부정** 가드*("본문이 다른 마켓을 말했으면
채우지 마라")이고, 실측 3건이 전부 **맞는 채움**인데 막혔다. 여기는 다르다 —
**조각 스코프**에서 **채움의 근거를 만든다**(긍정). 그 구분이 궤변이 아니라는 걸
기계적으로 보장하는 게 **기수성**이다: 결과는 `unique` 가 정확히 하나일 때만 근거가 되고,
옛 가드를 깨뜨린 바로 그 입력(마켓을 여럿 나열하는 비교글)에서는 `unique` 가 둘 이상이라
아무것도 만들지 않는다. 그 성질을 아래 `test_a_comparison_post_yields_no_single_market`
이 고정한다.

무네트워크·무LLM·무DB. 실행: `python -m eval.test_market_scan`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slime_rag import linking                            # noqa: E402

BASELINE = ROOT / "evals" / "results" / "dc_attribution_baseline.json"


def _kb() -> linking.KB:
    """가짜 KB — 의미론 케이스는 디스크 KB 를 읽지 않는다(데이터 변경에 흔들리면 안 된다)."""
    def m(word, cho, *, cho_alias=(), aliases=()):
        return {"market": word + "슬라임", "market_word": word, "handle": word,
                "handles_alt": [], "aliases": list(aliases), "choseong": cho,
                "choseong_aliases": list(cho_alias), "products": []}
    return linking.KB({"markets": [
        m("지나", "ㅈㄴ", cho_alias=["ㅅㅈㄴ", "ㅅㄹㅇㅈㄴ"], aliases=["슬지나"]),
        m("봄", "ㅂ", cho_alias=["ㅂㅅㄹㅇ"]),
        m("푸딩", "ㅍㄷ"),
        m("베이퍼", "ㅂㅇㅍ"),
        m("모모네", "ㅁㅁㄴ"),
    ]})


KB = _kb()
KNOWN = frozenset({"빠코볼", "헝잭버거", "건체리크럼블"})


def scan(text, **kw):
    return linking.markets_in_text(text, KB, **kw)


def test_ambiguous_choseong_is_noise_on_its_own():
    """`ㅈㄴ` = 존나. 실측: 빠코볼 25건에 등장한 `ㅈㄴ` 6건 대부분이 부사였다."""
    r = scan("ㅈㄴ 개좋음", known_products=KNOWN)
    assert r.unique == frozenset(), r.unique
    assert "ㅈㄴ" in r.noisy
    print("✓ `ㅈㄴ 개좋음` → noisy(unique 아님) OK")


def test_ambiguous_choseong_promotes_only_before_a_known_product():
    """모호 토큰의 **유일한** 승격 경로 — 바로 뒤에 아는 제품명이 붙을 때."""
    r = scan("ㅈㄴ 빠코볼 좋더라", known_products=KNOWN)
    assert r.unique == frozenset({"지나"}), r.unique
    print("✓ `ㅈㄴ 빠코볼` → {지나} OK")


def test_promotion_needs_injected_evidence():
    """증거 미주입이면 **아무것도 승격하지 않는다**(페일세이프).

    이 상태의 의미론이 감사 스크립트 `scan_markets` 와 정확히 같다 — 그래서 감사가 만든
    숫자와 대조가 가능하다. `is_non_product_label` ③이 증거 없으면 안 지우는 것과 같은 규율.
    """
    assert scan("ㅈㄴ 빠코볼 좋더라").unique == frozenset()
    print("✓ 증거 미주입 → 승격 없음 OK")


def test_ambiguous_surface_is_noise():
    """`푸딩` 은 질감어이기도 하다 — 마켓 `푸딩` 과 겹친다."""
    r = scan("젤리나 푸딩같은 느낌", known_products=KNOWN)
    assert r.unique == frozenset(), r.unique
    assert "푸딩" in r.noisy
    print("✓ `젤리나 푸딩같은 느낌` → noisy OK")


def test_choseong_aliases_resolve():
    """`ㅅㅈㄴ`(슬지나) · `ㅂㅅㄹㅇ`(봄슬라임) — 별칭은 초성으로 환원되지 않아 KB 한 줄이 필요했다."""
    assert scan("ㅅㅈㄴ 헝잭버거").unique == frozenset({"지나"})
    assert scan("ㅅㄹㅇㅈㄴ 후기").unique == frozenset({"지나"})
    assert scan("ㅂㅅㄹㅇ 후기").unique == frozenset({"봄"})
    print("✓ 초성 별칭 해소 OK")


def test_unregistered_tokens_land_in_unknown_not_unique():
    """미등재 마켓은 **채우지 않고 막는** 재료다 — `unique` 에 절대 안 들어간다."""
    r = scan("ㅋㅋㅁ 첫굼 후기", unregistered=frozenset({"ㅋㅋㅁ"}))
    assert r.unknown == frozenset({"ㅋㅋㅁ"}), r.unknown
    assert r.unique == frozenset(), r.unique
    # 미주입이면 unknown 은 항상 빈다(오버레이가 없으면 이 기능도 없다).
    assert scan("ㅋㅋㅁ 첫굼 후기").unknown == frozenset()
    print("✓ 미등재 토큰 → unknown OK")


def test_a_comparison_post_yields_no_single_market():
    """⛔ **되돌린 가드를 깨뜨린 바로 그 입력.** 여기서 스캐너는 아무 근거도 만들지 않는다.

    `unique` 가 둘 이상이면 소비처의 사다리 1단(`정확히 하나`)이 성립하지 않아 그냥 통과한다 —
    '막는다'가 아니라 '만들지 않는다'다. 이게 조각 스코프 긍정 스캐너가 행 스코프 부정
    가드와 다르다는 **기계적** 근거다(ADR-0018 대안 (d) 기각 사유).
    """
    r = scan("빠코볼, ㅂㅇㅍ 빨대 이런거였음. ㅁㅁㄴ 도 좋았고", known_products=KNOWN)
    assert len(r.unique) > 1, r.unique
    print("✓ 비교글 → unique 다중(근거 미생성) OK")


def test_a_single_jamo_choseong_still_counts():
    """⛔ **한 글자 초성을 버리지 말 것.** `len >= 2` 필터가 있었고, 대가가 실측으로 컸다.

    현 KB 의 한 글자 초성은 `ㅂ`(봄) 하나이고, amos 801행에서 제목 19회·본문 56회 등장하는데
    **전부 마켓 표기**였다(`ㅂ 산사람중에서…` · `ㅂ슬라임 간단후기` · `ㅂ은 수분감있음`).
    버렸을 때 벌어진 일: 제목이 `ㅂ …` 인 스레드가 '제목이 마켓을 선언 안 함'으로 읽혀 상속
    권위를 잃고, 멀쩡한 행 **8개**가 NULL 되돌림 후보로 올라왔다.
    ⚠️ 자모 런은 최장 일치라 `ㅂㅇㅍ`·`ㅋㅋㅋ` 에서 한 글자가 떨어져 나오지 않는다.
    """
    kb = _kb()
    for text in ("ㅂ 산사람중에서 비매 선택한사람", "ㅂ슬라임 간단후기", "근데 ㅂ은 수분감있음"):
        assert linking.markets_in_text(text, kb).unique == frozenset({"봄"}), text
    # 더 긴 런 안에서는 한 글자가 따로 잡히지 않는다.
    assert linking.markets_in_text("슬린이 ㅂㅇㅍ 첫구매", kb).unique == frozenset({"베이퍼"})
    print("✓ 한 글자 초성(`ㅂ`=봄) 인식 OK")


def test_solo_interjection_jamo_are_demoted_to_ambiguous():
    """감탄·웃음으로 홀로 서는 자모가 마켓 초성이면 **모호 토큰으로 강등**한다.

    현 KB 엔 그런 마켓이 없지만, 하나라도 생기면 웃음 런 하나가 마켓 근거가 된다.
    강등되면 바로 뒤에 아는 제품명이 붙을 때만 승격하므로 그 경로가 막힌다.
    """
    def m(word, cho):
        return {"market": word, "market_word": word, "handle": word, "handles_alt": [],
                "aliases": [], "choseong": cho, "choseong_aliases": [], "products": []}
    fake = linking.KB({"markets": [m("크림", "ㅋ")]})
    assert linking.markets_in_text("ㅋ 아 웃겨", fake).unique == frozenset()
    assert "ㅋ" in linking.markets_in_text("ㅋ 아 웃겨", fake).noisy
    assert linking.markets_in_text("ㅋ 빠코볼 좋더라", fake,
                                   known_products={"빠코볼"}).unique == frozenset({"크림"})
    print("✓ 홀로 선 감탄 자모 강등 OK")


def test_choseong_collision_goes_to_ambiguous_not_unique():
    """초성 충돌은 갈린 증거다 — `unique` 가 아니라 `ambiguous`."""
    def m(word, cho):
        return {"market": word, "market_word": word, "handle": word, "handles_alt": [],
                "aliases": [], "choseong": cho, "choseong_aliases": [], "products": []}
    fake = linking.KB({"markets": [m("머머", "ㅁㅁ"), m("미미", "ㅁㅁ")]})
    r = linking.markets_in_text("ㅁㅁ 후기", fake)
    assert r.unique == frozenset() and sorted(r.ambiguous["ㅁㅁ"]) == ["머머", "미미"]
    print("✓ 초성 충돌 → ambiguous OK")


def test_the_scanner_is_pure():
    """DB·네트워크·LLM 무의존 — `linking` 의 계약이다(`test_linking_stays_db_free` 와 같은 자리)."""
    import inspect
    src = inspect.getsource(linking.markets_in_text) + inspect.getsource(linking._scan_tables)
    for banned in ("connect(", "requests", "LLM(", "llm_ops"):
        assert banned not in src, f"스캐너가 {banned} 를 쓴다"
    print("✓ 스캐너 순수성 OK")


def test_audit_baseline_counts_are_pinned():
    """감사가 만든 D코드 숫자는 **커밋된 기준선**이다 — 재감사 diff 의 하드 입력.

    DB 없이 CI 에서 돌게 파일로 고정한다. 이 숫자가 흔들리면 `evals/diff_audit.py` 의
    `resolved = baseline − after − folded` 가 조용히 다른 것을 재기 시작한다.

    ⚠️ **이 숫자는 KB 에 의존한다.** 계획서가 인용한 값(D3 105 · F1 78 · 🔧 179)은 KB 가
      14마켓이던 시점의 것이다. 2026-08-11 현재 KB 는 38마켓이고(감사가 '미등재'로 지목한
      초성 24개가 이름만 등록된 상태 — `handle`·`logo` 는 비어 있다), 같은 스크립트가
      **D3 3 · F1 115 · 🔧 220** 을 낸다. 감사의 **판정 규칙은 그대로**이고 입력 명부만
      늘었다: D2·D5a·D5b·D6·D7·D8·D10 은 한 건도 안 움직였다.
      → 그래서 이 케이스는 '계획서 숫자'가 아니라 **현재 KB 기준 재현값**을 고정한다.
        계획서 숫자로 되돌리려면 KB 를 되돌려야 하고, 그건 데이터 결정이지 코드 결정이 아니다.
    """
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    got = tuple(base.get(c, 0) for c in
                ("D1", "D2", "D2c", "D3", "D4", "D5a", "D5b", "D6", "D7", "D8", "D10", "F1"))
    assert got == (27, 18, 1, 3, 3, 22, 25, 54, 4, 7, 8, 115), got
    assert base["_✅ 유지"] == 538 and base["_🔧 수정"] == 220
    # 키 집합이 같이 실려 있어야 행 수 비교(접기가 분모를 줄인다)를 피할 수 있다.
    assert base["keys_by_code"]["D3"] and base["keys_kept"], "코드별 키 집합이 비었다"
    print("✓ 감사 기준선 고정 OK")


def test_kb_carries_the_four_choseong_aliases_minus_koolime():
    """KB 별칭 3줄은 들어가고 **`ㅋㄹㅇ` 은 안 들어간다**(프리모템 S2).

    쿨라임은 지나가 **인수**한 별개 마켓이라, 문자열 별칭만으로도 인수 이전 제품
    86행/59종이 지나로 끌려온 전례가 있다(MEMORY.md `koolime-pakoball-recipe-transfer`).
    초성 별칭은 그 표면적을 **넓힌다** — 스캐너·접두 분리·백필·접기 풀 네 층에 번지므로
    dry-run 으로 영향 제품명을 사람이 확인하기 전에는 넣지 않는다.
    """
    kb = json.loads((ROOT / "data" / "slime_market_kb_demo.json").read_text(encoding="utf-8"))
    by = {m["market_word"]: m for m in kb["markets"]}
    assert set(by["지나"]["choseong_aliases"]) == {"ㅅㅈㄴ", "ㅅㄹㅇㅈㄴ"}
    assert by["봄"]["choseong_aliases"] == ["ㅂㅅㄹㅇ"]
    for m in kb["markets"]:
        assert "ㅋㄹㅇ" not in (m.get("choseong_aliases") or []), \
            "`ㅋㄹㅇ` 이 사람 확인 없이 들어갔다 — 프리모템 S2"
    print("✓ KB 초성 별칭 3줄 · ㅋㄹㅇ 보류 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
