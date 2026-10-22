You are implementing one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-085 · The pre-reset denominator, written out twice. Read that item in
`BACKLOG.md` (search for `### BL-085`). Read `docs/guide.md` by section only
if you need it (`grep -n '^#' docs/guide.md`, then Read with offset/limit).

`guide/field.py::build_field`'s `pre_total_scoped` is
`_classified_deck_total`'s query written inline over `e.event_date <= ?`.
Make `pre_total_scoped` call `_classified_deck_total` instead, widening that
function's signature if it needs to take the date bound. No visible effect:
every number the function returns stays the same. Update the docstring that
names the inline copy.

## Files you may touch

- `src/sensei_top/guide/field.py`
- `tests/test_guide_field.py` (only if an existing test needs to reach a
  renamed name; do not change any expected value)

Any other file means stop and report why.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_guide_field.py tests/test_guide_bundle.py tests/test_coach_evidence.py

both pass.

## Rules

- No network, no real database, log or config file, no commits, no branch
  changes. Never read `tests/fixtures/*.log`.
- Match the surrounding code's style and comment density (AGENTS.md,
  `docs/conventions.md`).
- Report in under 300 words: files changed, the checks and their result,
  decisions you made yourself, and what you could not verify.

## How you work: orchestrate, never implement

You are the orchestrator. You do not edit files yourself. Implement through
`Agent(subagent_type="implementer", model="haiku", prompt=<phase brief>)`,
one phase per agent (one phase is enough unless the work splits cleanly).
The phase brief carries everything below that the implementer needs: the
item, the files it may touch, the Docs line, the rules, the done-when
checks, and the 300-word report. It has no plan file: say so, and point it
at this item in `BACKLOG.md` instead.

When it reports, read `git diff` yourself against this brief: scope, every
comment and docstring in a touched file still true, the checks run and
passing. Give corrections to the same agent by message (SendMessage) or a
new agent; never fix the code yourself. Stop after two rounds of
corrections that do not converge and report what is left.

The Rules above bind you and the implementer; its brief carries them.
Your report also says how many correction rounds it took.
