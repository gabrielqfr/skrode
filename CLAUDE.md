@AGENTS.md

## Sessions (Claude Code only)

These rules are kept out of `AGENTS.md` because the local implementer reads that file and needs
none of them.

- **One phase per session; hand off at ~120k tokens.** When a phase ends, or context passes about
  120k tokens, rewrite `.claude/handoffs/HANDOFF.md` whole (state, not history, about 4 KB) and
  tell the user to `/clear`. Every turn re-reads the whole context, so a long session pays for its
  own history again and again. Auto-compaction starts near 167k, so a session that waits for 150k
  compacts during its next phase. The status line shows the live context size against 120k.
- **Read by section, never whole.** The plan: its Decisions and Phases tables, then the next
  phase's S section (`grep -n '^### S'`). The guide: one section at a time (`grep -n '^### '`).
  A finished phase's S sections move to `.claude/plans/plan.done.md`.
- **No subagents by default.** Each one re-reads what it has read on every call. The exception is
  the reviewer, on the phases the plan marks.
- **Bounded output.** Use `scripts/verify.sh`, which caps its output, rather than raw `pytest` or
  `ruff` runs, and pipe long commands through `tail` or `head`.
