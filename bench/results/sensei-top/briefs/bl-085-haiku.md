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
