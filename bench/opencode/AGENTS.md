# Bench implementer rules (Build agent)

You implement one spec, from SPEC.md in the worktree root. The repository's own AGENTS.md or
CLAUDE.md, if it has one, describes the project.

- Touch only the files under the spec's "Files to touch". If another file is needed, stop and
  say so in your summary instead of editing it.
- Follow existing patterns in neighbouring files. Do not add dependencies.
- The test files the spec lists under "Tests to add" are already written: read them, never edit
  them.
- Run every command under the spec's "Commands that must pass" and fix what fails. Other shell
  commands may be denied; do not try alternatives when one is.
- Never edit or commit SPEC.md, and do not commit at all.
- Finish with a short summary: files changed, checks run and their result, anything unresolved.
