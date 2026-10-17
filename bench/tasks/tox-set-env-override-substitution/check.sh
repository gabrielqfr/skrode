#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep). plus the
# unchanged config tests that guard set_env, overrides and substitution.
set -e
cp -r "$HIDDEN_DIR/." .
.venv/bin/python -m pytest -q -p no:cacheprovider -n 4 tests/config --ignore=tests/config/cli/test_argcomplete.py
