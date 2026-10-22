"""Run the replay bench: every item on every row, plus its accepted
reference, each through run -> checks -> blind grade; then a second grade
of a few runs to measure the grader's agreement with itself.

Usage: python3 -I bench.py [--workers N] [--only id-row,...] [--regrade N]
"""
import argparse
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

B = Path(__file__).resolve().parent
ROWS = ("haiku", "sonnet", "opus", "ref")
LOG = B / "status.log"


def log(msg: str) -> None:
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def items():
    for line in (B / "items.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        ident, base, tip, _row, web, desk = line.split("\t")
        yield ident, base, tip, web, desk


def step(name, args, env):
    t = time.time()
    r = subprocess.run(args, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"{name} exit={r.returncode} {int(time.time() - t)}s")
    return r.returncode


def graded(p):
    # An empty grade is a grade.sh that failed, not a grade.
    return p.exists() and p.stat().st_size > 0


def pipeline(job):
    ident, row, base, tip, web, desk = job
    env = dict(os.environ, WEB=web, DESKTOP=desk)
    name = f"{ident}-{row}"
    out = B / "runs" / name
    if graded(out / "grade1.json"):
        log(f"{name} skip (graded)")
        return
    if not (out / "checks.json").exists():
        if step(f"{name} run", [str(B / "run_one.sh"), ident, row, tip if row == "ref" else base], env) not in (0,):
            if not (out / "run.jsonl").exists() and row != "ref":
                log(f"{name} FAILED before the run")
                return
        step(f"{name} checks", [str(B / "checks.sh"), ident, row, base], env)
    step(f"{name} grade", [str(B / "grade.sh"), ident, row, base, tip, "1"], env)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--only", default="")
    ap.add_argument("--regrade", type=int, default=8)
    a = ap.parse_args()
    jobs = [(i, r, b, t, w, d) for i, b, t, w, d in items() for r in ROWS]
    if a.only:
        keep = set(a.only.split(","))
        jobs = [j for j in jobs if f"{j[0]}-{j[1]}" in keep]
    random.Random(7).shuffle(jobs)
    log(f"start {len(jobs)} jobs, {a.workers} workers")
    with ThreadPoolExecutor(a.workers) as pool:
        list(pool.map(pipeline, jobs))
    runs = [j for j in jobs if j[1] != "ref" and graded(B / "runs" / f"{j[0]}-{j[1]}" / "grade1.json")]
    again = random.Random(11).sample(runs, min(a.regrade, len(runs)))
    with ThreadPoolExecutor(a.workers) as pool:
        list(pool.map(lambda j: step(f"{j[0]}-{j[1]} grade2", [str(B / "grade.sh"), j[0], j[1], j[2], j[3], "2"],
                                     dict(os.environ, WEB=j[4], DESKTOP=j[5])), again))
    log("done")


if __name__ == "__main__":
    sys.exit(main())
