#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_logs_store.py tests/test_openai_responses.py tests/test_migrate.py tests/test_llm_logs.py
