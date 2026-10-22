You are implementing one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-082 · Doc-link titles matched by prefix. Read that item in `BACKLOG.md`
(search for `### BL-082`).

`scripts/check_doc_links.py::title_ok` accepts a cited section title when
any heading of the target doc starts with it, so a title cut short, or a
heading renamed to a longer one, still passes. Make it require a whole
heading match (after the script's existing `norm()`).

First, find out which current citations rely on the prefix: run the script
with the prefix rule and with the whole-heading rule and compare. For each
citation that only passes by prefix, fix the citation's quoted title in the
citing file to the full heading text. Do not rename any heading.

Keep the existing fallback (`t in text`) for titles that are not headings
unless it is the reason a cut-short title passes; if you change it, say why.

## Files you may touch

- `scripts/check_doc_links.py`
- Any file whose citation quotes a cut-short title: only the quoted title
  inside that citation, nothing else on the line.

Any other change means stop and report why.

## Done when

    uv run ruff check .
    uv run python scripts/check_doc_links.py

both pass (exit 0), and the report lists every citation you changed with
its file:line, old title and new title.

## Rules

- No network, no real database, log or config file, no commits, no branch
  changes. Never read `tests/fixtures/*.log`; scope any grep to
  `src/ web/src tests docs scripts site *.md`.
- Match the surrounding code's style and comment density.
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
