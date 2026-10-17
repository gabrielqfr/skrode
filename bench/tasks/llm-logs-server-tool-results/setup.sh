#!/usr/bin/env bash
# From the worktree root. Test dependencies resolved as of the merge date, so reruns match.
set -e
uv venv -q --allow-existing --python 3.13 .venv
uv pip install -q --python .venv/bin/python --exclude-newer 2026-08-21 -e . pytest pytest-asyncio httpx2-pytest pytest-recording syrupy cogapp numpy llm-echo==0.3a3
