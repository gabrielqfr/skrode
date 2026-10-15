"""A job: one task, its attempts, and the record the caller gets back."""

import contextlib
import json
import secrets
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, exceptions

from router.acceptance import (
    CHECK_TIMEOUT_S,
    CheckResult,
    first_failure,
    record_checks,
    run_check,
    run_checks,
    scope_violation,
)
from router.tools import Workspace, matches
from router.worker import (
    ARGS_SCHEMA_PATH,
    DEFAULT_MAX_TURNS,
    ChatClient,
    bullets,
    load_prompts,
    render,
    run_worker,
    task_message,
)
from router.worktree import (
    WorktreeError,
    branch_name,
    changed_paths,
    commit_all,
    create,
    default_must_not_touch,
    diff_stat,
    discard_changes,
    remove,
    resolve_base,
    uncommit,
)

_REPO = Path(__file__).resolve().parents[1]
RECORD_SCHEMA: dict = json.loads(
    (_REPO / "schemas" / "job_record.json").read_text(encoding="utf-8")
)
ARGS_SCHEMA: dict = json.loads(ARGS_SCHEMA_PATH.read_text(encoding="utf-8"))
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
    elif not attempt.checks:
        status, reason, detail = "failed", "internal", "no checks ran"
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


def _error_line(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}".strip().splitlines()[-1]


def run_job(
    args: dict,
    client: ChatClient,
    *,
    profile_name: str,   # the record's model: the profile file name
    model: str,          # the served alias sent in each request
    params: dict,        # request_params(profile, "agent")
    narrated_markers: tuple[str, ...] = (),
    prompts: dict[str, str] | None = None,
    state_dir: Path = STATE_DIR,
    job_id: str | None = None,
    setup: tuple[str, ...] = (),
    sandbox: bool = True,
    check_timeout_s: int = CHECK_TIMEOUT_S,
) -> dict:
    start = time.monotonic()
    if job_id is None:
        job_id = new_job_id()
    logs = state_dir / "jobs" / job_id
    logs.mkdir(parents=True, exist_ok=True)
    base_commit = ""
    repo = Path.cwd()
    worktree: Path | None = None
    attempts = 0
    turns = 0
    tokens: dict[str, int] = {"prompt": 0, "completion": 0}

    def failed(reason: str, detail: str) -> dict:
        return build_record(
            job_id=job_id,
            status="failed",
            reason=reason,
            detail=detail,
            attempts=attempts,
            turns=turns,
            tokens=tokens,
            checks=[],
            report=None,
            report_error="",
            turn_limited=False,
            files_changed=[],
            diffstat="",
            branch=None,
            base_commit=base_commit,
            model=profile_name,
            wall_s=time.monotonic() - start,
            logs=logs,
        )

    try:
        errors = list(Draft202012Validator(ARGS_SCHEMA).iter_errors(args))
        if errors:
            best = exceptions.best_match(errors)
            return failed("bad_request", f"bad arguments: {best.message}")
        if "repo" in args:
            repo = Path(args["repo"])
        try:
            base_commit = resolve_base(repo, args.get("base", "HEAD"))
        except WorktreeError as error:
            return failed("bad_request", str(error))
        files = args["files"]
        criteria = args["acceptance_criteria"]
        if "must_not_touch" in criteria:
            must_not_touch = criteria["must_not_touch"]
            for path in files:
                if path in must_not_touch:
                    return failed(
                        "bad_request", f"{path} is in both files and must_not_touch"
                    )
        else:
            must_not_touch = default_must_not_touch(repo, base_commit, files)
        if prompts is None:
            prompts = load_prompts()
        worktree = create(repo, base_commit, job_id, state_dir / "worktrees")
        # setup is the router's own config, not the request: it runs unsandboxed so it can
        # reach the network (uv sync) before the worker starts
        for i, command in enumerate(setup, start=1):
            check = run_check(
                worktree,
                command,
                logs / f"setup-{i}.log",
                timeout_s=check_timeout_s,
                sandbox=False,
            )
            if check.failed:
                remove(repo, worktree, job_id, keep_branch=False)
                worktree = None
                return failed("bad_request", f"setup failed: {_failing(check)}")
        workspace = Workspace(
            root=worktree, files=files, must_not_touch=must_not_touch, sandbox=sandbox
        )
        max_attempts = 1 + args.get("max_retries", 2)
        max_turns = args.get("max_turns", DEFAULT_MAX_TURNS)
        bad_calls = 0
        history = [
            {"role": "system", "content": prompts["system"]},
            {
                "role": "user",
                "content": task_message(prompts, args, protected=protected_text(must_not_touch)),
            },
        ]
        while True:
            attempts += 1
            result = run_worker(
                args,
                workspace,
                client,
                model=model,
                params=params,
                prompts=prompts,
                narrated_markers=narrated_markers,
                log=logs / f"worker-{attempts}.jsonl",
                history=history,
                bad_calls_before=bad_calls,
            )
            turns += result.turns
            tokens["prompt"] += result.tokens["prompt"]
            tokens["completion"] += result.tokens["completion"]
            bad_calls = result.bad_calls
            changed = changed_paths(worktree, base_commit)
            scope = scope_violation(changed, files, must_not_touch)
            if result.stop in ("must_not_touch", "endpoint") or (
                scope is not None and scope[0] == "must_not_touch"
            ):
                checks = []
            else:
                commit_all(worktree, base_commit, f"hybrid-router job {job_id} attempt {attempts}")
                checks = run_checks(
                    worktree,
                    criteria["commands"],
                    logs,
                    attempts,
                    timeout_s=check_timeout_s,
                    sandbox=sandbox,
                )
                for path in changed_paths(worktree, "HEAD"):
                    if matches(path, must_not_touch):
                        scope = ("must_not_touch", f"{path}, changed while the checks ran")
                        break
                discard_changes(worktree)
                uncommit(worktree, base_commit)
            status, reason, detail = verdict(
                Attempt(
                    result.stop,
                    result.detail,
                    result.report,
                    changed,
                    scope,
                    checks,
                ),
                number=attempts,
                max_attempts=max_attempts,
                max_turns=max_turns,
            )
            if status != "retry":
                break
            failing = first_failure(checks)
            history = [
                *result.messages,
                {
                    "role": "user",
                    "content": render(
                        prompts,
                        "retry",
                        attempt=attempts,
                        max_attempts=max_attempts,
                        command=failing.command,
                        exit_code=failing.exit_code,
                        tail=failing.tail,
                        max_turns=max_turns,
                    ),
                },
            ]
        has_changes = commit_all(
            worktree, base_commit, f"hybrid-router job {job_id} ({status})"
        )
        files_changed, diffstat = diff_stat(worktree, base_commit)
        remove(repo, worktree, job_id, keep_branch=has_changes)
        worktree = None
        return build_record(
            job_id=job_id,
            status=status,
            reason=reason,
            detail=detail,
            attempts=attempts,
            turns=turns,
            tokens=tokens,
            checks=checks,
            report=result.report,
            report_error=result.report_error,
            turn_limited=result.stop == "turn_limit",
            files_changed=files_changed,
            diffstat=diffstat,
            branch=branch_name(job_id) if has_changes else None,
            base_commit=base_commit,
            model=profile_name,
            wall_s=time.monotonic() - start,
            logs=logs,
        )
    except Exception as error:
        with (logs / "error.log").open("w", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
        if worktree is not None:
            # the record matters more than the cleanup
            with contextlib.suppress(Exception):
                remove(repo, worktree, job_id, keep_branch=False)
        return failed("internal", _error_line(error))
