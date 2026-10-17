#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
uv run pytest -q tests/test_cli_report.py
uv run ruff check .
