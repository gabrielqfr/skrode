#!/bin/zsh
# One bench run: item $1 on row $2 (haiku|sonnet|opus), from base commit $3,
# in a detached worktree. Row ref only checks out commit $3. WEB=1/DESKTOP=1 as in run.sh. The key is read here.
ID=$1; ROW=$2; BASE=$3
[ -n "$ID" ] && [ -n "$ROW" ] && [ -n "$BASE" ] || exit 2
S="$HOME/Code/sensei-top-public/sensei-top"; B="$S/.claude/handoffs/backlog-runs/bench"
O="$B/runs/$ID-$ROW"; W="$HOME/.cache/sensei-top-wt/bench-$ID-$ROW"
BRIEF="$B/briefs/$ID-$ROW.md"; [ "$ROW" = ref ] || test -s "$BRIEF" || exit 3
mkdir -p "$O"
[ -e "$W" ] && { echo "worktree exists: $W"; exit 9; }
git -C "$S" worktree add --detach "$W" "$BASE" >/dev/null || exit 9
cd "$W" || exit 9
mkdir -p .claude && cp -r "$S/.claude/agents" .claude/
uv sync --frozen -q || exit 4
[ "$WEB" = 1 ] && { (cd web && pnpm install --frozen-lockfile --silent) || exit 5; }
[ "$DESKTOP" = 1 ] && { (cd desktop && pnpm install --frozen-lockfile --silent) || exit 5; }
case $ROW in
  ref) exit 0;;  # the accepted solution, checked out at $3 for grading
  haiku) MODEL=haiku; BUDGET=1;; sonnet) MODEL=sonnet; BUDGET=3;; opus) MODEL=opus; BUDGET=5;;
  *) exit 2;;
esac
TOOLS=(Read Edit Write Grep Glob "Bash(uv run ruff *)"
  "Bash(TMPDIR=/var/tmp uv run pytest *)" "Bash(pnpm lint)"
  "Bash(git diff*)" "Bash(git status*)")
[ "$WEB" = 1 ] && TOOLS+=("Bash(pnpm test)" "Bash(pnpm exec tsc -b)" "Bash(pnpm gen:types)")
[ "$DESKTOP" = 1 ] && TOOLS+=("Bash(pnpm test)" "Bash(pnpm typecheck)")
[ "$ROW" = sonnet ] && TOOLS+=(Agent SendMessage)
test -s ~/.config/anthropic/credit-key || exit 6
ANTHROPIC_API_KEY="$(cat ~/.config/anthropic/credit-key)" \
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=2700000 \
  claude -p "$(cat "$BRIEF")" --model "$MODEL" \
    --output-format stream-json --verbose --strict-mcp-config \
    --permission-mode acceptEdits --max-budget-usd "$BUDGET" \
    --allowedTools "${TOOLS[@]}" \
  > "$O/run.jsonl" 2> "$O/run.err"
echo "exit $?" >> "$O/run.err"
