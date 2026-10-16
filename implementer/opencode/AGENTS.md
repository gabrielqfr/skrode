# skrode: implementer rules (Build agent)

You implement one spec, from SPEC.md in the worktree root. The repository's own AGENTS.md is also
loaded: it describes the project, and its non-negotiables bind every line you write.

- Touch only the files under the spec's "Files to touch". If another file is needed, stop and
  say so in your summary instead of editing it.
- Follow existing patterns in neighbouring files. Do not add dependencies, and do not edit
  `pyproject.toml` or `uv.lock` unless the spec lists them.
- Copy text the spec marks verbatim exactly, line breaks included.
- Before finishing, run the spec's "Commands that must pass", then `scripts/verify.sh`.
- No other shell commands are allowed; do not try alternatives when one is denied.
- No network. Do not commit, and never commit SPEC.md.
- Finish with a short summary: files changed, checks run with their result lines, and anything
  unresolved or decided without the spec.
