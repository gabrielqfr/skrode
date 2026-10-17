#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
uv run pytest -q tests/test_api.py
uv run ruff check .
if grep -rn mtgo_read_at_startup src web/src; then echo 'stale value still present' >&2; exit 1; fi
pnpm --dir web exec tsc -b
pnpm --dir web lint
