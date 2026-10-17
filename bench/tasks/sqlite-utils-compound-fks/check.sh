#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_foreign_keys.py tests/test_create.py tests/test_transform.py tests/test_m2m.py tests/test_extracts.py tests/test_cli.py
