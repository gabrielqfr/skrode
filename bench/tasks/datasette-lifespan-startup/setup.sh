#!/usr/bin/env bash
# From the worktree root. Test dependencies resolved as of the merge date, so reruns match.
set -e
uv venv -q --allow-existing --python 3.13 .venv
uv pip install -q --python .venv/bin/python --exclude-newer 2026-09-01 -e . pytest pytest-asyncio beautifulsoup4 trustme pytest-timeout
