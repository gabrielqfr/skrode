"""One summary of every bench run, and one line per row.

`skrode.benchrun` leaves one batch directory per bench batch: `results.tsv`
(one line per run, the grade from `check.sh`), and per run a directory holding
`row.out` (the row's stdout) and, for router rows, `state/jobs.jsonl`.
GPU batches add `power.jsonl` (GPU watts) and `idle.tsv` (loaded-idle
windows); the DeepSeek forwarder adds `usage.jsonl`.

This module joins each run to its Claude usage, its router jobs, its worker
dollars and its marginal GPU energy, writes `runs.tsv` (one line per run) and
`rows.tsv` (one line per row), and prints `rows.tsv` as a markdown table.

Why the energy is marginal: it is the energy above the GPU's idle draw with the
model loaded, taken from the last idle window before the job, because the
machine is on anyway; it is GPU only, so it is a lower bound; hardware is not
counted.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

from skrode import jobs

TARIFF_BRL_KWH = 0.89181  # the user's electricity tariff, R$ per kWh
BRL_PER_USD = 5.11
GPU_ROWS = ("opencode-bonsai", "router-bonsai")
WORKER_USD_ROW = "router-flash"
BASELINE_ROW = "claude-sonnet"
RUN_HEADER = (
    "batch", "row", "task", "run", "check", "minutes", "claude_usd", "claude_tokens",
    "claude_turns", "jobs", "job_status", "job_turns", "local_tokens", "worker_usd",
    "gpu_wh", "power_usd", "net_claude_usd",
)
ROW_HEADER = (
    "row", "runs", "passes", "pass_rate", "run_spread", "tasks_passed", "median_minutes",
    "median_claude_usd", "median_net_claude_usd", "worker_usd", "gpu_wh", "power_usd",
)
_TOKEN_FIELDS = (
    "inputTokens", "outputTokens",
    "cacheReadInputTokens", "cacheCreationInputTokens",
)


def claude_usage(row_out: Path) -> dict[str, float] | None:
    """Usage from the last JSON `result` line of a Claude stream-json `row.out`.

    Sums `costUSD` over every model for `usd`, the four token fields over every
    model for `tokens`, and takes `num_turns`. Returns `None` when the file is
    missing or has no result line.
    """
    try:
        text = row_out.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    result = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result = obj
    if result is None:
        return None
    total_usd = 0.0
    total_tokens = 0
    model_usage = result.get("modelUsage")
    if isinstance(model_usage, dict):
        for entry in model_usage.values():
            if not isinstance(entry, dict):
                continue
            total_usd += float(entry.get("costUSD", 0) or 0)
            for field in _TOKEN_FIELDS:
                total_tokens += int(entry.get(field) or 0)
    return {
        "usd": total_usd,
        "tokens": total_tokens,
        "turns": int(result.get("num_turns", 0) or 0),
    }


def _local_tokens(record: dict) -> int:
    tokens = record.get("local_tokens")
    if not isinstance(tokens, dict):
        return 0
    total = 0
    for field in ("prompt", "completion"):
        value = tokens.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def job_totals(state_dir: Path) -> dict[str, float] | None:
    """Aggregate `state/jobs.jsonl`: job count, last status, sum of turns and
    sum of local tokens. `None` when there are no records."""
    records = jobs.load_records(state_dir)
    if not records:
        return None
    total_turns = 0
    total_tokens = 0
    for record in records:
        turns = record.get("turns")
        if isinstance(turns, int) and not isinstance(turns, bool):
            total_turns += turns
        total_tokens += _local_tokens(record)
    return {
        "jobs": len(records),
        "status": str(records[-1].get("status", "-")),
        "turns": total_turns,
        "local_tokens": total_tokens,
    }


def worker_usd(usage: Path, tag: str) -> float | None:
    """Sum `cost_usd` over `event == "call"` lines whose `tag` matches.

    `None` when the file is missing; 0.0 when no matching calls exist.
    """
    try:
        text = usage.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    total = 0.0
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("event") == "call" and obj.get("tag") == tag:
            total += float(obj.get("cost_usd", 0) or 0)
    return total


def _read_jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    objects = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def read_power(path: Path) -> list[tuple[float, float]]:
    """`(t, w)` pairs from a `power.jsonl`, sorted by time; `[]` when the file
    is missing or has no usable readings."""
    samples = []
    for obj in _read_jsonl(path):
        try:
            samples.append((float(obj["t"]), float(obj["w"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(samples)


def read_idle(path: Path) -> list[tuple[float, float]]:
    """`(start, end)` per non-empty `idle<TAB>start<TAB>end` line, in file
    order; `[]` when the file is missing."""
    windows = []
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    for line in text.split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split("\t")
        if len(parts) < 3 or parts[0] != "idle":
            continue
        try:
            windows.append((float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return windows


def window(
    samples: list[tuple[float, float]], start: float, end: float
) -> list[tuple[float, float]]:
    """Samples with `start <= t <= end`."""
    return [sample for sample in samples if start <= sample[0] <= end]


def joules(samples: list[tuple[float, float]]) -> float:
    """Trapezoid rule over the real times."""
    total = 0.0
    for (t0, w0), (t1, w1) in itertools.pairwise(samples):
        total += (t1 - t0) * (w0 + w1) / 2
    return total


def marginal_wh(
    samples: list[tuple[float, float]],
    idle: list[tuple[float, float]],
    start: float,
    end: float,
) -> float | None:
    """Wh of draw above the idle draw in `[start, end]`.

    The idle window is the last one in `idle` whose end is `<= start`; `None`
    when there is none, or when that window or `[start, end]` holds fewer than
    2 samples. Otherwise `idle_w` is the average draw over the idle window and
    the result is the excess energy above that idle draw, in watt-hours.
    """
    idle_window = None
    for win_start, win_end in idle:
        if win_end <= start:
            idle_window = (win_start, win_end)
    if idle_window is None:
        return None
    idle_samples = window(samples, idle_window[0], idle_window[1])
    part = window(samples, start, end)
    if len(idle_samples) < 2 or len(part) < 2:
        return None
    idle_span = idle_samples[-1][0] - idle_samples[0][0]
    if idle_span <= 0:
        return None
    idle_w = joules(idle_samples) / idle_span
    part_span = part[-1][0] - part[0][0]
    return max(joules(part) - idle_w * part_span, 0.0) / 3600


def _read_results(batch: Path) -> list[dict[str, str]]:
    """Data rows of `batch/results.tsv`, in file order, as dicts keyed by
    header column names."""
    path = batch / "results.tsv"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    fields = [line.split("\t") for line in text.split("\n") if line]
    if len(fields) < 2:
        return []
    header = fields[0]
    return [dict(zip(header, row, strict=False)) for row in fields[1:]]


def summarize(
    batches: list[Path],
    tariff: float = TARIFF_BRL_KWH,
    brl_per_usd: float = BRL_PER_USD,
) -> list[dict[str, str]]:
    """One dict per `results.tsv` line, batches in the order given, lines in
    file order; keys exactly `RUN_HEADER`, in that order; `"-"` when there is
    nothing to show. Each batch reads its own `power.jsonl`, `idle.tsv` and
    `usage.jsonl`."""
    baseline_usd: dict[str, list[float]] = {}
    for batch in batches:
        for line in _read_results(batch):
            if line["row"] != BASELINE_ROW:
                continue
            usage = claude_usage(batch / line["dir"] / "row.out")
            if usage is not None:
                baseline_usd.setdefault(line["task"], []).append(usage["usd"])
    baselines = {
        task: statistics.median(values) for task, values in baseline_usd.items()
    }

    runs: list[dict[str, str]] = []
    for batch in batches:
        power = read_power(batch / "power.jsonl")
        idle = read_idle(batch / "idle.tsv")
        for line in _read_results(batch):
            out = {key: "-" for key in RUN_HEADER}
            out["batch"] = batch.name
            out["row"] = line["row"]
            out["task"] = line["task"]
            out["run"] = line["run"]
            out["check"] = line["check"]
            out["minutes"] = line["minutes"]

            usage = claude_usage(batch / line["dir"] / "row.out")
            if usage is not None:
                out["claude_usd"] = f"{usage['usd']:.4f}"
                out["claude_tokens"] = str(usage["tokens"])
                out["claude_turns"] = str(usage["turns"])

            totals = job_totals(batch / line["dir"] / "state")
            if totals is not None:
                out["jobs"] = str(totals["jobs"])
                out["job_status"] = str(totals["status"])
                out["job_turns"] = str(totals["turns"])
                out["local_tokens"] = str(totals["local_tokens"])

            if line["row"] == WORKER_USD_ROW:
                cost = worker_usd(batch / "usage.jsonl", f"{line['task']}.r{line['run']}")
                out["worker_usd"] = f"{cost:.4f}" if cost is not None else "-"

            if line["row"] in GPU_ROWS:
                wh = marginal_wh(power, idle, float(line["start"]), float(line["end"]))
                if wh is not None:
                    out["gpu_wh"] = f"{wh:.2f}"
                    out["power_usd"] = f"{wh / 1000 * tariff / brl_per_usd:.4f}"

            baseline = baselines.get(line["task"])
            if usage is not None and baseline is not None:
                out["net_claude_usd"] = f"{baseline - usage['usd']:.4f}"

            runs.append(out)
    return runs


def summarize_rows(runs: list[dict[str, str]]) -> list[dict[str, str]]:
    """One dict per row name, in order of first appearance; keys exactly
    `ROW_HEADER`."""
    grouped: dict[str, list[dict[str, str]]] = {}
    for run in runs:
        grouped.setdefault(run["row"], []).append(run)

    rows: list[dict[str, str]] = []
    for name, row_runs in grouped.items():
        runs_count = len(row_runs)
        passes = sum(1 for run in row_runs if run["check"] == "pass")
        run_groups: dict[str, list[bool]] = {}
        for run in row_runs:
            run_groups.setdefault(run["run"], []).append(run["check"] == "pass")
        run_rates = [sum(group) / len(group) for group in run_groups.values()]
        run_spread = f"{min(run_rates):.2f}-{max(run_rates):.2f}"
        tasks = {run["task"] for run in row_runs}
        tasks_passed = sum(
            1 for task in tasks
            if any(run["task"] == task and run["check"] == "pass" for run in row_runs)
        )
        rows.append({
            "row": name,
            "runs": str(runs_count),
            "passes": str(passes),
            "pass_rate": f"{passes / runs_count:.2f}",
            "run_spread": run_spread,
            "tasks_passed": f"{tasks_passed}/{len(tasks)}",
            "median_minutes": _median(row_runs, "minutes", ".1f"),
            "median_claude_usd": _median(row_runs, "claude_usd", ".4f"),
            "median_net_claude_usd": _median(row_runs, "net_claude_usd", ".4f"),
            "worker_usd": _sum(row_runs, "worker_usd", ".4f"),
            "gpu_wh": _sum(row_runs, "gpu_wh", ".2f"),
            "power_usd": _sum(row_runs, "power_usd", ".4f"),
        })
    return rows


def _as_float(value: str) -> float | None:
    if value == "-":
        return None
    return float(value)


def _median(runs: list[dict[str, str]], field: str, fmt: str) -> str:
    values = [v for v in (_as_float(run[field]) for run in runs) if v is not None]
    if not values:
        return "-"
    return format(statistics.median(values), fmt)


def _sum(runs: list[dict[str, str]], field: str, fmt: str) -> str:
    values = [v for v in (_as_float(run[field]) for run in runs) if v is not None]
    if not values:
        return "-"
    return format(sum(values), fmt)


def _write_tsv(path: Path, header: tuple[str, ...], records: list[dict[str, str]]) -> None:
    lines = ["\t".join(header)]
    for record in records:
        lines.append("\t".join(record[key] for key in header))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m skrode.benchsummary")
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tariff", type=float, default=TARIFF_BRL_KWH)
    parser.add_argument("--brl-per-usd", type=float, default=BRL_PER_USD)
    args = parser.parse_args(argv)
    batch_paths = [Path(batch).resolve() for batch in args.batches]
    runs = summarize(batch_paths, tariff=args.tariff, brl_per_usd=args.brl_per_usd)
    rows = summarize_rows(runs)
    args.out.mkdir(parents=True, exist_ok=True)
    _write_tsv(args.out / "runs.tsv", RUN_HEADER, runs)
    _write_tsv(args.out / "rows.tsv", ROW_HEADER, rows)
    print("| " + " | ".join(ROW_HEADER) + " |")
    print("|" + " --- |" * len(ROW_HEADER))
    for row in rows:
        print("| " + " | ".join(row[key] for key in ROW_HEADER) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
