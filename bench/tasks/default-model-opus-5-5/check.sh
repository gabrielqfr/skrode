#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
uv run pytest -q tests/test_llm.py
uv run ruff check .
if grep -rn -E 'DEFAULT_MODEL = "claude-opus-5"$|e.g. claude-opus-5"' src; then echo 'stale default still present' >&2; exit 1; fi
