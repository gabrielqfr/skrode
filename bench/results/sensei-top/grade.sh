#!/bin/zsh
# Blind grade of a bench run, or of a reference (ROW=ref grades the
# accepted solution checked out as bench-<id>-ref). Usage: grade.sh <id> <row> <base> <tip> [pass]
ID=$1; ROW=$2; BASE=$3; TIP=$4; PASS=${5:-1}
S="$HOME/Code/sensei-top-public/sensei-top"; B="$S/.claude/handoffs/backlog-runs/bench"
O="$B/runs/$ID-$ROW"; W="$HOME/.cache/sensei-top-wt/bench-$ID-$ROW"
cd "$W" || exit 9
python3 -I - "$O" "$B/briefs/$ID-haiku.md" > "$O/report.txt" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / "run.jsonl"
rs = [json.loads(l) for l in open(p) if '"type":"result"' in l] if p.exists() else None
print("(no report given)" if rs is None else rs[-1].get("result", "(no report)") if rs else "(no report: the run never finished)")
PY
BRIEF=$(sed '/^## How you work/,/^## Rules/{/^## Rules/!d;}' "$B/briefs/$ID-haiku.md")
REF=$(git -C "$S" diff "$BASE" "$TIP" -- . ':!web/src/types/api.ts' ':!web/openapi.json' ':!BACKLOG.md' ':!CHANGELOG.md' ':!ROADMAP.md' | head -3000)
DIFF=$(head -3000 "$O/diff.patch")
if [ "$ROW" = ref ]; then
  DIFF=$(git -C "$S" diff "$BASE" "$TIP" -- . ':!web/src/types/api.ts' ':!web/openapi.json' ':!BACKLOG.md' ':!CHANGELOG.md' ':!ROADMAP.md' | head -3000)
  echo "(none: this change was committed without an author report; score report 4)" > "$O/report.txt"
  REFSECTION="# Reference diff
(none: grade this change on its own. Its BACKLOG.md, CHANGELOG.md and ROADMAP.md edits are the repo's bookkeeping for closing the item, done at commit time; they are not part of the change and cost no scope.)"
else
  REFSECTION="# Reference diff (accepted solution; its BACKLOG/CHANGELOG/ROADMAP edits left out)
\`\`\`diff
$REF
\`\`\`"
fi
PROMPT="$(cat "$B/grader.md")

Base commit: $BASE

# Brief
$BRIEF

# Objective checks
$(cat "$O/checks.json")

# Author's report
$(cat "$O/report.txt")

# The change's diff (generated api.ts left out; first 3000 lines)
\`\`\`diff
$DIFF
\`\`\`

$REFSECTION"
# The prompt goes in on stdin: as an argument it can pass Linux's 128 KB
# per-argument limit (062 and 069) and claude never starts.
printf '%s' "$PROMPT" | ANTHROPIC_API_KEY="$(cat ~/.config/anthropic/credit-key)" \
  claude -p --model opus --output-format json --strict-mcp-config \
    --json-schema "$(cat "$B/schema.json")" --max-budget-usd 4 \
    --allowedTools Read Grep Glob "Bash(git diff*)" "Bash(git show*)" "Bash(git status*)" \
  > "$O/grade$PASS.json" 2> "$O/grade$PASS.err"
RC=$?
echo "exit $RC" >> "$O/grade$PASS.err"
exit $RC
