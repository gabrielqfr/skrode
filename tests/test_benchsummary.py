import json
from pathlib import Path

import pytest

from skrode.benchrun import RESULTS_HEADER
from skrode.benchsummary import (
    BRL_PER_USD,
    ROW_HEADER,
    RUN_HEADER,
    TARIFF_BRL_KWH,
    claude_usage,
    job_totals,
    main,
    marginal_wh,
    read_idle,
    read_power,
    summarize,
    summarize_rows,
    worker_usd,
)


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def result_line(models: dict[str, tuple[int, int, int, int, float]], turns: int) -> str:
    usage = {
        name: {
            "inputTokens": a,
            "outputTokens": b,
            "cacheReadInputTokens": c,
            "cacheCreationInputTokens": d,
            "costUSD": usd,
            "contextWindow": 1000000,
        }
        for name, (a, b, c, d, usd) in models.items()
    }
    total = sum(usd for *_, usd in models.values())
    return json.dumps(
        {"type": "result", "subtype": "success", "num_turns": turns, "total_cost_usd": total,
         "modelUsage": usage}
    )


def stream(path: Path, models: dict[str, tuple[int, int, int, int, float]], turns: int) -> None:
    write_lines(path, [
        json.dumps({"type": "system", "subtype": "init"}),
        "not json at all",
        json.dumps({"type": "assistant", "message": {"content": []}}),
        result_line(models, turns),
    ])


def record(status: str, turns: int, prompt: int, completion: int) -> str:
    return json.dumps({
        "record_version": 1, "job_id": f"j-{status}-{turns}", "status": status, "attempts": 1,
        "turns": turns, "local_tokens": {"prompt": prompt, "completion": completion},
        "wall_s": 10.0,
    })


def call(tag: str, usd: float) -> str:
    return json.dumps({"event": "call", "tag": tag, "model": "deepseek-flash", "cost_usd": usd})


def power(path: Path, spans: list[tuple[int, int, float]]) -> None:
    """One reading a second over each (start, end, watts) span, written out of order."""
    lines = [
        json.dumps({"t": float(t), "w": w})
        for start, end, w in reversed(spans)
        for t in range(start, end + 1)
    ]
    write_lines(path, lines)


def results(batch: Path, rows: list[tuple[str, str, int, str, float, float]]) -> None:
    lines = ["\t".join(RESULTS_HEADER)]
    for row, task, run, check, start, end in rows:
        values = {
            "row": row, "task": task, "run": str(run), "check": check,
            "exit": "0" if check == "pass" else "1",
            "minutes": f"{(end - start) / 60:.1f}", "start": f"{start:.3f}", "end": f"{end:.3f}",
            "extra": "-", "missing": "-", "dir": f"{row}/{task}/r{run}",
        }
        lines.append("\t".join(values[key] for key in RESULTS_HEADER))
    write_lines(batch / "results.tsv", lines)


def run_dir(batch: Path, row: str, task: str, run: int) -> Path:
    return batch / row / task / f"r{run}"


def test_claude_usage_sums_every_model_from_the_last_result_line(tmp_path: Path) -> None:
    out = tmp_path / "row.out"
    stream(out, {"claude-sonnet-5-5": (1, 1, 1, 1, 9.0)}, 99)
    lines = out.read_text(encoding="utf-8").splitlines()
    lines.append(result_line({
        "claude-sonnet-5-5": (12, 2076, 119429, 11164, 0.0773829),
        "claude-haiku-5-5": (12, 3857, 92815, 23986, 0.0058561),
    }, 17))
    lines.append("trailing text")
    write_lines(out, lines)
    usage = claude_usage(out)
    assert usage is not None
    assert usage["usd"] == pytest.approx(0.0773829 + 0.0058561)
    assert usage["tokens"] == 12 + 2076 + 119429 + 11164 + 12 + 3857 + 92815 + 23986
    assert usage["turns"] == 17


def test_claude_usage_is_none_without_a_result_line(tmp_path: Path) -> None:
    out = tmp_path / "row.out"
    write_lines(out, ["## Objective", "- Implement the spec", "MAXIMUM STEPS REACHED"])
    assert claude_usage(out) is None
    assert claude_usage(tmp_path / "missing.out") is None


def test_job_totals_counts_jobs_sums_turns_and_tokens_and_keeps_the_last_status(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    write_lines(state / "jobs.jsonl", [
        record("escalate", 40, 1000, 200),
        record("passed", 12, 300, 50),
    ])
    assert job_totals(state) == {"jobs": 2, "status": "passed", "turns": 52, "local_tokens": 1550}
    assert job_totals(tmp_path / "no-state") is None


def test_worker_usd_sums_the_calls_carrying_the_tag(tmp_path: Path) -> None:
    usage = tmp_path / "usage.jsonl"
    write_lines(usage, [
        json.dumps({"event": "error", "tag": "a.r1", "status": 500, "cost_usd": 5.0}),
        call("a.r1", 0.002),
        call("a.r2", 0.4),
        call("b.r1", 0.3),
        "garbage",
        call("a.r1", 0.003),
    ])
    assert worker_usd(usage, "a.r1") == pytest.approx(0.005)
    assert worker_usd(usage, "c.r1") == 0.0
    assert worker_usd(tmp_path / "none.jsonl", "a.r1") is None


def test_read_power_sorts_by_time_and_read_idle_keeps_every_window(tmp_path: Path) -> None:
    power(tmp_path / "power.jsonl", [(0, 2, 20.0), (10, 11, 50.0)])
    assert read_power(tmp_path / "power.jsonl") == [
        (0.0, 20.0), (1.0, 20.0), (2.0, 20.0), (10.0, 50.0), (11.0, 50.0)
    ]
    write_lines(tmp_path / "idle.tsv", ["idle\t0.5\t10.25", "idle\t100\t110"])
    assert read_idle(tmp_path / "idle.tsv") == [(0.5, 10.25), (100.0, 110.0)]
    assert read_power(tmp_path / "missing.jsonl") == []
    assert read_idle(tmp_path / "missing.tsv") == []


def test_marginal_wh_measures_above_the_last_idle_window_before_the_job() -> None:
    samples = sorted(
        (float(t), w)
        for start, end, w in [
            (0, 10, 20.0), (50, 86, 120.0), (100, 110, 30.0), (200, 236, 120.0),
            (300, 336, 10.0),
        ]
        for t in range(start, end + 1)
    )
    idle = [(0.0, 10.0), (100.0, 110.0)]
    assert marginal_wh(samples, idle, 50.0, 86.0) == pytest.approx(100 * 36 / 3600)
    assert marginal_wh(samples, idle, 200.0, 236.0) == pytest.approx(90 * 36 / 3600)
    assert marginal_wh(samples, idle, 300.0, 336.0) == 0.0
    assert marginal_wh(samples, [(100.0, 110.0)], 50.0, 86.0) is None
    assert marginal_wh(samples, idle, 400.0, 460.0) is None
    assert marginal_wh(samples, [], 200.0, 236.0) is None


def build_batch(batch: Path) -> None:
    results(batch, [
        ("claude-sonnet", "t1", 1, "pass", 1000.0, 1060.0),
        ("claude-sonnet", "t1", 2, "fail", 1100.0, 1190.0),
        ("router-flash", "t1", 1, "pass", 1200.0, 1290.0),
        ("router-flash", "t2", 1, "fail", 1300.0, 1330.0),
        ("router-bonsai", "t1", 1, "pass", 2000.0, 2360.0),
        ("opencode-bonsai", "t1", 1, "pass", 3000.0, 3180.0),
    ])
    sonnet = "claude-sonnet-5-5"
    stream(run_dir(batch, "claude-sonnet", "t1", 1) / "row.out", {sonnet: (10, 20, 30, 40, 0.1)}, 5)
    stream(run_dir(batch, "claude-sonnet", "t1", 2) / "row.out", {sonnet: (1, 2, 3, 4, 0.3)}, 9)
    stream(run_dir(batch, "router-flash", "t1", 1) / "row.out", {sonnet: (5, 5, 5, 5, 0.05)}, 4)
    write_lines(run_dir(batch, "router-flash", "t1", 1) / "state" / "jobs.jsonl", [
        record("passed", 18, 9000, 700),
    ])
    stream(run_dir(batch, "router-flash", "t2", 1) / "row.out", {sonnet: (5, 5, 5, 5, 0.25)}, 6)
    stream(run_dir(batch, "router-bonsai", "t1", 1) / "row.out", {sonnet: (1, 1, 1, 1, 0.12)}, 3)
    write_lines(run_dir(batch, "router-bonsai", "t1", 1) / "state" / "jobs.jsonl", [
        record("failed", 40, 5000, 500),
        record("passed", 22, 3000, 300),
    ])
    write_lines(run_dir(batch, "opencode-bonsai", "t1", 1) / "row.out", ["plain opencode text"])
    write_lines(batch / "usage.jsonl", [call("t1.r1", 0.005), call("t1.r1", 0.007)])
    write_lines(batch / "idle.tsv", ["idle\t1900\t1960"])
    power(batch / "power.jsonl", [(1900, 1960, 40.0), (2000, 2360, 400.0), (3000, 3180, 220.0)])


def by_key(runs: list[dict[str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    return {(run["row"], run["task"], run["run"]): run for run in runs}


def test_summarize_fills_each_kind_of_row(tmp_path: Path) -> None:
    batch = tmp_path / "b1"
    build_batch(batch)
    runs = summarize([batch])
    assert [list(run) for run in runs] == [list(RUN_HEADER)] * 6
    got = by_key(runs)
    assert got["claude-sonnet", "t1", "1"] == {
        "batch": "b1", "row": "claude-sonnet", "task": "t1", "run": "1", "check": "pass",
        "minutes": "1.0", "claude_usd": "0.1000", "claude_tokens": "100", "claude_turns": "5",
        "jobs": "-", "job_status": "-", "job_turns": "-", "local_tokens": "-",
        "worker_usd": "-", "gpu_wh": "-", "power_usd": "-", "net_claude_usd": "0.1000",
    }
    assert got["claude-sonnet", "t1", "2"]["net_claude_usd"] == "-0.1000"
    flash = got["router-flash", "t1", "1"]
    assert (flash["claude_usd"], flash["net_claude_usd"]) == ("0.0500", "0.1500")
    assert (flash["jobs"], flash["job_status"], flash["job_turns"]) == ("1", "passed", "18")
    assert (flash["local_tokens"], flash["worker_usd"]) == ("9700", "0.0120")
    assert (flash["gpu_wh"], flash["power_usd"]) == ("-", "-")
    other = got["router-flash", "t2", "1"]
    assert (other["check"], other["jobs"], other["worker_usd"]) == ("fail", "-", "0.0000")
    assert other["net_claude_usd"] == "-"
    bonsai = got["router-bonsai", "t1", "1"]
    assert (bonsai["jobs"], bonsai["job_status"], bonsai["job_turns"]) == ("2", "passed", "62")
    assert (bonsai["local_tokens"], bonsai["worker_usd"]) == ("8800", "-")
    assert (bonsai["gpu_wh"], bonsai["power_usd"]) == ("36.00", "0.0063")
    assert bonsai["net_claude_usd"] == "0.0800"
    opencode = got["opencode-bonsai", "t1", "1"]
    assert (opencode["claude_usd"], opencode["claude_tokens"], opencode["claude_turns"]) == (
        "-", "-", "-"
    )
    assert (opencode["gpu_wh"], opencode["net_claude_usd"]) == ("9.00", "-")


def test_summarize_power_price_follows_tariff_and_rate(tmp_path: Path) -> None:
    batch = tmp_path / "b1"
    build_batch(batch)
    runs = by_key(summarize([batch], tariff=1.0, brl_per_usd=1.0))
    assert runs["router-bonsai", "t1", "1"]["power_usd"] == "0.0360"
    assert TARIFF_BRL_KWH == 0.89181
    assert BRL_PER_USD == 5.11


def test_summarize_reads_each_batch_with_its_own_files_and_shares_the_baseline(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    results(first, [("claude-sonnet", "t3", 1, "pass", 10.0, 70.0)])
    stream(run_dir(first, "claude-sonnet", "t3", 1) / "row.out", {"m": (1, 1, 1, 1, 0.5)}, 2)
    results(second, [("router-flash", "t3", 1, "pass", 10.0, 70.0)])
    stream(run_dir(second, "router-flash", "t3", 1) / "row.out", {"m": (1, 1, 1, 1, 0.2)}, 2)
    write_lines(second / "usage.jsonl", [call("t3.r1", 0.01)])
    runs = summarize([first, second])
    assert [(run["batch"], run["row"]) for run in runs] == [
        ("first", "claude-sonnet"), ("second", "router-flash")
    ]
    assert runs[1]["net_claude_usd"] == "0.3000"
    assert runs[1]["worker_usd"] == "0.0100"


def run(row: str, task: str, k: int, check: str, minutes: str, **values: str) -> dict[str, str]:
    base = {key: "-" for key in RUN_HEADER}
    base.update(batch="b", row=row, task=task, run=str(k), check=check, minutes=minutes)
    base.update(values)
    return base


def test_summarize_rows_reports_rate_spread_and_medians() -> None:
    runs = [
        run("router-bonsai", "t1", 1, "pass", "2.0", claude_usd="0.1000",
            net_claude_usd="0.0500", gpu_wh="8.00", power_usd="0.0014"),
        run("router-bonsai", "t2", 1, "pass", "4.0", claude_usd="0.3000",
            net_claude_usd="-0.1000", gpu_wh="2.50", power_usd="0.0004"),
        run("claude-sonnet", "t1", 1, "pass", "0.6", claude_usd="0.1500",
            net_claude_usd="0.0000"),
        run("router-bonsai", "t1", 2, "pass", "3.0", claude_usd="0.2000",
            net_claude_usd="0.0100", gpu_wh="1.00", power_usd="0.0002"),
        run("router-bonsai", "t2", 2, "fail", "9.0", claude_usd="0.5000",
            net_claude_usd="-0.3000", gpu_wh="4.00", power_usd="0.0007"),
        run("opencode-bonsai", "t1", 1, "fail", "22.3", gpu_wh="89.20", power_usd="0.0156"),
        run("opencode-bonsai", "t2", 1, "fail", "20.1", gpu_wh="10.00", power_usd="0.0017"),
        run("router-flash", "t1", 1, "pass", "1.6", claude_usd="0.0700", worker_usd="0.0075"),
        run("router-flash", "t1", 2, "pass", "1.0", claude_usd="0.0900", worker_usd="0.0025"),
    ]
    rows = summarize_rows(runs)
    assert [row["row"] for row in rows] == [
        "router-bonsai", "claude-sonnet", "opencode-bonsai", "router-flash"
    ]
    assert [list(row) for row in rows] == [list(ROW_HEADER)] * 4
    assert rows[0] == {
        "row": "router-bonsai", "runs": "4", "passes": "3", "pass_rate": "0.75",
        "run_spread": "0.50-1.00", "tasks_passed": "2/2", "median_minutes": "3.5",
        "median_claude_usd": "0.2500", "median_net_claude_usd": "-0.0450",
        "worker_usd": "-", "gpu_wh": "15.50", "power_usd": "0.0027",
    }
    assert rows[2] == {
        "row": "opencode-bonsai", "runs": "2", "passes": "0", "pass_rate": "0.00",
        "run_spread": "0.00-0.00", "tasks_passed": "0/2", "median_minutes": "21.2",
        "median_claude_usd": "-", "median_net_claude_usd": "-",
        "worker_usd": "-", "gpu_wh": "99.20", "power_usd": "0.0173",
    }
    assert (rows[3]["tasks_passed"], rows[3]["worker_usd"]) == ("1/1", "0.0100")
    assert rows[3]["median_net_claude_usd"] == "-"


def test_main_writes_runs_and_rows_and_prints_a_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = tmp_path / "b1"
    build_batch(batch)
    out = tmp_path / "summary"
    assert main([str(batch), "--out", str(out)]) == 0
    runs = (out / "runs.tsv").read_text(encoding="utf-8").splitlines()
    assert runs[0] == "\t".join(RUN_HEADER)
    assert len(runs) == 7
    assert runs[1].split("\t")[:5] == ["b1", "claude-sonnet", "t1", "1", "pass"]
    rows = (out / "rows.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "\t".join(ROW_HEADER)
    assert [line.split("\t")[0] for line in rows[1:]] == [
        "claude-sonnet", "router-flash", "router-bonsai", "opencode-bonsai"
    ]
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "| " + " | ".join(ROW_HEADER) + " |"
    assert printed[1] == "|" + " --- |" * len(ROW_HEADER)
    assert printed[2] == "| " + " | ".join(rows[1].split("\t")) + " |"
    assert len(printed) == 6


def test_main_takes_tariff_and_rate(tmp_path: Path) -> None:
    batch = tmp_path / "b1"
    build_batch(batch)
    out = tmp_path / "summary"
    assert main([str(batch), "--out", str(out), "--tariff", "2", "--brl-per-usd", "1"]) == 0
    line = next(
        line.split("\t") for line in (out / "runs.tsv").read_text(encoding="utf-8").splitlines()
        if line.startswith("b1\trouter-bonsai")
    )
    assert line[RUN_HEADER.index("power_usd")] == "0.0720"


def test_summarize_shows_a_dash_for_a_gpu_row_without_power_readings(tmp_path: Path) -> None:
    batch = tmp_path / "b1"
    results(batch, [
        ("router-bonsai", "t1", 1, "pass", 10.0, 70.0),
        ("opencode-bonsai", "t1", 1, "fail", 100.0, 160.0),
    ])
    write_lines(batch / "idle.tsv", ["idle\t0\t5"])
    runs = summarize([batch])
    assert [(run["gpu_wh"], run["power_usd"]) for run in runs] == [("-", "-"), ("-", "-")]
