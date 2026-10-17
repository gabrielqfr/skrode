#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_background_tasks.py tests/test_cli_serve_get.py tests/test_lifespan.py tests/test_plugins.py
