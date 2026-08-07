#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""컨텍스트 문서의 **주장(claim)** 무결성 검사 (Cat F 회귀 방지).

[`validate_context_paths.py`](validate_context_paths.py) 의 자매 게이트다. 저건 문서가 가리키는
**경로가 실재하는가**를 보고, 이건 문서가 하는 **상태 서술이 아직 참인가**를 본다.

왜 따로 필요한가 (2026-08-07 실측):
  CLAUDE.md 가 "HTTP API 는 오늘 없다" 라고 단언하는 동안 `api/main.py` 는 이미 있었다.
  경로 검사는 통과했다 — 언급된 경로가 전부 실재했으니까. mtime 검사도 통과했다 — 문서와
  코드가 같은 날 수정돼 drift 가 0 으로 보였으니까. **부재 주장은 어느 자동 검사에도 안 걸린다.**
  그 사이 새 세션의 에이전트는 이미 있는 API 를 다시 설계했다.

규칙: **살아있는 문서에서 부재를 주장하려면, 그 부재의 대상을 경로로 지목해야 한다.**
  그러면 CI 가 "정말 없는지" 확인할 수 있다. 지목 없는 부재 주장은 검증 불가라 금지한다.
  ADR·골드 노트는 면제다 — 결정 시점의 사실을 박제하는 게 그 문서들의 존재 이유이고,
  나중에 참이 아니게 되는 건 드리프트가 아니라 기록이다.

stdlib only. 사용: python .github/scripts/validate_context_claims.py [repo_root]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REGISTRY_REL = ".github/context_claims.json"

# 살아있는 문서 = 지금 상태를 서술하는 문서. 여기서만 부재 주장을 통제한다.
LIVING_NAMES = {"CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md", "README.md", "MEMORY.md"}
# 박제 문서 = 과거를 기록하는 문서. 부재 주장이 당연히 남아 있다.
FROZEN_PARTS = {"adr", "gold", "decisions"}
IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
               ".omc", ".claude", "dist", "build", ".next", ".mypy_cache"}

# 부재 관용구 — 코드베이스의 상태를 '없다'고 말하는 표현만. 일반 부정문("평균내지 않는다")은
# 대상이 아니다. 넓히면 소음이 되고, 소음이 나면 게이트가 꺼진다.
ABSENCE_IDIOMS = [
    r"아직 없", r"아직 안 ", r"아직 미", r"아직 아니", r"없어서", r"미연결",
    r"존재하지 않", r"there is none", r"does not exist", r"doesn't exist",
    r"not wired", r"no such column", r"is none today",
]
RE_ABSENCE = re.compile("|".join(ABSENCE_IDIOMS), re.IGNORECASE)


def iter_living_docs(repo: Path):
    for p in sorted(repo.rglob("*.md")):
        parts = p.relative_to(repo).parts
        if any(d in IGNORE_DIRS or d.startswith(".") for d in parts[:-1]):
            continue
        if any(d in FROZEN_PARTS for d in parts):
            continue
        if p.name in LIVING_NAMES:
            yield p


def load_registry(repo: Path) -> dict:
    f = repo / REGISTRY_REL
    if not f.exists():
        return {"absence_claims": [], "presence_guards": []}
    return json.loads(f.read_text(encoding="utf-8"))


def check(repo: Path) -> list[str]:
    reg = load_registry(repo)
    errors: list[str] = []

    # ── 1. 등록된 부재 주장이 아직 참인가 (대상이 생겼으면 문서가 썩은 것)
    for c in reg.get("absence_claims", []):
        target = repo / c["absent_path"]
        if target.exists():
            errors.append(
                f"[{c['id']}] {c['doc']} 의 부재 주장이 더는 참이 아니다 — "
                f"`{c['absent_path']}` 가 생겼다.\n"
                f"        주장: \"{c['phrase']}\"\n"
                f"        → 문서를 현재 상태로 고치고 이 항목을 {REGISTRY_REL} 에서 지워라.")
        doc = repo / c["doc"]
        if doc.exists() and c["phrase"] not in doc.read_text(encoding="utf-8", errors="ignore"):
            errors.append(
                f"[{c['id']}] 등록된 문구가 {c['doc']} 에 없다 (문서가 이미 바뀐 듯) — "
                f"{REGISTRY_REL} 에서 지워라.\n        주장: \"{c['phrase']}\"")

    # ── 2. 이미 있는 것을 '없다'고 말하는 굳은 회귀 방지(문구 단위 하드 차단)
    for g in reg.get("presence_guards", []):
        if not (repo / g["present_path"]).exists():
            continue
        for rel in g["docs"]:
            doc = repo / rel
            if not doc.exists():
                continue
            text = doc.read_text(encoding="utf-8", errors="ignore")
            for bad in g["forbid"]:
                if bad in text:
                    errors.append(
                        f"[{g['id']}] {rel} 이 `{g['present_path']}` 의 실재와 모순되는 문구를 담고 있다.\n"
                        f"        금지 문구: \"{bad}\"\n        이유: {g['why']}")

    # ── 3. 등록되지 않은 부재 주장 (검증 불가 → 금지)
    known = {(c["doc"], c["phrase"]) for c in reg.get("absence_claims", [])}
    for doc in iter_living_docs(repo):
        rel = str(doc.relative_to(repo))
        for i, line in enumerate(doc.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            m = RE_ABSENCE.search(line)
            if not m:
                continue
            if any(phrase in line for d, phrase in known if d == rel):
                continue
            errors.append(
                f"[unregistered] {rel}:{i} 검증 불가능한 부재 주장.\n"
                f"        \"{line.strip()[:96]}\"\n"
                f"        → 무엇이 없는지 경로로 지목하고 {REGISTRY_REL} 의 absence_claims 에 등록하라"
                f" (그래야 그게 생겼을 때 CI 가 알려준다). 과거 서술이라면 ADR 로 옮겨라.")
    return errors


def main(argv: list[str]) -> int:
    repo = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[2]
    reg = load_registry(repo)
    docs = list(iter_living_docs(repo))
    errors = check(repo)

    n_claims = len(reg.get("absence_claims", []))
    n_guards = len(reg.get("presence_guards", []))
    print(f"컨텍스트 주장 검사: 살아있는 문서 {len(docs)}건 · "
          f"등록된 부재 주장 {n_claims}건 · 회귀 가드 {n_guards}건")
    if errors:
        print(f"\n❌ 주장 불일치 {len(errors)}건:")
        for e in errors:
            print(f"  {e}")
        print("\n문서가 '없다'고 말하는 동안 그게 있으면, 에이전트는 있는 걸 다시 만든다.")
        return 1
    print("✅ 모든 상태 주장이 코드와 일치 (0 stale claims).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
