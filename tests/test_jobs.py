"""Tests for router.jobs: the verdict and the record, as pure functions."""

import re
from datetime import datetime
from pathlib import Path

import pytest

from router.acceptance import CheckResult
from router.jobs import (
    RECORD_SCHEMA,
    Attempt,
    build_record,
    new_job_id,
    protected_text,
    validate_record,
    verdict,
)


def _check(command: str = "pytest -q", exit_code: int = 0, timed_out: bool = False) -> CheckResult:
    return CheckResult(command, exit_code, timed_out, 1.0, "out", Path("/tmp/x.log"))


def _attempt(**overrides) -> Attempt:
    values = {
        "stop": "finished",
        "detail": "",
        "report": {"outcome": "done", "summary": "changed a.py", "concerns": []},
        "changed": ["a.py"],
        "scope": None,
        "checks": [_check()],
    }
    values.update(overrides)
    return Attempt(**values)


def _verdict(attempt: Attempt, number: int = 1) -> tuple[str, str, str]:
    return verdict(attempt, number=number, max_attempts=3, max_turns=15)


def test_verdict_passed():
    assert _verdict(_attempt()) == ("passed", "", "")


def test_verdict_must_not_touch_stop():
    attempt = _attempt(stop="must_not_touch", detail="tests/t.py")
    assert _verdict(attempt) == ("escalate", "must_not_touch", "tests/t.py")


def test_verdict_must_not_touch_scope_beats_endpoint():
    attempt = _attempt(stop="endpoint", detail="down", scope=("must_not_touch", "tests/t.py"))
    assert _verdict(attempt) == ("escalate", "must_not_touch", "tests/t.py")


def test_verdict_endpoint_beats_outside_files():
    attempt = _attempt(stop="endpoint", detail="down", scope=("outside_files", "b.py"), checks=[])
    assert _verdict(attempt) == ("failed", "endpoint", "down")


def test_verdict_outside_files_beats_schema_errors():
    attempt = _attempt(stop="schema_errors", detail="4 bad calls", scope=("outside_files", "b.py"))
    assert _verdict(attempt) == ("escalate", "outside_files", "b.py")


def test_verdict_schema_errors():
    attempt = _attempt(stop="schema_errors", detail="4 bad calls")
    assert _verdict(attempt) == ("escalate", "schema_errors", "4 bad calls")


def test_verdict_rewrite_loop():
    attempt = _attempt(stop="rewrite_loop", detail="a.py")
    assert _verdict(attempt) == ("escalate", "rewrite_loop", "a.py")


def test_verdict_blocked_beats_passing_checks():
    attempt = _attempt(report={"outcome": "blocked", "summary": "s", "concerns": []})
    assert _verdict(attempt) == ("escalate", "worker_blocked", "the worker's report says blocked")


def test_verdict_no_changes():
    attempt = _attempt(changed=[])
    assert _verdict(attempt) == ("escalate", "no_changes", "the worktree has no changes")


def test_verdict_turn_limit_passing_checks_pass():
    attempt = _attempt(stop="turn_limit")
    assert _verdict(attempt) == ("passed", "", "")


def test_verdict_turn_limit_failing_checks():
    attempt = _attempt(stop="turn_limit", checks=[_check(exit_code=1)])
    assert _verdict(attempt) == ("escalate", "turn_limit", "15 turns, then `pytest -q` exited 1")


def test_verdict_retry_then_checks_failed():
    attempt = _attempt(checks=[_check(), _check("ruff check .", 2)])
    assert _verdict(attempt, 1) == ("retry", "", "")
    assert _verdict(attempt, 2) == ("retry", "", "")
    assert _verdict(attempt, 3) == (
        "escalate", "checks_failed", "`ruff check .` exited 2 after 3 attempts"
    )


def test_verdict_timed_out_check():
    attempt = _attempt(checks=[_check(timed_out=True, exit_code=-9)])
    assert _verdict(attempt, 3) == (
        "escalate", "checks_failed", "`pytest -q` timed out after 3 attempts"
    )


def test_verdict_detail_is_cut():
    attempt = _attempt(stop="schema_errors", detail="x" * 500)
    status, reason, detail = _verdict(attempt)
    assert (status, reason) == ("escalate", "schema_errors")
    assert len(detail) == 300


def test_verdict_no_report_does_not_block():
    assert _verdict(_attempt(report=None)) == ("passed", "", "")


def test_new_job_id():
    job_id = new_job_id(datetime(2026, 10, 15))
    assert job_id.startswith("j-20261015-")
    assert len(job_id) == 17
    assert re.fullmatch(RECORD_SCHEMA["properties"]["job_id"]["pattern"], job_id)
    assert new_job_id() != new_job_id()


def test_protected_text_short_list():
    assert protected_text(["tests/a.py", "tests/b.py"]) == "- tests/a.py\n- tests/b.py"
    assert protected_text([]) == "(none)"


def test_protected_text_long_list():
    paths = [f"tests/t{i}.py" for i in range(21)]
    text = protected_text(paths)
    assert "(21 paths)" in text
    assert "\n" not in text
    assert text.startswith("- every test file")
    for path in paths:
        assert path not in text


def _record(**overrides) -> dict:
    values = {
        "job_id": "j-20261015-a1b2c3",
        "status": "passed",
        "reason": "",
        "detail": "",
        "attempts": 1,
        "turns": 4,
        "tokens": {"prompt": 100, "completion": 20},
        "checks": [_check()],
        "report": {"outcome": "done", "summary": "changed a.py", "concerns": ["check b"]},
        "report_error": "",
        "turn_limited": False,
        "files_changed": [{"path": "a.py", "added": 1, "removed": 1}],
        "diffstat": "1 file changed, 1 insertion(+), 1 deletion(-)",
        "branch": "local-agent/j-20261015-a1b2c3",
        "base_commit": "a" * 40,
        "model": "bonsai2-27b",
        "wall_s": 12.345,
        "logs": Path("/tmp/jobs/j-20261015-a1b2c3"),
    }
    values.update(overrides)
    return build_record(**values)


def test_record_passed():
    record = _record()
    assert "reason" not in record
    assert "detail" not in record
    assert record["summary"] == "changed a.py"
    assert record["concerns"] == ["check b"]
    assert record["wall_s"] == 12.3
    assert record["est_claude_tokens_avoided"] == 0
    assert record["record_version"] == 1
    assert record["logs"] == "/tmp/jobs/j-20261015-a1b2c3"
    assert record["checks"][0]["tail"] == ""


def test_record_escalate():
    record = _record(
        status="escalate",
        reason="checks_failed",
        detail="d" * 400,
        checks=[_check(exit_code=1)],
        attempts=3,
    )
    assert record["reason"] == "checks_failed"
    assert len(record["detail"]) == 300
    assert record["checks"][0]["tail"] == "out"


def test_record_turn_limit_concern_first():
    record = _record(turn_limited=True)
    assert record["concerns"] == [
        "the last attempt hit the turn limit; the checks pass, but the work may be unfinished",
        "check b",
    ]
    record = _record(
        status="escalate",
        reason="turn_limit",
        detail="x",
        checks=[_check(exit_code=1)],
        attempts=3,
        turn_limited=True,
    )
    assert (
        "the last attempt hit the turn limit; the checks pass, but the work may be unfinished"
        not in record["concerns"]
    )


def test_record_no_report():
    record = _record(report=None, report_error="report is not JSON")
    assert record["summary"] == ""
    assert record["concerns"] == ["no worker report: report is not JSON"]


def test_record_concerns_are_cut():
    record = _record(
        turn_limited=True,
        report={"outcome": "done", "summary": "s", "concerns": ["a", "b", "c"]},
    )
    assert len(record["concerns"]) == 3
    assert record["concerns"][-1] == "b"


def test_record_failed_without_branch():
    record = _record(
        status="failed",
        reason="endpoint",
        detail="down",
        attempts=1,
        files_changed=[],
        diffstat="",
        branch=None,
        checks=[],
        report=None,
    )
    assert record["branch"] is None


def test_record_invalid_raises():
    with pytest.raises(ValueError, match=r"^invalid record: "):
        _record(status="passed", checks=[_check(exit_code=1)])


def test_verdict_must_not_touch_stop_beats_scope():
    attempt = _attempt(
        stop="must_not_touch", detail="tests/a.py", scope=("must_not_touch", "tests/b.py")
    )
    assert _verdict(attempt) == ("escalate", "must_not_touch", "tests/a.py")


def test_verdict_schema_errors_beats_blocked():
    attempt = _attempt(
        stop="schema_errors",
        detail="4 bad calls",
        report={"outcome": "blocked", "summary": "s", "concerns": []},
    )
    assert _verdict(attempt) == ("escalate", "schema_errors", "4 bad calls")


def test_verdict_blocked_beats_no_changes():
    attempt = _attempt(
        report={"outcome": "blocked", "summary": "s", "concerns": []},
        changed=[],
        checks=[],
    )
    assert _verdict(attempt) == ("escalate", "worker_blocked", "the worker's report says blocked")


def test_verdict_turn_limit_on_last_attempt():
    attempt = _attempt(stop="turn_limit", checks=[_check(exit_code=1)])
    assert _verdict(attempt, 3) == (
        "escalate", "turn_limit", "15 turns, then `pytest -q` exited 1"
    )


def test_protected_text_at_the_limit():
    paths = [f"tests/t{i}.py" for i in range(20)]
    assert protected_text(paths) == "\n".join(f"- {p}" for p in paths)


def test_protected_text_long_list_exact():
    assert protected_text([f"tests/t{i}.py" for i in range(21)]) == (
        "- every test file tracked at the base commit that is not in the list above "
        "(21 paths): anything under a tests/ or test/ directory, or named test_*, *_test.*, "
        "*.test.* or *.spec.*"
    )


def test_record_cuts():
    record = _record(
        report={"outcome": "done", "summary": "s" * 1500, "concerns": ["c" * 300]},
        logs=Path("/" + "l" * 400),
    )
    assert len(record["summary"]) == 1000
    assert record["concerns"] == ["c" * 200]
    assert len(record["logs"]) == 300


def test_record_report_and_report_error():
    record = _record(report_error="ignored")
    assert record["concerns"] == ["check b"]


def test_record_local_tokens():
    record = _record(tokens={"completion": 20, "prompt": 100})
    assert list(record["local_tokens"].items()) == [("prompt", 100), ("completion", 20)]


def test_record_router_concerns_order():
    record = _record(
        turn_limited=True,
        router_concerns=("r1",),
        report=None,
        report_error="bad json",
    )
    assert record["concerns"] == [
        "the last attempt hit the turn limit; the checks pass, but the work may be unfinished",
        "r1",
        "no worker report: bad json",
    ]


def test_validate_record_message_is_cut():
    with pytest.raises(ValueError) as excinfo:
        validate_record({**_record(), "job_id": "x" * 500})
    message = str(excinfo.value)
    assert message.startswith("invalid record: ")
    assert len(message) <= 216
