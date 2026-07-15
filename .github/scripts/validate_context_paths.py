#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""컨텍스트 문서의 경로 무결성 검사 (Cat F · E1 회귀 방지).

CLAUDE.md / AGENTS.md / ARCHITECTURE.md / MEMORY.md / README.md / docs/** 안의
- 마크다운 링크 `[텍스트](경로)` (외부 URL·앵커 제외)
- 인라인 경로 참조 `dir/file.ext`
가 실제로 존재하는지 확인한다. 하나라도 깨지면 exit 1 → pre-push 훅 / CI 가 머지를 막는다.

'stale reference 는 없는 것보다 나쁘다' — 에이전트가 유령 경로를 따라가는 회귀를 코드 진입 시점에 차단.
stdlib only. 사용: python .github/scripts/validate_context_paths.py [repo_root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

IGNORE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
               ".omc", ".claude", ".github", "dist", "build", ".next", ".mypy_cache"}

# 스캔 대상 문서
DOC_NAMES = {"CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md", "MEMORY.md", "README.md"}

# 인라인 경로: dir/file.ext (확장자 alternation 은 긴 것 먼저 — json before js)
RE_PATH_REF = re.compile(
    r"(?<![A-Za-z0-9_/])"
    r"((?:\./|\.?[A-Za-z0-9_]+/)[A-Za-z0-9_./-]+\.(?:py|tsx|ts|jsx|js|md|sql|json|yaml|yml|toml|html|css|sh|cfg|ini))"
    r"(?![A-Za-z0-9])"
)
# 마크다운 링크(외부 URL 제외)
RE_MD_LINK = re.compile(r"\[[^\]]+\]\((?!https?://|mailto:)([^)]+)\)")


def iter_docs(repo: Path):
    """DOC_NAMES 전부 + docs/ 아래 모든 .md 를 스캔 대상으로."""
    for p in repo.rglob("*.md"):
        if any(part in IGNORE_DIRS or part.startswith(".") for part in p.relative_to(repo).parts[:-1]):
            continue
        if p.name in DOC_NAMES or "docs" in p.relative_to(repo).parts:
            yield p


def resolve(ref: str, doc: Path, repo: Path) -> bool:
    """ref 가 doc 기준 상대 또는 repo 루트 기준으로 존재하면 True. 앵커/쿼리는 제거."""
    ref = ref.split("#", 1)[0].split("?", 1)[0].strip()
    if not ref:
        return True  # 순수 앵커 링크(#section) — 파일 참조 아님
    # 절대(레포 루트 기준) 시도 + doc 상대 시도
    for base in (doc.parent, repo):
        try:
            if (base / ref).resolve().exists():
                return True
        except (OSError, ValueError):
            continue
    return False


def check_doc(doc: Path, repo: Path) -> list[tuple[str, str]]:
    text = doc.read_text(encoding="utf-8", errors="ignore")
    broken: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in RE_MD_LINK.finditer(text):
        ref = m.group(1)
        if ref in seen:
            continue
        seen.add(ref)
        if not resolve(ref, doc, repo):
            broken.append(("link", ref))
    # 인라인 경로 스캔 전에 마크다운 링크 스팬을 제거한다 — 안 그러면 `](../..)` 같은
    # 상대 링크 내부를 부분매칭해 유령 경로(예: './../x.md')로 오탐한다(링크는 위에서 이미 검사).
    stripped = RE_MD_LINK.sub(" ", text)
    for m in RE_PATH_REF.finditer(stripped):
        ref = m.group(1)
        if ref in seen:
            continue
        seen.add(ref)
        if not resolve(ref, doc, repo):
            broken.append(("path", ref))
    return broken


def main(argv: list[str]) -> int:
    repo = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[2]
    docs = sorted(iter_docs(repo))
    total_refs = 0
    all_broken: list[tuple[Path, str, str]] = []
    for doc in docs:
        broken = check_doc(doc, repo)
        all_broken.extend((doc, kind, ref) for kind, ref in broken)

    print(f"컨텍스트 경로 검사: 문서 {len(docs)}건 스캔")
    if all_broken:
        print(f"\n❌ 깨진 참조 {len(all_broken)}건:")
        for doc, kind, ref in all_broken:
            print(f"  {doc.relative_to(repo)}  [{kind}]  →  {ref}")
        print("\nstale reference 는 없는 것보다 나쁘다 — 실제 경로로 고치거나 참조를 제거하라.")
        return 1
    print("✅ 모든 참조가 실제 파일/디렉터리로 해석됨 (0 hallucinated paths).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
