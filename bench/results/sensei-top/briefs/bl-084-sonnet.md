You are running one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-084 · Test comments citing pins that don't exist. Read that item in
`BACKLOG.md` (search for `### BL-084`), and `docs/testing.md` by section
where you need it (`grep -n '^#'` the doc, then Read with offset/limit).

Four test comments cite labels no test or doc defines:

- `tests/test_metagame_holds.py`: `# k4:` (around line 242), `# k1:`
  (384), `# k3:` (434), `# k6:` (546). The item names k4, k1 and k3; k6 is
  the same kind and goes too.
- `tests/test_metagame_auto.py`: the docstring starting `"""Defect 1:`
  (around line 486).

Each loses its label and says what it pins in its own words: the rule
the test checks, from the code under test and `docs/metagame.md`, not a
label from a review. Keep each comment as short as it is now.

`tests/test_formats.py::test_the_seven_formats_in_mtgos_spelling`'s
docstring says migrations 049's and 053's CHECKs and the API's Literal are
pinned to `FORMATS`. The Literals (`api/models/metagame.py::FormatName`,
`api/models/matches.py::MtgoFormat`) are built from the tuples, so they
hold by construction. The CHECKs are not tested. Add one test, in
`tests/test_formats.py`, that opens a fresh database with
`sensei_top.db.open_database` on a `tmp_path` file and asserts that the
format list in each CHECK (`card_legality.format` from migration 053,
`mtgo_match_pick.format` from 049) is exactly `FORMATS`, read from the
migrated schema (`sqlite_master`), not from the `.sql` files. Then make the
docstring say what is actually pinned and where. Do not touch a
migration. If a later migration rebuilt either table, the schema is the
truth; say so in the report.

## Files you may touch

- `tests/test_metagame_holds.py`, `tests/test_metagame_auto.py`,
  `tests/test_formats.py`

Docs: none; a test comment is not a doc. Any other file means stop and
report why.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_formats.py tests/test_metagame_holds.py tests/test_metagame_auto.py

pass, and the new test fails if a format is removed from `FORMATS` (check
by reasoning, not by editing `formats.py`; say so in the report).

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
