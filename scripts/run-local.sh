#!/usr/bin/env bash
# Run one spec through opencode and the local model, in its own worktree and branch, then check
# the result and log the round. The implementer for this repo until the router can build itself.
#   scripts/run-local.sh <phase> <spec.md>
# Needs a model server already on 127.0.0.1:8080; never starts one. Env: TIMEOUT_MIN (45),
# BASE (the commit to branch from, default HEAD).
# Round N of <phase> leaves:
#   worktree ../hybrid-router-wt/<phase>-rN on branch local/<phase>-rN (kept for review)
#   .claude/rounds/<phase>/rN/{spec.md,opencode.log,agent.diff,touched.txt,named.txt,verify.log}
#   one row in .claude/rounds.tsv (revision rounds are counted, never hidden)
set -u
phase=${1:?usage: run-local.sh <phase> <spec.md>}
spec=$(realpath "${2:?usage: run-local.sh <phase> <spec.md>}")
repo=$(git rev-parse --show-toplevel)
base=$(git -C "$repo" rev-parse --verify -q "${BASE:-HEAD}^{commit}") \
  || { echo "no commit ${BASE:-HEAD} to branch from" >&2; exit 1; }
export OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1
export PATH="$HOME/.local/bin:$PATH"
prompt="Implement the spec in SPEC.md. Run the checks it lists and stop when they pass."

curl -sf -m 5 http://127.0.0.1:8080/health >/dev/null \
  || { echo "no model server on 127.0.0.1:8080; start one first (llm plan)" >&2; exit 1; }

n=1; while [ -e "$repo/.claude/rounds/$phase/r$n" ]; do n=$((n + 1)); done
out="$repo/.claude/rounds/$phase/r$n"
branch="local/$phase-r$n"
wt="$(dirname "$repo")/hybrid-router-wt/$phase-r$n"
mkdir -p "$out" "$(dirname "$wt")"
cp "$spec" "$out/spec.md"

git -C "$repo" worktree add -q -b "$branch" "$wt" "$base" \
  || { echo "worktree failed" >&2; exit 1; }
cp "$spec" "$wt/SPEC.md"
( cd "$wt" && uv sync --frozen -q ) > "$out/setup.log" 2>&1

echo "== $phase round $n on $branch"
start=$(date +%s)
# Its own config dir: this repo's rules and permissions, none of the global opencode config.
( cd "$wt" && XDG_CONFIG_HOME="$repo/implementer" timeout "${TIMEOUT_MIN:-45}m" \
    opencode run "$prompt" ) > "$out/opencode.log" 2>&1
minutes=$(awk -v s=$(( $(date +%s) - start )) 'BEGIN{printf "%.1f", s/60}')

# Everything since the base, untracked files included; SPEC.md is gitignored.
git -C "$wt" add -A -N
git -C "$wt" diff "$base" > "$out/agent.diff"
git -C "$wt" diff --name-only "$base" | sort -u > "$out/touched.txt"
awk 'tolower($0) ~ /^#+ .*files to touch/ {f=1; next} /^#/ {f=0} f' "$spec" \
  | grep -o '`[^` ]*[./][^` ]*`' | tr -d '`' | sed 's#^\./##' | sort -u > "$out/named.txt"
extra=$(comm -23 "$out/touched.txt" "$out/named.txt" | paste -sd, -)
missing=$(comm -13 "$out/touched.txt" "$out/named.txt" | paste -sd, -)

# The router's own check, not the model's report (non-negotiable 3 applies to us too).
( cd "$wt" && scripts/verify.sh ) > "$out/verify.log" 2>&1 && verify=pass || verify=fail

log="$repo/.claude/rounds.tsv"
[ -e "$log" ] || printf 'date\tphase\tround\tverify\tminutes\ttouched\tnamed\textra\tmissing\tbranch\n' > "$log"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%F)" "$phase" "$n" "$verify" "$minutes" \
  "$(wc -l < "$out/touched.txt")" "$(wc -l < "$out/named.txt")" "${extra:--}" "${missing:--}" \
  "$branch" >> "$log"

echo "verify: $verify, $minutes min, extra: ${extra:--}, missing: ${missing:--}"
tail -n 3 "$out/verify.log"
echo "diff: $out/agent.diff   worktree: $wt"
