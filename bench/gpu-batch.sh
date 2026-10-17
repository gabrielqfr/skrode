#!/usr/bin/env bash
# One GPU batch: wait for the model server, sample GPU power, record 60 s of loaded idle, then
# run each row in ROWS one after another over the tasks given as arguments.
#
#   OUT=runs/b9 RUNS=3 bench/gpu-batch.sh datasette-write-sql-views tox-override-substitution
#
# OUT (required): the batch dir. PREP: benchprep's --out (default ~/.cache/skrode-bench/prep).
# WORKTREES: where rows make their worktrees (default ~/.cache/skrode-bench/wt). RUNS (default
# 1). ROWS (default router-bonsai). MODEL_UP and MODEL_DOWN: optional commands that start and
# stop the model server (a systemd unit, say); without them the server must already be
# listening on 127.0.0.1:8080.
set -u
cd "$(dirname "$0")/.." || exit 1
out=${OUT:?set OUT to the batch dir}
prep=${PREP:-$HOME/.cache/skrode-bench/prep}
worktrees=${WORKTREES:-$HOME/.cache/skrode-bench/wt}
runs=${RUNS:-1}
[ $# -gt 0 ] || { echo "usage: OUT=dir $0 task..." >&2; exit 2; }
mkdir -p "$out"
[ -n "${MODEL_UP:-}" ] && bash -c "$MODEL_UP"
until curl -sf http://127.0.0.1:8080/health >/dev/null; do sleep 2; done
python3 bench/sampler.py --out "$out/power.jsonl" &
sampler=$!
trap 'kill $sampler 2>/dev/null; [ -n "${MODEL_DOWN:-}" ] && bash -c "$MODEL_DOWN"' EXIT
start=$(date +%s.%N)
sleep 60
echo -e "idle\t$start\t$(date +%s.%N)" >> "$out/idle.tsv"
for row in ${ROWS:-router-bonsai}; do
  .venv/bin/python -m skrode.benchrun --prep "$prep" --row "$row" --out "$out" \
    --worktrees "$worktrees" --runs "$runs" "$@"
done
