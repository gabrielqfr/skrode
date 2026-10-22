You are running one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-062 · The draft-sharing leftovers. Read that item in `BACKLOG.md`
(search for `### BL-062`), and `docs/frontend.md` by section where you need
it (`grep -n '^#'` the doc, then Read with offset/limit).

Remove everything that describes draft sharing, which the app no longer
reads and nothing draws outside `/design/surfaces`:

- `web/src/components/settings/SharingSection.tsx` and the whole
  `web/src/components/sharing/` folder (`SharingNote`, `DraftReceipt`,
  `PayloadView`); their entries in `views/SurfacesView.tsx` and the Draft
  data section in `views/SettingsView.tsx`.
- The `Sharing`, `SharedDraft`, `DraftPayload` and `Deleted` types and the
  `sharing` field in `web/src/bridge.ts`, the shell side of
  `deleteContributions` there, in `views/SettingsView.tsx` and in
  `lib/sampleShell.ts`, and what `web/src/bridge.test.ts` checks of them.
- In the desktop shell, `desktop/src/preload.ts` and `desktop/src/settings.ts`
  (which set `sharing` to null) and `desktop/src/settings.test.ts`.
- The keys only these used, in both `web/src/locales/en/` and
  `web/src/locales/pt-BR/` (`prompts.json`'s `sharing` block and the
  `settings.json` keys of the Draft data section). Delete keys; never
  write or reword a string. Grep `web/src` for each key before deleting it.
- `docs/frontend.md`'s paragraphs and table row on them (around lines 298,
  393, 463-480).

"Sharing" also appears as a plain English word in unrelated places
(`TurnChart.tsx`, `OpponentHandCard.tsx`, Python docs about a shared
helper); leave those alone. If removing `deleteContributions` would also
remove something a live, non-draft feature uses, keep that part and say
so in the report.

No `CHANGELOG.md` line: nothing a user sees changes.

## Files you may touch

- `web/src/bridge.ts`, `web/src/bridge.test.ts`, `web/src/lib/sampleShell.ts`,
  `web/src/views/SettingsView.tsx`, `web/src/views/SurfacesView.tsx`
- `web/src/components/settings/SharingSection.tsx`,
  `web/src/components/sharing/*` (delete)
- `web/src/locales/{en,pt-BR}/{prompts,settings}.json`
- `desktop/src/preload.ts`, `desktop/src/settings.ts`,
  `desktop/src/settings.test.ts`

Docs: `docs/frontend.md`. Any other file means stop and report why.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_doc_refs.py tests/test_site.py

and, in `web/`, `pnpm lint`, `pnpm test` and `pnpm exec tsc -b`; in
`desktop/`, `pnpm typecheck` and `pnpm test`, all pass. `tests/test_doc_refs.py`
reads the tree from git: a deleted file a doc still names fails it, which
is the point.
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
