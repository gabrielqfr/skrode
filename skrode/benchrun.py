"""Run one bench row over prepared tasks and grade every run the same way (D24).

Each run gets a fresh worktree at the task's bench base, the task's setup, SPEC.md holding the
task text, then the row's command. The grade is the task's own check.sh in that worktree, the
same for every row, so a row is judged on what it left behind and not on what it reported.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from skrode.benchprep import PrepError, _git
from skrode.config import load_config

ROWS = (
    "opencode-bonsai",
    "router-bonsai",
    "router-flash",
    "claude-sonnet",
    "claude-haiku-subagent",
)
SONNET = "claude-sonnet-5-5"
FLASH_URL = "http://127.0.0.1:8091/bench"
CHECK_TIMEOUT_S = 900
KILL_GRACE_S = 10
RESULTS_HEADER = (
    "row", "task", "run", "check", "exit", "minutes", "start", "end", "extra", "missing", "dir"
)
CLAUDE_TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "Glob")
ROUTER_TOOLS = (
    "mcp__skrode__run_local_subagent",
    "mcp__skrode__wait_jobs",
    "mcp__skrode__job_status",
    "mcp__skrode__cancel_job",
)
PLAIN_PROMPT = "Implement the spec in SPEC.md. Run the checks it lists and stop when they pass."
SUBAGENT_PROMPT = (
    "Implement the spec in SPEC.md by delegating the work to the implementer subagent: give it "
    "the spec's full text. When it returns, review its changes and run the checks the spec "
    "lists. If they fail, either send the implementer the failure output or fix the code "
    "yourself. Stop when the checks pass."
)
ROUTER_PROMPT = (
    "Implement the spec in SPEC.md by delegating it to the local model with the skrode "
    "tool run_local_subagent. Arguments: repo {repo}, base {base}, task the full text of "
    "SPEC.md, files the paths under its \"Files to touch\", and acceptance_criteria.commands "
    "the commands under \"Commands that must pass\". Wait for the job with wait_jobs. If it "
    "passed, review `git diff {base} <its branch>` and bring the branch into this working "
    "tree with `git merge --ff-only <its branch>`. If it escalated or failed, read its "
    "record, then either submit a revised task or finish the work yourself. Stop when the "
    "spec's checks pass in this working tree."
)
IMPLEMENTER_AGENT = {
    "implementer": {
        "description": "Implements a written spec in this working tree and runs its checks.",
        "prompt": (
            "You implement one spec in this working tree. Touch only the files it names, run "
            "the checks it lists, and finish with what you changed and the checks' result."
        ),
        "model": "haiku",
        "tools": list(CLAUDE_TOOLS),
    }
}


def read_manifest(prep: Path) -> list[dict[str, str]]:
    """Read the prepared task manifest into one dict per non-empty data line."""
    lines = (prep / "tasks.tsv").read_text(encoding="utf-8").splitlines()
    names = lines[0].split("\t")
    return [
        dict(zip(names, line.split("\t"), strict=True))
        for line in lines[1:]
        if line
    ]


def claude_command(
    prompt: str, budget_usd: float, extra: list[str], tools: tuple[str, ...]
) -> list[str]:
    """Claude argv for a prompt; --allowedTools goes last so it takes the rest."""
    argv = [
        "claude",
        "-p",
        prompt,
        "--model",
        SONNET,
        "--output-format",
        "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--setting-sources",
        "project,local",
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        str(budget_usd),
    ]
    argv.extend(extra)
    argv.append("--allowedTools")
    argv.extend(tools)
    return argv


def row_command(
    row: str,
    *,
    task: dict[str, str],
    run_dir: Path,
    prep: Path,
    router_repo: Path,
    budget_usd: float,
) -> tuple[list[str], dict[str, str]]:
    """The argv and extra environment for one run of a row."""
    if row == "opencode-bonsai":
        return (
            ["opencode", "run", PLAIN_PROMPT],
            {
                "XDG_CONFIG_HOME": str(router_repo / "bench"),
                "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
            },
        )
    if row == "claude-sonnet":
        return claude_command(PLAIN_PROMPT, budget_usd, [], CLAUDE_TOOLS), {}
    if row == "claude-haiku-subagent":
        return (
            claude_command(
                SUBAGENT_PROMPT,
                budget_usd,
                ["--agents", json.dumps(IMPLEMENTER_AGENT)],
                (*CLAUDE_TOOLS, "Agent"),
            ),
            {},
        )
    if row in ("router-bonsai", "router-flash"):
        server_args = [
            "--directory",
            str(router_repo),
            "run",
            "skrode",
            "--config",
            str(prep / "config.toml"),
            "--state-dir",
            str(run_dir / "state"),
        ]
        if row == "router-flash":
            server_args += [
                "--model",
                "deepseek-flash",
                "--profile",
                "deepseek-flash",
                "--base-url",
                f"{FLASH_URL}/{task['id']}.{run_dir.name}",
            ]
        mcp_path = run_dir / "mcp.json"
        mcp = {
            "mcpServers": {"skrode": {"command": "uv", "args": server_args}}
        }
        mcp_path.write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
        return (
            claude_command(
                ROUTER_PROMPT.format(repo=task["repo"], base=task["base"]),
                budget_usd,
                ["--mcp-config", str(mcp_path)],
                (*CLAUDE_TOOLS, *ROUTER_TOOLS),
            ),
            {},
        )
    raise ValueError(f"unknown row {row!r}")


def run_command(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    out: Path,
    err: Path,
    timeout_s: float,
) -> int:
    """Run argv in its own process group and kill the group on timeout."""
    with open(out, "w", encoding="utf-8") as out_file, open(
        err, "w", encoding="utf-8"
    ) as err_file:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env={**os.environ, **env},
            stdout=out_file,
            stderr=err_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            return 124


def remove_worktree(repo: Path, wt: Path) -> None:
    """Drop a bench worktree, falling back to rmtree plus prune on failure."""
    try:
        _git(repo, "worktree", "remove", "--force", str(wt))
    except PrepError:
        shutil.rmtree(wt, ignore_errors=True)
        _git(repo, "worktree", "prune")


def run_once(
    row: str,
    task: dict[str, str],
    k: int,
    *,
    out: Path,
    worktrees: Path,
    prep: Path,
    router_repo: Path,
    timeout_min: float,
    budget_usd: float,
    keep: bool = False,
) -> dict[str, str]:
    """One run of a row over a prepared task; returns one results.tsv row."""
    repo = Path(task["repo"])
    base = task["base"]
    task_id = task["id"]
    run_dir = out / row / task_id / f"r{k}"
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    wt = worktrees / f"{row}-{task_id}-r{k}"
    if wt.exists():
        remove_worktree(repo, wt)
    worktrees.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "prune")
    _git(repo, "worktree", "add", "--detach", str(wt), base)
    args = json.loads((prep / task_id / "args.json").read_text(encoding="utf-8"))
    try:
        setup_log = run_dir / "setup.log"
        commands = load_config(prep / "config.toml").get(repo.resolve(), ())
        for command in commands:
            with open(setup_log, "a", encoding="utf-8") as log:
                result = subprocess.run(command, shell=True, cwd=wt,
                                        stdout=log, stderr=log)
            if result.returncode:
                now = f"{time.time():.3f}"
                return {
                    "row": row,
                    "task": task_id,
                    "run": str(k),
                    "check": "setup-failed",
                    "exit": "-",
                    "minutes": "0.0",
                    "start": now,
                    "end": now,
                    "extra": "-",
                    "missing": "-",
                    "dir": run_dir.relative_to(out).as_posix(),
                }
        (wt / "SPEC.md").write_text(args["task"], encoding="utf-8")
        argv, env = row_command(
            row,
            task=task,
            run_dir=run_dir,
            prep=prep,
            router_repo=router_repo,
            budget_usd=budget_usd,
        )
        start = time.time()
        code = run_command(
            argv,
            wt,
            {**env, "PWD": str(wt)},
            run_dir / "row.out",
            run_dir / "row.err",
            timeout_min * 60,
        )
        end = time.time()
        (wt / "SPEC.md").unlink(missing_ok=True)
        _git(wt, "add", "-A", "-N")
        (run_dir / "agent.diff").write_text(_git(wt, "diff", base), encoding="utf-8")
        touched = set(_git(wt, "diff", "--name-only", base).split())
        task_dir = Path(task["task_dir"])
        check = "fail"
        with open(run_dir / "check.log", "w", encoding="utf-8") as log:
            try:
                result = subprocess.run(
                    ["bash", str(task_dir / "check.sh")],
                    cwd=wt,
                    env={
                        **os.environ,
                        "TASK_DIR": str(task_dir),
                        "HIDDEN_DIR": task.get("hidden", str(task_dir / "hidden")),
                    },
                    stdout=log,
                    stderr=log,
                    timeout=CHECK_TIMEOUT_S,
                )
                check = "pass" if result.returncode == 0 else "fail"
            except subprocess.TimeoutExpired:
                check = "fail"
        named = set(args["files"])
        return {
            "row": row,
            "task": task_id,
            "run": str(k),
            "check": check,
            "exit": str(code),
            "minutes": f"{(end - start) / 60:.1f}",
            "start": f"{start:.3f}",
            "end": f"{end:.3f}",
            "extra": ",".join(sorted(touched - named)) or "-",
            "missing": ",".join(sorted(named - touched)) or "-",
            "dir": run_dir.relative_to(out).as_posix(),
        }
    finally:
        if not keep:
            remove_worktree(repo, wt)


def done_runs(results: Path) -> set[tuple[str, str, str]]:
    """(row, task, run) pairs already recorded in the results file."""
    if not results.exists():
        return set()
    return {
        tuple(line.split("\t")[:3])
        for line in results.read_text(encoding="utf-8").splitlines()[1:]
        if line
    }


def append_result(results: Path, result: dict[str, str]) -> None:
    """Append one run's result row, writing the header on the first call."""
    fresh = not results.exists()
    with open(results, "a", encoding="utf-8") as log:
        if fresh:
            log.write("\t".join(RESULTS_HEADER) + "\n")
        log.write("\t".join(str(result[key]) for key in RESULTS_HEADER) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m skrode.benchrun")
    parser.add_argument("--prep", type=Path, required=True)
    parser.add_argument("--row", choices=ROWS, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--worktrees", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout-min", type=float, default=45)
    parser.add_argument("--budget-usd", type=float, default=5)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("ids", nargs="*")
    options = parser.parse_args(argv)
    options.prep = options.prep.resolve()
    options.out = options.out.resolve()
    options.worktrees = options.worktrees.resolve()
    tasks = read_manifest(options.prep)
    if options.ids:
        tasks = [task for task in tasks if task["id"] in options.ids]
    options.out.mkdir(parents=True, exist_ok=True)
    router_repo = Path(__file__).resolve().parents[1]
    results = options.out / "results.tsv"
    done = done_runs(results)
    for k in range(1, options.runs + 1):
        for task in tasks:
            if (options.row, task["id"], str(k)) in done:
                continue
            result = run_once(
                options.row,
                task,
                k,
                out=options.out,
                worktrees=options.worktrees,
                prep=options.prep,
                router_repo=router_repo,
                timeout_min=options.timeout_min,
                budget_usd=options.budget_usd,
                keep=options.keep,
            )
            append_result(results, result)
            print("\t".join(str(result[key]) for key in RESULTS_HEADER), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
