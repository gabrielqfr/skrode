#!/bin/zsh
# Objective checks on a finished bench run; writes runs/<id>-<row>/checks.json.
# Usage: [WEB=1] [DESKTOP=1] checks.sh <id> <row> <base>
ID=$1; ROW=$2; BASE=$3
S="$HOME/Code/sensei-top-public/sensei-top"; B="$S/.claude/handoffs/backlog-runs/bench"
O="$B/runs/$ID-$ROW"; W="$HOME/.cache/sensei-top-wt/bench-$ID-$ROW"
cd "$W" || exit 9
git add -A
T=$(git write-tree)
git diff --stat "$BASE" "$T" > "$O/diffstat.txt"
git diff "$BASE" "$T" -- . ':!web/src/types/api.ts' ':!web/openapi.json' > "$O/diff.patch"
res() { "$@" > "$O/log.$NAME" 2>&1; echo $?; }
NAME=ruff; RUFF=$(res uv run ruff check .)
NAME=pytest; PYTEST=$(TMPDIR=/var/tmp res uv run pytest -q -p no:cacheprovider)
PYSUM=$(tail -1 "$O/log.pytest")
WEBR='null'; DESKR='null'
if [ "$WEB" = 1 ]; then
  cd web || exit 9
  NAME=web-lint; L=$(res pnpm -s lint); NAME=web-test; TT=$(res pnpm -s test); NAME=web-tsc; C=$(res pnpm -s exec tsc -b)
  WEBR="{\"lint\": $L, \"test\": $TT, \"tsc\": $C}"; cd ..
fi
if [ "$DESKTOP" = 1 ]; then
  cd desktop || exit 9
  NAME=desk-test; TT=$(res pnpm -s test); NAME=desk-tsc; C=$(res pnpm -s typecheck)
  DESKR="{\"test\": $TT, \"typecheck\": $C}"; cd ..
fi
# Revert check: put every non-test file back to base, keep the run's tests,
# and run the test files it changed. Pinned when they fail there.
TESTS=($(git diff --name-only "$BASE" "$T" -- 'tests/*.py'))
TSTESTS=($(git diff --name-only "$BASE" "$T" -- '*.test.ts'))
NONTEST=($(git diff --name-only "$BASE" "$T" | grep -v -E '^tests/|\.test\.ts$'))
REV_PY='null'; REV_TS='null'
if [ ${#NONTEST} -gt 0 ] && [ $(( ${#TESTS} + ${#TSTESTS} )) -gt 0 ]; then
  for f in $NONTEST; do
    if git cat-file -e "$BASE:$f" 2>/dev/null; then git checkout -q "$BASE" -- "$f"; else rm -f "$f"; fi
  done
  if [ ${#TESTS} -gt 0 ]; then
    NAME=revert-py; R=$(TMPDIR=/var/tmp res uv run pytest -q -p no:cacheprovider $TESTS)
    REV_PY="{\"exit\": $R, \"summary\": $(tail -1 "$O/log.revert-py" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')}"
  fi
  if [ ${#TSTESTS} -gt 0 ] && [ -d web/node_modules ]; then
    REL=(${TSTESTS#web/}); REL=(${TSTESTS//web\//})
    NAME=revert-ts; R=$(cd web && res pnpm -s exec vitest run $REL); REV_TS="{\"exit\": $R}"
  fi
  git read-tree -u --reset "$T"
fi
[ "$(git write-tree)" = "$T" ] || echo "WARNING: tree not restored" >> "$O/run.err"
python3 -I - "$O" <<PY
import json, sys, pathlib
null = None  # the shell values below are JSON
o = pathlib.Path(sys.argv[1])
names = """$(git diff --name-status "$BASE" "$T")"""
out = {
  "files": [l.split("\t") for l in names.splitlines() if l],
  "ruff_exit": $RUFF, "pytest_exit": $PYTEST, "pytest_summary": """$PYSUM""",
  "web": $WEBR, "desktop": $DESKR,
  "tests_changed": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' $TESTS $TSTESTS),
  "revert_py": $REV_PY, "revert_ts": $REV_TS,
}
(o / "checks.json").write_text(json.dumps(out, indent=1))
PY
