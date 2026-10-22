You are running one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-066 · A seat's colours, read in two places. Read that item in
`BACKLOG.md` (search for `### BL-066`), and `docs/current-match.md` and
`docs/opponent-deck.md` by section where you need them (`grep -n '^#'` the
doc, then Read with offset/limit).

The rule that reads a seat's colours from its played cards (nonland,
nontoken by the supertype text before the type line's em dash, distinct
names, colour identities unioned, unreadable JSON as none, a name absent
from `cards` excluded) exists twice:

- `archetypes/classify.py::live_opponent_colors(conn, names)` — one query
  per call; `table/intermission.py` calls it.
- `api/routes/mtgo_picks.py::_colors_by_played_name(conn, names)` (one
  chunked query for every name of every match, a `name -> (counted,
  colours)` table) and `_colors_of(table, names)`, kept equal by
  `tests/test_api.py::test_mtgo_bulk_colour_helper_equals_live_opponent_colors`.

Make one rule both import, in `archetypes/classify.py`: a public bulk
function that builds the per-name table (chunked as the route's is now)
and a public function that reads `(colors, cards_seen)` for a name list
from that table. `live_opponent_colors` keeps its name and signature and
becomes those two composed, so `table/intermission.py` and the tests in
`tests/test_classify_seen.py` are untouched and still pass unchanged.
The route drops its two private functions and imports the public ones.
Pick names that read like the module's others (`card_colors_by_name`,
`card_colors_of`). The table's `counted` flag is always `True` today; drop
it if nothing needs it, and say which you chose.

`live_opponent_colors`'s docstring (the view's arithmetic re-run in Python,
the distinct-names difference, rule 4) stays true; move whatever of it
describes the shared rule onto the function that now holds it. The
equality test in `tests/test_api.py` pinned two copies that no longer
exist: replace it with a test there that the route's import is the
archetypes helper, or drop it if `tests/test_classify_seen.py` already
covers every case it checked (lands, tokens, an unknown name, a reprint);
say which and why. Add to `tests/test_classify_seen.py` one test of the
bulk table on more than 500 names, so the chunking is covered, only if no
existing test already covers it.

## Files you may touch

- `src/sensei_top/archetypes/classify.py`
- `src/sensei_top/api/routes/mtgo_picks.py`
- `tests/test_api.py`, `tests/test_classify_seen.py`

Docs: `docs/current-match.md` (the paragraph around line 520 that names
`live_opponent_colors`), only if its sentence stops being true. Any other
file means stop and report why.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_classify_seen.py tests/test_api.py tests/test_intermission.py tests/test_mtgo_picks.py tests/test_doc_refs.py

pass.
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

## Rules (yours and the implementer's)

- No network, no real database, log or config file, no commits, no branch
  changes. Never read `tests/fixtures/*.log`; scope any grep to
  `src/ web/src tests docs scripts site *.md`.
- Every comment, docstring and doc sentence in a touched file stays true
  after the change, counts and caller lists included. A doc edit rewraps
  its paragraph to the file's width.
- Match the surrounding code's style and comment density.
- Your final report, under 300 words: files changed, the checks and their
  result, the decisions made without asking (yours or the implementer's),
  how many correction rounds it took, and what could not be verified.
