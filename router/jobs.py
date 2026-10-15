"""A job: one task, its attempts, and the record the caller gets back."""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, exceptions

from router.acceptance import CheckResult, first_failure, record_checks
from router.worker import bullets

_REPO = Path(__file__).resolve().parents[1]
RECORD_SCHEMA: dict = json.loads(
    (_REPO / "schemas" / "job_record.json").read_text(encoding="utf-8")
)
STATE_DIR = Path.home() / ".local" / "state" / "hybrid-router"
PROTECTED_LIST_LIMIT = 20  # the request schema's own limit for an explicit list
DETAIL_LIMIT = 300
SUMMARY_LIMIT = 1_000
CONCERN_LIMIT = 200
CONCERNS_MAX = 3


def new_job_id(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now()
    return f"j-{now.strftime('%Y%m%d')}-{secrets.token_hex(3)}"


def protected_text(paths: list[str]) -> str:
    if len(paths) <= PROTECTED_LIST_LIMIT:
        return bullets(paths)
    return (
        f"- every test file tracked at the base commit that is not in the list above "
        f"({len(paths)} paths): anything under a tests/ or test/ directory, "
        f"or named test_*, *_test.*, *.test.* or *.spec.*"
    )


@dataclass
class Attempt:
    stop: str                       # WorkerResult.stop
    detail: str                     # WorkerResult.detail
    report: dict | None             # WorkerResult.report
    changed: list[str]              # changed paths in the worktree after the attempt
    scope: tuple[str, str] | None   # acceptance.scope_violation(...) for those paths
    checks: list[CheckResult]       # this attempt's checks; [] when they were not run


def _failing(check: CheckResult) -> str:
    if check.timed_out:
        return f"`{check.command}` timed out"
    return f"`{check.command}` exited {check.exit_code}"


def verdict(
    attempt: Attempt, *, number: int, max_attempts: int, max_turns: int
) -> tuple[str, str, str]:
    if attempt.stop == "must_not_touch":
        status, reason, detail = "escalate", "must_not_touch", attempt.detail
    elif attempt.scope is not None and attempt.scope[0] == "must_not_touch":
        status, reason, detail = "escalate", "must_not_touch", attempt.scope[1]
    elif attempt.stop == "endpoint":
        status, reason, detail = "failed", "endpoint", attempt.detail
    elif attempt.scope is not None:
        status, reason, detail = "escalate", "outside_files", attempt.scope[1]
    elif attempt.stop in ("schema_errors", "rewrite_loop"):
        status, reason, detail = "escalate", attempt.stop, attempt.detail
    elif attempt.report is not None and attempt.report["outcome"] == "blocked":
        status, reason, detail = "escalate", "worker_blocked", (
            "the worker's report says blocked"
        )
    elif not attempt.changed:
        status, reason, detail = "escalate", "no_changes", "the worktree has no changes"
    else:
        first_fail = first_failure(attempt.checks)
        if first_fail is None:
            status, reason, detail = "passed", "", ""
        elif attempt.stop == "turn_limit":
            status, reason = "escalate", "turn_limit"
            detail = f"{max_turns} turns, then {_failing(first_fail)}"
        elif number >= max_attempts:
            status, reason = "escalate", "checks_failed"
            detail = f"{_failing(first_fail)} after {number} attempts"
        else:
            status, reason, detail = "retry", "", ""
    return status, reason, detail[:DETAIL_LIMIT]


def validate_record(record: dict) -> None:
    errors = list(Draft202012Validator(RECORD_SCHEMA).iter_errors(record))
    if errors:
        best = exceptions.best_match(errors)
        raise ValueError(f"invalid record: {best.message}")


def build_record(
    *,
    job_id: str,
    status: str,
    reason: str,
    detail: str,
    attempts: int,
    turns: int,
    tokens: dict[str, int],
    checks: list[CheckResult],
    report: dict | None,
    report_error: str,
    turn_limited: bool,
    files_changed: list[dict],
    diffstat: str,
    branch: str | None,
    base_commit: str,
    model: str,
    wall_s: float,
    logs: Path,
) -> dict:
    record: dict = {
        "record_version": 1,
        "job_id": job_id,
        "status": status,
    }
    if status != "passed":
        record["reason"] = reason
        record["detail"] = detail[:DETAIL_LIMIT]
    record.update(
        {
            "attempts": attempts,
            "turns": turns,
            "files_changed": files_changed,
            "diffstat": diffstat,
            "checks": record_checks(checks),
            "summary": report["summary"][:SUMMARY_LIMIT] if report is not None else "",
        }
    )
    concerns: list[str] = []
    if status == "passed" and turn_limited:
        concerns.append(
            "the last attempt hit the turn limit; the checks pass, but the work may be unfinished"
        )
    if report is None and report_error:
        concerns.append(f"no worker report: {report_error}")
    if report is not None:
        concerns.extend(report.get("concerns", []))
    record["concerns"] = [concern[:CONCERN_LIMIT] for concern in concerns[:CONCERNS_MAX]]
    record.update(
        {
            "branch": branch,
            "base_commit": base_commit,
            "model": model,
            "local_tokens": {"prompt": tokens["prompt"], "completion": tokens["completion"]},
            "wall_s": round(wall_s, 1),
            "est_claude_tokens_avoided": 0,
            "logs": str(logs)[:DETAIL_LIMIT],
        }
    )
    validate_record(record)
    return record
