# -*- coding: utf-8 -*-
"""감사 산출물의 **ADR-0013 경계** — 무엇이 커밋되고 무엇이 안 되는가.

`docs/dcinside-extraction-review.md`(원본)는 조각 본문을 1,500자까지 그대로 싣는다(약 600KB).
그건 "사람에게 물어볼 선택지"가 아니라 **절대 규칙 위반**이다 — 수집한 바이트는 git 밖.
`data/product_registry.json` 이 캡션을 뺀 이유, `market_inversion_review()` 반환값을 파일로
커밋하지 말라는 규칙과 같은 자리다.

그래서 커밋되는 건 둘뿐이고, 이 게이트가 그 둘의 모양을 고정한다:
  · `docs/dcinside-extraction-review.redacted.md` — 식별자·판정·D코드·현재/제안값 +
    **≤15자 근거 스니펫**. 원문 본문 펜스 없음.
  · `evals/gold/dc_attribution_gold.json` — 사람 검수 오라클. 키는
    **`(post_id, mentioned_product)`** 다.

⛔ 골드 키를 바꾸지 말 것. `reviews.id` 는 Phase 4·6 의 접기(fold)가 지우고,
  `reviews.evidence` 는 마켓이 바뀌면 `render_review` 가 다시 굽는다(그 재렌더는
  `test_market_change_always_re_renders_evidence_tokens_and_embedding` 이 **강제한다**).
  즉 계획이 성공하는 만큼 evidence 가 바뀌어 **골드가 자기를 파괴한다** — 그리고 그 상태는
  '결함 0'과 구분되지 않는다(`INVERSION_ROLLBACK_WHERE` 의 `::real` 사고와 같은 모양).

무네트워크·무LLM·무DB. 실행: `python -m eval.test_audit_redaction`
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REDACTED = ROOT / "docs" / "dcinside-extraction-review.redacted.md"
ORIGINAL = ROOT / "docs" / "dcinside-extraction-review.md"
GOLD = ROOT / "evals" / "gold" / "dc_attribution_gold.json"

SNIPPET_MAX = 15
_BACKTICK_RE = re.compile(r"`([^`]*)`")


def test_the_raw_audit_document_is_gitignored():
    """원본은 **추적 대상이 아니다.** 잃는 게 없다 — 스크립트 + `data/raw/dc_thread` 로 $0 재생성.

    ⚠️ 계약은 **`.gitignore` 의 그 줄**이다(추적 파일이라 어디서든 읽힌다). `git check-ignore`
      는 실제 해석까지 보는 더 강한 검사지만 **git 저장소 안에서만** 돈다 — CI 재현은
      `git ls-files` 로 뽑은 트리에서 돌리므로 거기엔 `.git` 이 없다. 그래서 강한 검사는
      가능할 때만 얹고, 없으면 **눈에 보이게** 넘어간다(조용한 스킵 금지).
      이 저장소가 `test_apify_source` 에서 정확히 같은 방식으로 한 번 깨졌다.
    """
    rule = "docs/dcinside-extraction-review.md"
    lines = {ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert rule in lines, f".gitignore 에 규칙이 없다: {rule}"
    if not (ROOT / ".git").exists():
        print("✓ 감사 원본 gitignore 규칙 OK (git 저장소 밖 — check-ignore skip)")
        return
    r = subprocess.run(["git", "check-ignore", "-q", rule], cwd=ROOT)
    assert r.returncode == 0, "원문 본문 600KB 짜리 검수 문서가 gitignore 되지 않았다"
    print("✓ 감사 원본 gitignore OK (규칙 + 실제 해석)")


def test_redacted_carries_no_source_text_block():
    """편집본에 **원문 코드펜스가 없다** — 그게 원본과 편집본을 가르는 유일한 구조적 차이다."""
    assert REDACTED.exists(), f"편집본이 없다: {REDACTED}"
    text = REDACTED.read_text(encoding="utf-8")
    assert "```" not in text, "편집본에 코드펜스(원문 블록)가 있다"
    assert "**원문**" not in text, "편집본에 원문 섹션이 남아 있다"
    print("✓ 편집본에 원문 블록 없음 OK")


def test_every_redacted_snippet_stays_within_the_cap():
    """백틱 인용은 전부 `SNIPPET_MAX` 자 이하 — 1급 규칙의 '근거 스니펫 ~15자'와 같은 눈금."""
    text = REDACTED.read_text(encoding="utf-8")
    over = [s for s in _BACKTICK_RE.findall(text) if len(s) > SNIPPET_MAX]
    assert not over, f"{SNIPPET_MAX}자를 넘는 인용 {len(over)}건: {over[:5]}"
    print(f"✓ 편집본 스니펫 ≤{SNIPPET_MAX}자 OK")


def test_redacted_is_far_smaller_than_the_original():
    """크기는 증상이지 계약이 아니다 — 그래도 편집본이 원본만 해지면 뭔가 새고 있다는 뜻이다.

    원본이 로컬에 없으면(클론 직후) 이 케이스만 건너뛴다 — gitignore 된 파일에 의존하는
    검사는 CI 에서 죽는다(`test_apify_source` 의 피드 스냅샷과 같은 자리).
    """
    if not ORIGINAL.exists():
        print("· 원본 부재 — 크기 비교 skip(gitignore 대상이라 정상)")
        return
    assert REDACTED.stat().st_size * 4 < ORIGINAL.stat().st_size, \
        "편집본이 원본에 근접했다 — 본문이 새고 있을 가능성"
    print("✓ 편집본 크기 OK")


def test_gold_is_keyed_by_post_id_and_mentioned_product():
    """⛔ **키 계약.** `reviews.id`(접기가 지운다)도 `evidence`(재렌더가 바꾼다)도 아니다."""
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    assert gold["_key_fields"] == ["post_id", "mentioned_product"], gold["_key_fields"]
    entries = gold["entries"]
    assert entries, "골드가 비었다"
    for e in entries:
        assert e["key"] == f"{e['post_id']}␟{e.get('mentioned_product') or ''}", e["key"]
        assert "evidence" not in e, "골드가 evidence 를 키/값으로 들고 있다(자기 파괴 경로)"
        assert "body" not in e and "text" not in e, "골드에 원문이 들어갔다"
    print(f"✓ 골드 키 계약 OK ({len(entries)}건)")


def test_gold_covers_the_audited_defect_rows():
    """골드는 🔧 220 + ⚠️ 43 을 담는다 — ✅ 538 은 회귀 판정에 쓰이므로 카운트로만 남는다.

    ⚠️ 계획서가 인용한 값(🔧 179 · ⚠️ 128 · ✅ 494 · D3 105 · F1 78)은 KB 가 14마켓이던
      시점의 것이다. 현재 KB 는 38마켓이라 미등재로 못 채우던 행이 채울 수 있는 행으로
      옮겨 갔다(D3→F1). 판정 규칙은 그대로다 —
      D2·D5a·D5b·D6·D7·D8·D10 은 한 건도 안 움직였다.
    """
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    fixes = sum(1 for e in gold["entries"] if e["status"] == "🔧 수정")
    notes = sum(1 for e in gold["entries"] if e["status"] == "⚠️ 지적")
    assert (fixes, notes) == (220, 43), (fixes, notes)
    counts = gold["_counts"]
    got = tuple(counts.get(c, 0) for c in
                ("D1", "D2", "D2c", "D3", "D4", "D5a", "D5b", "D6", "D7", "D8", "D10", "F1"))
    assert got == (27, 18, 1, 3, 3, 22, 25, 54, 4, 7, 8, 115), got
    print("✓ 골드 규모·D코드 카운트 OK")


def test_gold_snippets_stay_within_the_cap():
    """골드에도 자유 문장이 새지 않는다 — 값은 이름·코드·판정뿐이다."""
    raw = GOLD.read_text(encoding="utf-8")
    over = [s for s in _BACKTICK_RE.findall(raw) if len(s) > SNIPPET_MAX]
    assert not over, f"골드에 긴 인용이 있다: {over[:5]}"
    print("✓ 골드 인용 상한 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n전체 {len(tests)}개 통과")
