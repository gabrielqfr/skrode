#!/usr/bin/env bash
# From the worktree root. Test dependencies resolved as of the merge date, so reruns match.
# condense-json>=1.1 is named here so the parent's venv has it before pyproject asks for it.
set -e
uv venv -q --allow-existing --python 3.13 .venv
uv pip install -q --python .venv/bin/python --exclude-newer 2026-08-04 -e . 'condense-json>=1.1' pytest pytest-asyncio 'pytest-httpx>=0.33.0' pytest-recording syrupy cogapp numpy llm-echo
