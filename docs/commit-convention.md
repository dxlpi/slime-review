# Commit message convention

> [CLAUDE.md](../CLAUDE.md) 에서 분리한 규약. 커밋할 때만 필요한데 컴퍼스의 1/6을 차지했다.
> 강제는 [.githooks/commit-msg](../.githooks/commit-msg) 가 한다 — 이 문서는 그 훅의 근거다.

ALWAYS write commit messages in English — subject, body, and trailers.

Korean is allowed ONLY as a quoted literal, wrapped in backticks:
- Identifiers, filenames, or paths that are actually Korean in the codebase
- UI copy / string values being added or changed
- Domain terms with no established English equivalent (product names,
  category labels, service-specific jargon)

Everything else — verbs, connectives, explanations, reasoning — stays
in English. Never write a Korean sentence and never mix Korean grammar
into English prose.

  GOOD  fix(search): normalize `슬라임` variants before indexing
  GOOD  feat(ui): change empty-state copy to `검색 결과가 없어요`
  BAD   fix(search): 슬라임 검색어 정규화
  BAD   fix(search): normalize 슬라임 검색어 before indexing

On first use in the body, gloss an unfamiliar Korean term once in
parentheses, then reuse the term as-is:
  `제논` (the marketplace this feature targets)

If a term has a widely used English equivalent, prefer the English one.
Do not romanize Korean — use Hangul or English, never `seullaim`. Without
that ban one concept scatters across `슬라임` / `seullaim` / `slime` and
`git log --grep` stops finding it.

Enforced by [.githooks/commit-msg](.githooks/commit-msg) (quoted literals are
stripped before the Korean check; subject must be `<type>(<scope>): <description>`
with the description ≤50 chars). The romanization ban is convention only — no
hook can detect it.
