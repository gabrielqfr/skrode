You are running one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-083 · Readers that filter `md.deck_id` against a set. Read that item
in `BACKLOG.md` (search for `### BL-083`), and `docs/architecture.md`'s
paragraph that starts `**Filter a deck through \`v_match_deck\` by match
id` (`grep -n` for it, then Read with offset/limit).

Seven queries join `v_match_deck md` and then filter
`md.deck_id IN (?, …)`, the shape that doc paragraph forbids:

- `guide/field.py`: around lines 193, 249 and 383
- `guide/deck.py`: around 164
- `guide/list_diff.py`: around 260
- `guide/bundle.py`: around 141
- `coach/evidence.py`: around 417

Convert each so the deck filter is `m.match_id IN (<subquery>)`, built by
`guide/play.py::_deck_matches` (read its docstring). Rename that helper to
`deck_matches` (public: `coach/` will import it, and BL-069 forbids a new
cross-package private import) and update its six uses in `play.py`.

Four of the queries only filter on the deck. Three (`field.py` ~383,
`list_diff.py` ~260, `evidence.py` ~417) also select `md.deck_id` to find
the most recently played version: keep the `v_match_deck` join for the
selected column, and move only the filter to the match-id form. Results
must not change: `v_match_deck` has one row per match (check that in its
migration and say so), so the two filters select the same rows. Don't merge
the three look-alike queries into one helper in this run; say in the
report whether you would.

Fix every comment that describes the old shape (`evidence.py` ~408 says
the query filters the view's `deck_id`). If a query cannot take the
`IN` form, leave it, and add a sentence to the doc paragraph naming the
exception and why.

Pin the rule with one test that fails while any module under
`src/sensei_top/` contains `md.deck_id IN (`: it reads the source files,
it does not run them. Put it beside the tests of `v_match_deck` or of
`guide/play.py`, whichever already exists, and say which.

## Files you may touch

- `src/sensei_top/guide/field.py`, `deck.py`, `list_diff.py`, `bundle.py`,
  `play.py`, `src/sensei_top/coach/evidence.py`
- one test file under `tests/` for the pin
- `docs/architecture.md`, that paragraph only, for an exception

Docs: `docs/architecture.md`, the paragraph above. Any other file means
stop and report why. No migration, no view change.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_guide_field.py tests/test_guide_deck.py tests/test_guide_list_diff.py tests/test_guide_bundle.py tests/test_guide_play.py tests/test_coach_evidence.py <the pin's file>

pass. You cannot measure the speed (no real database, no `python -c`);
say so, and the session will check the query plans.

## Rules

- No network, no real database, log or config file, no commits, no branch
  changes. Never read `tests/fixtures/*.log`; scope any grep to
  `src/ web/src tests docs scripts site *.md`.
- Every comment, docstring and doc sentence in a touched file stays true
  after the change, counts and caller lists included. A doc edit rewraps
  its paragraph to the file's width.
- Match the surrounding code's style and comment density.
- Your final report, under 300 words: files changed, the checks and their
  result, the decisions you made yourself, and what could not be verified.
