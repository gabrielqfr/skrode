#!/usr/bin/env bash
# From the worktree root. $HIDDEN_DIR holds the merged versions of the test files (benchprep).
set -e
cp -r "$HIDDEN_DIR/." .
pnpm --dir web exec vitest run src/bridge.test.ts
pnpm --dir web exec tsc -b
pnpm --dir web lint
