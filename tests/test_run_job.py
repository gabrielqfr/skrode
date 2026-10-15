"""Tests for router.jobs.run_job: one job end to end, in a temp git repo, with a fake model."""

import json
import subprocess
from pathlib import Path

import pytest

import router.jobs as jobs
from router.jobs import Attempt, run_job, verdict
from router.llm import ChatReply, LLMError
from router.tools import Workspace
from router.worker import run_worker
from router.worktree import WorktreeError

TASK = "Change a.py so the value x becomes 2 instead of 1."
CHECK = "grep -q 'x = 2' a.py"
JOB = "j-20261015-abcdef"
REPORT = {"outcome": "done", "summary": "set x to 2", "concerns": []}
USAGE = {"prompt_tokens": 10, "completion_tokens": 5}


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "init", "-q")
    sh(root, "config", "user.name", "t")
    sh(root, "config", "user.email", "t@t.t")
    (root / "a.py").write_text("x = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("t\n")
    (root / ".gitignore").write_text(".setup-done\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    return root


def _reply(content: str = "", calls: list[dict] | None = None) -> ChatReply:
    message: dict = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return ChatReply(message=message, finish_reason="stop", usage=dict(USAGE), timings={})


def call(name: str, **arguments) -> ChatReply:
    function = {"name": name, "arguments": json.dumps(arguments)}
    return _reply(calls=[{"id": f"c_{name}", "function": function}])


def done() -> ChatReply:
    return _reply("done")


class ScriptClient:
    """Answers loop calls from a script and every report call with REPORT."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.bodies: list[dict] = []

    def chat(self, body: dict) -> ChatReply:
        self.bodies.append(body)
        if "response_format" in body:
            return _reply(json.dumps(REPORT))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _args(repo: Path, **extra) -> dict:
    args = {
        "task": TASK,
        "files": ["a.py"],
        "acceptance_criteria": {"commands": [CHECK]},
        "repo": str(repo),
    }
    args.update(extra)
    return args


def _run(repo: Path, client: ScriptClient, args: dict | None = None, **kwargs) -> dict:
    return run_job(
        _args(repo) if args is None else args,
        client,
        profile_name="test-profile",
        model="local",
        params={},
        state_dir=repo.parent / "state",
        job_id=JOB,
        sandbox=False,
        **kwargs,
    )


W = call("write", path="a.py", content="x = 2\n")
WRONG_WRITE = call("write", path="a.py", content="x = 3\n")


def test_passed(repo: Path) -> None:
    state = repo.parent / "state"
    client = ScriptClient([W, done()])
    record = _run(repo, client)
    assert record["status"] == "passed"
    assert "reason" not in record
    assert record["attempts"] == 1
    assert record["turns"] == 2
    assert record["local_tokens"] == {"prompt": 30, "completion": 15}
    assert record["branch"] == f"local-agent/{JOB}"
    assert record["files_changed"] == [{"path": "a.py", "added": 1, "removed": 1}]
    assert record["model"] == "test-profile"
    assert record["base_commit"] == sh(repo, "rev-parse", "HEAD")
    assert record["summary"] == "set x to 2"
    assert sh(repo, "show", f"local-agent/{JOB}:a.py") == "x = 2"
    assert not (state / "worktrees" / JOB).exists()
    assert record["logs"] == str(state / "jobs" / JOB)
    assert (state / "jobs" / JOB / "worker-1.jsonl").exists()
    assert (state / "jobs" / JOB / "check-1-1.log").exists()


def test_first_message_protects_default_tests(repo: Path) -> None:
    client = ScriptClient([W, done()])
    _run(repo, client)
    messages = client.bodies[0]["messages"]
    assert messages[0]["role"] == "system"
    marker = "Protected (never change):\n- tests/test_a.py\n"
    assert marker in messages[1]["content"]


def test_retry_fixes_failing_check(repo: Path) -> None:
    state = repo.parent / "state"
    client = ScriptClient([WRONG_WRITE, done(), W, done()])
    record = _run(repo, client)
    assert record["status"] == "passed"
    assert record["attempts"] == 2
    assert record["turns"] == 4
    assert record["local_tokens"] == {"prompt": 60, "completion": 30}
    last = client.bodies[3]["messages"][-1]
    assert last["role"] == "user"
    start = f"The checks failed (attempt 1 of 3). `{CHECK}` exited 1."
    assert last["content"].startswith(start)
    assert (state / "jobs" / JOB / "worker-2.jsonl").exists()
    assert (state / "jobs" / JOB / "check-2-1.log").exists()


def test_checks_failed_after_last_attempt(repo: Path) -> None:
    client = ScriptClient([WRONG_WRITE, done()])
    record = _run(repo, client, args=_args(repo, max_retries=0))
    assert record["status"] == "escalate"
    assert record["reason"] == "checks_failed"
    assert record["detail"] == f"`{CHECK}` exited 1 after 1 attempts"
    assert record["branch"] == f"local-agent/{JOB}"
    assert record["checks"][0]["exit_code"] == 1


def test_no_changes_deletes_branch(repo: Path) -> None:
    client = ScriptClient([done()])
    record = _run(repo, client)
    assert record["status"] == "escalate"
    assert record["reason"] == "no_changes"
    assert record["branch"] is None
    assert record["files_changed"] == []
    assert sh(repo, "branch", "--list", f"local-agent/{JOB}") == ""


def test_protected_change_by_bash_skips_checks(repo: Path) -> None:
    state = repo.parent / "state"
    client = ScriptClient([call("bash", cmd="echo x >> tests/test_a.py"), done()])
    record = _run(repo, client)
    assert record["status"] == "escalate"
    assert record["reason"] == "must_not_touch"
    assert record["detail"] == "tests/test_a.py"
    assert record["checks"] == []
    assert not (state / "jobs" / JOB / "check-1-1.log").exists()


def test_bad_calls_count_across_attempts(repo: Path) -> None:
    script = [call("nope"), call("nope"), WRONG_WRITE, done(), call("nope"), call("nope")]
    client = ScriptClient(script)
    record = _run(repo, client)
    assert record["status"] == "escalate"
    assert record["reason"] == "schema_errors"
    assert record["detail"] == "4 bad calls"
    assert record["attempts"] == 2


def test_endpoint_down(repo: Path) -> None:
    client = ScriptClient([LLMError("connection refused")])
    record = _run(repo, client)
    assert record["status"] == "failed"
    assert record["reason"] == "endpoint"
    assert record["checks"] == []
    assert record["branch"] is None


def test_invalid_arguments(repo: Path) -> None:
    args = _args(repo)
    del args["task"]
    client = ScriptClient([])
    record = _run(repo, client, args=args)
    assert record["status"] == "failed"
    assert record["reason"] == "bad_request"
    assert record["detail"].startswith("bad arguments: ")
    assert record["base_commit"] == ""
    assert record["attempts"] == 0
    assert client.bodies == []


def test_base_does_not_resolve(repo: Path) -> None:
    client = ScriptClient([])
    record = _run(repo, client, args=_args(repo, base="nope"))
    assert record["reason"] == "bad_request"
    assert record["detail"] == "base does not resolve: nope"


def test_path_in_files_and_must_not_touch(repo: Path) -> None:
    state = repo.parent / "state"
    args = _args(repo)
    args["acceptance_criteria"]["must_not_touch"] = ["a.py"]
    client = ScriptClient([])
    record = _run(repo, client, args=args)
    assert record["reason"] == "bad_request"
    assert record["detail"] == "a.py is in both files and must_not_touch"
    assert record["base_commit"] == sh(repo, "rev-parse", "HEAD")
    assert not (state / "worktrees" / JOB).exists()


def test_setup_runs_before_the_worker(repo: Path) -> None:
    state = repo.parent / "state"
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = ["test -f .setup-done", CHECK]
    client = ScriptClient([W, done()])
    record = _run(repo, client, args=args, setup=("touch .setup-done",))
    assert record["status"] == "passed"
    assert (state / "jobs" / JOB / "setup-1.log").exists()


def test_setup_failure(repo: Path) -> None:
    state = repo.parent / "state"
    client = ScriptClient([])
    record = _run(repo, client, setup=("exit 3",))
    assert record["status"] == "failed"
    assert record["reason"] == "bad_request"
    assert record["detail"] == "setup failed: `exit 3` exited 3"
    assert client.bodies == []
    assert not (state / "worktrees" / JOB).exists()
    assert sh(repo, "branch", "--list", f"local-agent/{JOB}") == ""


def test_unexpected_error_is_internal(repo: Path, monkeypatch) -> None:
    state = repo.parent / "state"

    def boom(*args, **kwargs) -> None:
        raise RuntimeError("first\nlast line")

    monkeypatch.setattr(jobs, "run_checks", boom)
    client = ScriptClient([W, done()])
    record = _run(repo, client)
    assert record["status"] == "failed"
    assert record["reason"] == "internal"
    assert record["detail"] == "last line"
    assert record["attempts"] == 1
    assert record["turns"] == 2
    assert not (state / "worktrees" / JOB).exists()
    error_log = (state / "jobs" / JOB / "error.log").read_text(encoding="utf-8")
    assert "RuntimeError" in error_log


def test_verdict_no_checks_is_internal() -> None:
    assert verdict(
        Attempt("finished", "", None, ["a.py"], None, []),
        number=1,
        max_attempts=3,
        max_turns=15,
    ) == ("failed", "internal", "no checks ran")


def test_run_worker_counts_bad_calls_before(tmp_path) -> None:
    (tmp_path / "a.py").write_text("")
    workspace = Workspace(root=tmp_path, files=["a.py"], sandbox=False)
    result = run_worker(
        _args(tmp_path),
        workspace,
        ScriptClient([call("nope")]),
        model="local",
        params={},
        bad_calls_before=3,
    )
    assert result.stop == "schema_errors"
    assert result.detail == "4 bad calls"
    assert result.bad_calls == 4


def test_check_changing_protected_file_escalates(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"echo y >> tests/test_a.py; {CHECK}"]
    client = ScriptClient([call("write", path="a.py", content="x = 2\n"), done()])
    record = _run(repo, client, args)
    assert record["status"] == "escalate"
    assert record["reason"] == "must_not_touch"
    assert record["detail"] == "tests/test_a.py, changed while the checks ran"
    assert record["attempts"] == 1
    assert sh(repo, "show", f"local-agent/{JOB}:tests/test_a.py") == "t"
    assert sh(repo, "show", f"local-agent/{JOB}:a.py") == "x = 2"


def test_check_artifacts_are_discarded(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"touch out.txt; echo z >> a.py; {CHECK}"]
    client = ScriptClient([call("write", path="a.py", content="x = 2\n"), done()])
    record = _run(repo, client, args)
    assert record["status"] == "passed"
    assert record["files_changed"] == [{"path": "a.py", "added": 1, "removed": 1}]
    assert sh(repo, "show", f"local-agent/{JOB}:a.py") == "x = 2"
    assert record["concerns"] == [
        "the checks changed a.py; the branch has it as it was before the checks ran"
    ]


def test_retry_carries_the_tail(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = ["cat a.py; " + CHECK]
    client = ScriptClient(
        [
            call("write", path="a.py", content="x = 3\n"),
            done(),
            call("write", path="a.py", content="x = 2\n"),
            done(),
        ]
    )
    record = _run(repo, client, args)
    assert record["status"] == "passed"
    assert "x = 3" in client.bodies[3]["messages"][-1]["content"]


def test_protected_write_is_refused(repo):
    client = ScriptClient([call("write", path="tests/test_a.py", content="y\n")])
    record = _run(repo, client)
    assert record["status"] == "escalate"
    assert record["reason"] == "must_not_touch"
    assert record["detail"] == "tests/test_a.py"
    assert record["attempts"] == 1
    assert record["checks"] == []
    assert not (repo.parent / "state" / "jobs" / JOB / "check-1-1.log").exists()
    assert record["branch"] is None


def test_retry_sees_earlier_changes(repo):
    client = ScriptClient(
        [
            call("write", path="a.py", content="x = 3\n"),
            done(),
            call("bash", cmd="git status --porcelain"),
            call("write", path="a.py", content="x = 2\n"),
            done(),
        ]
    )
    record = _run(repo, client)
    assert record["status"] == "passed"
    tool_messages = [m for m in client.bodies[4]["messages"] if m["role"] == "tool"]
    assert "a.py" in tool_messages[-1]["content"]
    assert " M a.py" in tool_messages[-1]["content"]
    subject = sh(repo, "log", "--format=%s", "-1", f"local-agent/{JOB}")
    assert subject == f"hybrid-router job {JOB} (passed)"
    assert sh(repo, "rev-list", "--count", f"HEAD..local-agent/{JOB}") == "1"


def test_check_changing_protected_glob_escalates(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"echo y >> tests/test_a.py; {CHECK}"]
    args["acceptance_criteria"]["must_not_touch"] = ["tests/"]
    client = ScriptClient([call("write", path="a.py", content="x = 2\n"), done()])
    record = _run(repo, client, args)
    assert record["reason"] == "must_not_touch"
    assert record["detail"] == "tests/test_a.py, changed while the checks ran"


def test_retry_prompt_names_a_timeout(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = ["grep -q 'x = 2' a.py || sleep 5"]
    client = ScriptClient([WRONG_WRITE, done(), W, done()])
    record = _run(repo, client, args=args, check_timeout_s=1)
    assert record["status"] == "passed"
    message = client.bodies[3]["messages"][-1]["content"]
    assert f"`{CHECK} || sleep 5` timed out" in message
    assert "exited" not in message


def test_retry_prompt_names_the_exit_code(repo):
    client = ScriptClient([WRONG_WRITE, done(), W, done()])
    record = _run(repo, client)
    assert record["status"] == "passed"
    assert f"`{CHECK}` exited 1" in client.bodies[3]["messages"][-1]["content"]


def test_read_only_directory_is_removed(repo):
    state = repo.parent / "state"
    client = ScriptClient([W, call("bash", cmd="chmod 555 ."), done()])
    record = _run(repo, client)
    assert record["status"] == "passed"
    assert record["concerns"] == []
    assert not (state / "worktrees" / JOB).exists()
    assert sh(repo, "show", f"local-agent/{JOB}:a.py") == "x = 2"


def test_remove_failure_keeps_the_verdict(repo, monkeypatch):
    def boom(*args, **kwargs) -> None:
        raise WorktreeError("git worktree remove failed: busy")

    monkeypatch.setattr(jobs, "remove", boom)
    client = ScriptClient([W, done()])
    record = _run(repo, client)
    assert record["status"] == "passed"
    assert record["branch"] == f"local-agent/{JOB}"
    assert record["concerns"] == [
        "could not remove the worktree: WorktreeError: git worktree remove failed: busy"
    ]


def test_setup_changing_the_worktree(repo):
    state = repo.parent / "state"
    client = ScriptClient([])
    record = _run(repo, client, setup=("touch stray.txt",))
    assert record["status"] == "failed"
    assert record["reason"] == "bad_request"
    assert record["detail"] == "setup changed stray.txt"
    assert client.bodies == []
    assert not (state / "worktrees" / JOB).exists()
    assert sh(repo, "branch", "--list", f"local-agent/{JOB}") == ""


def test_check_changing_a_tracked_file_is_a_concern(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"echo z >> a.py; {CHECK}"]
    client = ScriptClient([W, done()])
    record = _run(repo, client, args)
    assert record["status"] == "passed"
    assert record["concerns"] == [
        "the checks changed a.py; the branch has it as it was before the checks ran"
    ]
    assert sh(repo, "show", f"local-agent/{JOB}:a.py") == "x = 2"


def test_untracked_check_output_is_not_a_concern(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"touch out.txt; {CHECK}"]
    client = ScriptClient([W, done()])
    record = _run(repo, client, args)
    assert record["status"] == "passed"
    assert record["concerns"] == []


def test_check_concern_is_per_attempt(repo):
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"{CHECK} || {{ echo z >> a.py; false; }}"]
    client = ScriptClient([WRONG_WRITE, done(), W, done()])
    record = _run(repo, client, args)
    assert record["status"] == "passed"
    assert record["attempts"] == 2
    assert record["concerns"] == []


def test_check_concern_comes_before_remove_concern(repo, monkeypatch):
    def boom(*args, **kwargs) -> None:
        raise WorktreeError("busy")

    monkeypatch.setattr(jobs, "remove", boom)
    args = _args(repo)
    args["acceptance_criteria"]["commands"] = [f"echo z >> a.py; {CHECK}"]
    client = ScriptClient([W, done()])
    record = _run(repo, client, args)
    assert record["status"] == "passed"
    assert record["concerns"] == [
        "the checks changed a.py; the branch has it as it was before the checks ran",
        "could not remove the worktree: WorktreeError: busy",
    ]


def test_cancel_keeps_work_and_skips_checks(repo: Path) -> None:
    answers = iter([None, "cancelled"])
    client = ScriptClient([W, done()])
    record = _run(repo, client, should_stop=lambda: next(answers, None))
    assert record["status"] == "failed"
    assert record["reason"] == "cancelled"
    assert record["detail"] == "cancelled in attempt 1, after 1 of 40 turns"
    assert record["attempts"] == 1
    assert record["turns"] == 1
    assert record["checks"] == []
    assert record["branch"] == f"local-agent/{JOB}"
    assert sh(repo, "show", f"local-agent/{JOB}:a.py") == "x = 2"
    assert not (repo.parent / "state" / "jobs" / JOB / "check-1-1.log").exists()


def test_timeout_before_any_call(repo: Path) -> None:
    client = ScriptClient([W, done()])
    record = _run(repo, client, should_stop=lambda: "timeout")
    assert record["status"] == "failed"
    assert record["reason"] == "timeout"
    assert record["detail"] == "timed out in attempt 1, after 0 of 40 turns"
    assert record["turns"] == 0
    assert record["branch"] is None
    assert client.bodies == []


def test_progress_lines(repo: Path) -> None:
    lines: list[str] = []
    client = ScriptClient([WRONG_WRITE, done(), W, done()])
    record = _run(repo, client, progress=lines.append)
    assert record["status"] == "passed"
    assert lines == [
        "attempt 1 of 3: turn 1 of 40",
        "attempt 1 of 3: turn 2 of 40",
        "attempt 1 of 3: running the checks",
        "attempt 2 of 3: turn 1 of 40",
        "attempt 2 of 3: turn 2 of 40",
        "attempt 2 of 3: running the checks",
    ]
