You are running one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-069 · Imports of private names. Read that item in `BACKLOG.md` (search
for `### BL-069`), and `docs/conventions.md` by section where you need it
(`grep -n '^#'` the doc, then Read with offset/limit).

This run does part of the item, two groups, by promoting each name to a
public one (drop the leading underscore), not by pinning with a test:

1. Every leading-underscore name defined in
   `src/sensei_top/archetypes/classify.py` and imported by any other module,
   in `src/` or `tests/` (`_card_colors_by_name`, `_load_definitions`,
   `_seen_forms`, `_card_colors`, `_COLOR_ORDER`, `_odds_text` and whatever
   else a grep finds).
2. Every leading-underscore name imported into
   `src/sensei_top/coach/evidence.py` from another package: today
   `_mana_flags`, `_scope_clause`, `_wilson` (`review/leaks.py`),
   `_finding_line`, `_play_lines`, `_turn_lines` (`review/narrative.py`),
   `_classify_own_deck` (`review/snapshot.py`).

Rename each in its defining module and update every use and every mention
of the old name: imports, call sites, `__init__.py` re-exports, comments,
docstrings, and docs (`docs/*.md` cite symbols as `module.py::name`, and
`tests/test_doc_refs.py` checks those). Grep each old name across
`src/ tests docs scripts site *.md` before and after; none may remain
except an unrelated symbol of the same spelling elsewhere. Keep no
compatibility alias. If the public name already exists in that module or
would shadow something where it is imported, pick another and say why.

Out of scope, leave alone: `cli.py`'s import from `reader/`, any name
defined in `reader/`, `projector/`, `llm/` or `api/guard.py`, the
`api/routes` ↔ `api/sql`/`api/convert` imports (same package), and the
gettext alias `_`. Do not edit `BACKLOG.md`; report the count of
private cross-package imports left after the run, by package, so the
item can be updated.

## Files you may touch

- `src/sensei_top/**/*.py` except `reader/`, `projector/`, `llm/`,
  `api/guard.py` and `migrations/`: only the renamed names and their
  mentions, nothing else on those lines.
- `tests/*.py`: the same.
- `docs/*.md`, `AGENTS.md`: a mention of a renamed name only.

Docs: whichever `docs/` file cites a renamed name. Any other change means
stop and report why. Do not touch `web/`.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q

(the full suite: a rename can break any importer) pass. `tests/test_doc_refs.py`
reads the tree from git, so a failure there that names a file you did not
create is real; one about an untracked file is not, say so.

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
