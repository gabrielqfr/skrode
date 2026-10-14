#!/usr/bin/env bash
# The "Before you finish" checks (AGENTS.md), with output bounded so a failure
# cannot flood an agent's context. Each command's output is capped at 60 lines
# of 300 characters; the full log of a failing run is kept in a temp file.
#   scripts/verify.sh
# Exits non-zero if any check fails.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
max_lines=60 max_chars=300
log=$(mktemp -t verify.XXXXXX.log)
failed=0

bounded() {
  awk -v n="$max_lines" -v c="$max_chars" '
    NR <= n { if (length($0) > c) $0 = substr($0, 1, c - 24) "...[+" length($0) - c + 24 " chars]"; print }
    END { if (NR > n) print "... [+" NR - n " more lines truncated]" }'
}

check() {  # <name> <command...>
  local name=$1; shift
  local out; out=$("$@" 2>&1); local rc=$?
  printf '== %s (exit %d)\n%s\n' "$name" "$rc" "$out" >> "$log"
  if [ "$rc" -eq 0 ]; then
    printf '%s: ok\n' "$name"
    [ -n "$out" ] && printf '%s\n' "$out" | tail -n 1 | bounded
  else
    printf '%s: FAILED (exit %d)\n' "$name" "$rc"
    printf '%s\n' "$out" | bounded
    failed=1
  fi
}

check "ruff" uv run ruff check .
check "pytest" uv run pytest -q

if [ "$failed" -eq 0 ]; then rm -f "$log"; echo "All checks passed."
else echo "Full log: $log"; fi
exit "$failed"
