"""The acceptance checks: the task's commands, run by the router in the job's worktree, and the scope rules for what an attempt changed."""  # noqa: E501

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from skrode.tools import HIDDEN_ENV_PREFIXES, matches, sandbox_argv

CHECK_TIMEOUT_S = 600
TAIL_LINES = 200
# a check can print gigabytes; read back only the end of its log
TAIL_BYTES = 65_536
RETRY_TAIL_CHARS = 12_000
RECORD_TAIL_CHARS = 2_000


@dataclass(frozen=True)
class CheckResult:
    command: str
    exit_code: int
    timed_out: bool
    duration_s: float
    tail: str
    log: Path

    @property
    def failed(self) -> bool:
        return self.timed_out or self.exit_code != 0


def read_tail(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size == 0:
        return ""
    if size <= TAIL_BYTES:
        with path.open("rb") as f:
            data = f.read()
    else:
        with path.open("rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
        # the seek can land mid-line; drop that partial first line
        cut = data.find(b"\n")
        if cut != -1:
            data = data[cut + 1:]
    if not data:
        return ""
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-TAIL_LINES:])[-RETRY_TAIL_CHARS:]


def run_check(
    worktree: Path,
    command: str,
    log: Path,
    *,
    timeout_s: int = CHECK_TIMEOUT_S,
    sandbox: bool = True,
) -> CheckResult:
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = sandbox_argv(worktree, command) if sandbox else ["bash", "-c", command]
    env = {k: v for k, v in os.environ.items() if not k.startswith(HIDDEN_ENV_PREFIXES)}
    start = time.monotonic()
    timed_out = False
    with log.open("wb") as out:
        if not worktree.is_dir():
            out.write((f"worktree not found: {worktree}\n").encode())
            out.flush()
            return CheckResult(
                command,
                127,
                False,
                round(time.monotonic() - start, 1),
                read_tail(log),
                log,
            )
        try:
            proc = subprocess.Popen(
                argv,
                cwd=worktree,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                # its own process group, so a timeout kills the command's children too
                start_new_session=True,
                env=env,
            )
        except FileNotFoundError:
            out.write((f"{argv[0]} is not available\n").encode())
            out.flush()
            elapsed = time.monotonic() - start
            return CheckResult(command, 127, False, round(elapsed, 1), read_tail(log), log)
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    return CheckResult(
        command,
        proc.returncode,
        timed_out,
        round(time.monotonic() - start, 1),
        read_tail(log),
        log,
    )


def run_checks(
    worktree: Path,
    commands: list[str],
    log_dir: Path,
    attempt: int,
    *,
    timeout_s: int = CHECK_TIMEOUT_S,
    sandbox: bool = True,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for i, command in enumerate(commands, start=1):
        results.append(
            run_check(
                worktree,
                command,
                log_dir / f"check-{attempt}-{i}.log",
                timeout_s=timeout_s,
                sandbox=sandbox,
            )
        )
    return results


def first_failure(results: list[CheckResult]) -> CheckResult | None:
    for result in results:
        if result.failed:
            return result
    return None


def record_checks(results: list[CheckResult]) -> list[dict]:
    records: list[dict] = []
    seen_failure = False
    for result in results:
        if not seen_failure and result.failed:
            seen_failure = True
            tail = result.tail[-RECORD_TAIL_CHARS:]
        else:
            tail = ""
        records.append(
            {
                "command": result.command,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_s": result.duration_s,
                "tail": tail,
            }
        )
    return records


def scope_violation(
    changed: list[str],
    files: list[str],
    must_not_touch: list[str],
) -> tuple[str, str] | None:
    for path in changed:
        if matches(path, must_not_touch):
            return ("must_not_touch", path)
    for path in changed:
        if not matches(path, files):
            return ("outside_files", path)
    return None
