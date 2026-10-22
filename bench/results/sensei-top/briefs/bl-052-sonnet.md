You are implementing one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-052 · An unsure MTGO pick names no format. Read that item in
`BACKLOG.md` (search for `### BL-052`), and by section where you need them
(`grep -n '^#'` the doc, then Read with offset/limit): `docs/architecture.md`,
"Picking many MTGO matches at once"; `docs/frontend-pages.md`, the
`/mtgo/picks` section; `docs/i18n.md` on keyed strings.

On `/mtgo/picks`, an unsure match whose reason is `no_list` (no saved list
competes in its inferred format) reads "No saved list fits." The match's
inferred format is known to the route (`mtgo/picks.py::PickMatch.format`)
but `MtgoUnsurePick` (`api/models/matches.py`) does not carry it.

1. Add `format: str | None` to `MtgoUnsurePick`, with a `Field`
   description in the style of its neighbours: the format inferred for the
   match, null when none was. Fill it in `api/routes/mtgo_picks.py`'s
   `unsure_out` from the match's `PickMatch`. Prefer looking it up in the
   route; touch `mtgo/picks.py`'s `Unsure` only if the route cannot reach
   it, and say which.
2. Regenerate the types: `pnpm gen:types` in `web/`. Never hand-edit or
   read `web/src/types/api.ts` or `web/openapi.json`.
3. In `web/src/views/MtgoPicksView.tsx::reasonWords`, a `no_list` match
   with a format reads a new key `mtgo.unsure.noListIn`, its format named
   by `lib/decks.ts::formatName`. Everything else that reads `noFit` today
   keeps it: a `low_fit` match with no best list has lists in its format,
   so "no saved list in Modern" would be false there, and a match with no
   format has no format to name. The strings, exactly:
   - `web/src/locales/en/mtgo.json`: `"noListIn": "No saved list in {{format}}."`
   - `web/src/locales/pt-BR/mtgo.json`: `"noListIn": "Nenhuma lista salva em {{format}}."`
   Rewrite the comment above that branch, which says the route does not
   name the format; it must stay true.
4. Tests, each failing if the change is reverted: one in
   `tests/test_mtgo_picks.py` (or `tests/test_api.py`, wherever the
   candidates route's unsure items are already tested) that a `no_list`
   unsure item carries the match's inferred format, with the expected
   format derived from the test's own data, not from running the code; and
   one for `reasonWords`'s three cases if a test file for `MtgoPicksView`
   or a nearby view helper exists — if none does, say so rather than
   building a render harness.

## Files you may touch

- `src/sensei_top/api/models/matches.py`, `src/sensei_top/api/routes/mtgo_picks.py`,
  and `src/sensei_top/mtgo/picks.py` only as step 1 says
- `web/src/views/MtgoPicksView.tsx` (and a test beside it),
  `web/src/locales/{en,pt-BR}/mtgo.json`
- `web/src/types/api.ts`, `web/openapi.json` (regenerated only)
- `tests/test_mtgo_picks.py`, `tests/test_api.py`

Docs: `docs/frontend-pages.md` and `docs/architecture.md`, only where a
sentence names the unsure item's fields or the "No saved list fits." line.
No `CHANGELOG.md` line. Any other file means stop and report why.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_mtgo_picks.py tests/test_api.py tests/test_doc_refs.py tests/test_site.py

pass, and in `web/` `pnpm lint`, `pnpm test` and `pnpm exec tsc -b` pass.

## Rules

- No network, no real database, log or config file, no commits, no branch
  changes. Never read `tests/fixtures/*.log`; scope any grep to
  `src/ web/src tests docs scripts site *.md` (never `src/sensei_top/web_dist`).
- Every comment, docstring and doc sentence in a touched file stays true
  after the change. A doc edit rewraps its paragraph to the file's width.
- Match the surrounding code's style and comment density (AGENTS.md,
  `docs/conventions.md`, `docs/frontend.md`).
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
