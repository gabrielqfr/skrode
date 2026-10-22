You are implementing one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-008 · A download for the deck guide's Markdown. Read that item in
`BACKLOG.md` (search for `### BL-008`), and `docs/frontend.md` and
`docs/i18n.md` by section only where you need them (`grep -n '^#'` the doc,
then Read with offset/limit; `docs/i18n.md` "Keying a string in the
dashboard" is the one that matters).

The deck guide's Export card (`web/src/components/guide/WriteAndExport.tsx`)
copies the server's Markdown but cannot save it. Add a download button
beside the copy button, built the way the RCQ guide's is:
`web/src/components/rcq/RcqResult.tsx`, `CarryCard`'s `download` (Blob,
anchor, revoke after a second; `Button variant="secondary"`, disabled
while there is no text).

File name: `deck-guide.md`. If the component already has a generation date
in scope, use `deck-guide-YYYY-MM-DD.md` the way `markdownFileName` in
`web/src/lib/rcq.ts` does; do not add a prop or a fetch to get one. Say in
the report which you did.

The label is a new key, `decks.guide.export.download`, with exactly this
wording (the RCQ guide's own):

- `web/src/locales/en/decks.json`: `"Download .md"`
- `web/src/locales/pt-BR/decks.json`: `"Baixar .md"`

## Files you may touch

- `web/src/components/guide/WriteAndExport.tsx`
- `web/src/locales/en/decks.json`, `web/src/locales/pt-BR/decks.json`

Any other file means stop and report why. Do not touch `web/src/types/api.ts`
or `web/openapi.json`.

## Done when

    cd web && pnpm lint
    cd web && pnpm test
    TMPDIR=/var/tmp uv run pytest -q tests/test_i18n.py tests/test_site.py

all pass.

## Rules

- No network, no real database, log or config file, no commits, no branch
  changes. Never read `tests/fixtures/*.log`.
- Match the surrounding code's style and comment density.
- Report in under 300 words: files changed, the checks and their result,
  decisions you made yourself, and what you could not verify (you cannot
  click the button in a browser; say so).

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
