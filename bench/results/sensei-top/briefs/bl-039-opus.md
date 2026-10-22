You are implementing one backlog item in this repo, headless. Nobody will
answer questions: make the call yourself and say so in the report.

## Item

BL-039 · "mode 600" on Windows. Read that item in `BACKLOG.md` (search for
`### BL-039`).

`src/sensei_top/cli.py` writes the config file with `path.chmod(0o600)`
(around line 789) and then prints "(mode 600)" in five messages: the
`_store_key` file branch (around line 993, and its docstring at 974),
`_written` (around 1107), and the backup-settings and config lines in
`cmd_config` (around 1219 and 1230). On Windows `chmod` only sets or
clears the read-only flag, so the claim is false there.

The decision is made: leave the claim out where `chmod` does nothing. One
small helper in `cli.py` gives the parenthesis on any platform but
`sys.platform == "win32"` and nothing there; every one of the five
messages uses it, so the text elsewhere is byte-for-byte unchanged. Keep
the `chmod` call as it is. No new wording, on any platform. The
`_store_key` docstring says "(mode 600)" too: make it true on both, in as
few words as it has now.

Test, in `tests/test_llm.py` beside the existing assertion near line 1292
(which must still pass untouched): with `sys.platform` monkeypatched to
`"win32"`, the same command prints the same line without " (mode 600)".
The test must fail if the helper is reverted.

## Files you may touch

- `src/sensei_top/cli.py`
- `tests/test_llm.py`

Docs: `docs/llm.md` line 156 says the file is written `chmod 600`, which
stays true (the call still happens); leave it unless your change makes it
false. Any other file means stop and report why.

## Done when

    uv run ruff check .
    TMPDIR=/var/tmp uv run pytest -q tests/test_llm.py tests/test_cli.py

both pass.

## Rules

- No network, no real database, log, keyring or config file (the tests
  already isolate the config path; never let one reach the real keyring),
  no commits, no branch changes. Never read `tests/fixtures/*.log`; scope
  any grep to `src/ tests docs`.
- Every comment and docstring in a touched file stays true after the
  change.
- Match the surrounding code's style and comment density (AGENTS.md,
  `docs/conventions.md`).
- Report in under 300 words: files changed, the checks and their result,
  decisions you made yourself, and what you could not verify.
