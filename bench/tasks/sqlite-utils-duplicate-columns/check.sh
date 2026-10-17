#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_cli.py tests/test_fts.py tests/test_query.py tests/test_rows.py tests/test_utils.py
