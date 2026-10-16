"""The acceptance checks, run in a temp directory; no model server, no network."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from skrode.acceptance import (
    RETRY_TAIL_CHARS,
    CheckResult,
    first_failure,
    read_tail,
    record_checks,
    run_check,
    run_checks,
    scope_violation,
)


def test_run_check_passes(tmp_path):
    log = tmp_path / "logs" / "c.log"
    r = run_check(tmp_path, "echo hi", log, sandbox=False)
    assert r.exit_code == 0
    assert r.timed_out is False
    assert r.failed is False
    assert r.tail == "hi"
    assert isinstance(r.duration_s, float)
    assert r.duration_s >= 0
    assert log.read_bytes() == b"hi\n"


def test_run_check_failure_merges_stderr(tmp_path):
    log = tmp_path / "logs" / "c.log"
    r = run_check(tmp_path, "echo out; echo err >&2; exit 3", log, sandbox=False)
    assert r.exit_code == 3
    assert r.failed is True
    assert r.tail.index("out") < r.tail.index("err")


def test_run_check_runs_in_worktree(tmp_path):
    log = tmp_path / "logs" / "c.log"
    r = run_check(tmp_path, "pwd", log, sandbox=False)
    assert r.tail == str(tmp_path)


def test_run_check_timeout_kills_the_group(tmp_path):
    log = tmp_path / "logs" / "c.log"
    start = time.monotonic()
    r = run_check(
        tmp_path,
        "(sleep 2; echo late > late.txt) & sleep 30",
        log,
        timeout_s=1,
        sandbox=False,
    )
    assert time.monotonic() - start < 10
    assert r.timed_out is True
    assert r.failed is True
    time.sleep(3)
    assert not (tmp_path / "late.txt").exists()


def test_run_check_missing_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    log = tmp_path / "logs" / "c.log"
    r = run_check(tmp_path, "echo hi", log, sandbox=False)
    assert r.exit_code == 127
    assert r.timed_out is False
    assert r.tail == "bash is not available"
    assert r.log == log


def test_run_check_missing_bwrap(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    log = tmp_path / "logs" / "c.log"
    r = run_check(tmp_path, "echo hi", log)
    assert r.exit_code == 127
    assert r.timed_out is False
    assert r.tail == "bwrap is not available"


def test_run_check_missing_worktree(tmp_path):
    log = tmp_path / "logs" / "c.log"
    r = run_check(tmp_path / "gone", "true", log)
    assert r.exit_code == 127
    assert r.timed_out is False
    assert r.tail == f"worktree not found: {tmp_path / 'gone'}"


def test_run_check_hides_anthropic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    log = tmp_path / "logs" / "c.log"
    run_check(tmp_path, "env", log, sandbox=False)
    text = log.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text
    assert "PATH=" in text


def _bwrap_ok() -> bool:
    try:
        result = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--unshare-net", "--", "true"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


needs_bwrap = pytest.mark.skipif(not _bwrap_ok(), reason="bwrap not usable")


@needs_bwrap
def test_run_check_has_no_network(tmp_path):
    log = tmp_path / "logs" / "c.log"
    r = run_check(
        tmp_path,
        "tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' '",
        log,
        sandbox=True,
    )
    assert r.exit_code == 0
    assert r.tail == "lo"


@needs_bwrap
def test_run_check_sandbox_keeps_outside_read_only(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    probe = Path(__file__).resolve().parent / f"sandbox-probe-{os.getpid()}.txt"
    log = tmp_path / "logs" / "c.log"
    r = run_check(root, f"echo in > inside.txt; touch {probe}", log)
    try:
        assert r.exit_code != 0
        assert (root / "inside.txt").read_text(encoding="utf-8") == "in\n"
        assert not probe.exists()
    finally:
        if probe.exists():
            probe.unlink()


def test_read_tail_keeps_last_lines(tmp_path):
    log = tmp_path / "big.log"
    log.write_text("\n".join(f"line {i}" for i in range(1, 501)) + "\n", encoding="utf-8")
    assert read_tail(log).splitlines() == [f"line {i}" for i in range(301, 501)]


def test_read_tail_bounds(tmp_path):
    log = tmp_path / "big.log"
    line = "x" * 99 + "\n"
    log.write_bytes((line * 2000).encode("utf-8"))
    assert len(read_tail(log)) <= RETRY_TAIL_CHARS
    assert read_tail(log).endswith("x")
    assert read_tail(tmp_path / "missing.log") == ""


def test_read_tail_drops_cut_line(tmp_path):
    log = tmp_path / "big.log"
    log.write_bytes(b"y" * 100_000 + b"\nend\n")
    assert read_tail(log) == "end"


def test_read_tail_without_newline_keeps_last_chars(tmp_path):
    log = tmp_path / "big.log"
    log.write_bytes(b"a" * 10_000 + b"b" * 60_000)
    assert read_tail(log) == "b" * 12_000


def test_run_checks_runs_all_and_names_logs(tmp_path):
    log_dir = tmp_path / "logs"
    results = run_checks(
        tmp_path,
        ["exit 1", "echo two"],
        log_dir,
        2,
        sandbox=False,
    )
    assert [r.exit_code for r in results] == [1, 0]
    assert (log_dir / "check-2-1.log").exists()
    assert (log_dir / "check-2-2.log").exists()


def test_first_failure(tmp_path):
    a = CheckResult("a", 0, False, 0.0, "", tmp_path)
    b = CheckResult("b", 1, False, 0.0, "", tmp_path)
    c = CheckResult("c", 0, False, 0.0, "", tmp_path)
    assert first_failure([a, b, c]).command == "b"
    assert first_failure([a, c]) is None
    t = CheckResult("t", 0, True, 0.0, "", tmp_path)
    assert first_failure([a, t]).command == "t"


def test_record_checks_tail_only_on_first_failure(tmp_path):
    ok = CheckResult("ok", 0, False, 0.5, "p", tmp_path)
    bad = CheckResult("bad", 1, False, 0.5, "a" * 3000, tmp_path)
    bad2 = CheckResult("bad2", 1, False, 0.5, "b", tmp_path)
    records = record_checks([ok, bad, bad2])
    assert [r["tail"] for r in records] == ["", "a" * 2000, ""]
    expected_keys = ["command", "exit_code", "timed_out", "duration_s", "tail"]
    for r in records:
        assert list(r.keys()) == expected_keys


@pytest.mark.parametrize(
    ("changed", "files", "must_not_touch", "expected"),
    [
        (["a.py"], ["a.py"], [], None),
        (["a.py", "b.py"], ["a.py"], [], ("outside_files", "b.py")),
        (["b.py", "tests/t.py"], ["*.py"], ["tests/"], ("must_not_touch", "tests/t.py")),
        (["b.py", "tests/t.py"], ["a.py"], ["tests/"], ("must_not_touch", "tests/t.py")),
        (["src/x/y.py"], ["src/*"], [], None),
        ([], ["a.py"], ["tests/"], None),
    ],
)
def test_scope_violation(changed, files, must_not_touch, expected):
    assert scope_violation(changed, files, must_not_touch) == expected
