"""Tests for the job store (jobs.jsonl) and the python -m skrode.jobs command."""

import json
import subprocess
from pathlib import Path

import pytest

import skrode.jobs as jobs
from skrode.jobs import append_record, load_records, main, run_job
from skrode.llm import ChatReply
from skrode.profiles import load_profile, request_params

CHECK = "grep -q 'x = 2' a.py"
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
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    return root


def _reply(content: str = "", calls: list[dict] | None = None) -> ChatReply:
    message: dict = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return ChatReply(message=message, finish_reason="stop", usage=dict(USAGE), timings={})


def _write_x2() -> ChatReply:
    arguments = json.dumps({"path": "a.py", "content": "x = 2\n"})
    return _reply(calls=[{"id": "c_write", "function": {"name": "write", "arguments": arguments}}])


class ScriptClient:
    """Answers loop calls from a script and every report call with REPORT."""

    def __init__(self, script: list) -> None:
        self.script = list(script)

    def chat(self, body: dict) -> ChatReply:
        if "response_format" in body:
            return _reply(json.dumps(REPORT))
        return self.script.pop(0)


def _args(repo: Path) -> dict:
    return {
        "task": "Change a.py so the value x becomes 2 instead of 1.",
        "files": ["a.py"],
        "acceptance_criteria": {"commands": [CHECK]},
        "repo": str(repo),
    }


def _args_file(tmp_path: Path, args: dict) -> Path:
    path = tmp_path / "args.json"
    path.write_text(json.dumps(args), encoding="utf-8")
    return path


def _run(repo: Path, args: dict) -> dict:
    return run_job(
        args,
        ScriptClient([_write_x2(), _reply("done")]),
        profile_name="test-profile",
        model="local",
        params={},
        state_dir=repo.parent / "state",
        job_id="j-20261015-abcdef",
        sandbox=False,
    )


class FakeRunJob:
    """Stands in for run_job in the CLI tests: records its call, returns a fixed record."""

    def __init__(self, status: str = "passed") -> None:
        self.status = status
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object) -> dict:
        self.calls.append((args, kwargs))
        return {"job_id": "j-1", "status": self.status}


PROFILE = load_profile("bonsai2-27b")


def test_append_record_writes_one_line_each(tmp_path):
    state = tmp_path / "state"
    first = {"job_id": "j-1", "status": "passed"}
    second = {"job_id": "j-2", "status": "failed"}
    append_record(first, state)
    assert append_record(second, state) == state / "jobs.jsonl"
    text = (state / "jobs.jsonl").read_text(encoding="utf-8")
    assert text.endswith("\n")
    lines = text.split("\n")[:-1]
    assert [json.loads(line) for line in lines] == [first, second]


def test_load_records_without_store(tmp_path):
    assert load_records(tmp_path / "none") == []


def test_load_records_skips_a_cut_line(tmp_path):
    append_record({"job_id": "j-1"}, tmp_path)
    with (tmp_path / "jobs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"job_id": "j-')
    assert load_records(tmp_path) == [{"job_id": "j-1"}]


def test_load_records_keeps_a_line_separator(tmp_path):
    append_record({"detail": "a\N{LINE SEPARATOR}b"}, tmp_path)
    assert load_records(tmp_path) == [{"detail": "a\N{LINE SEPARATOR}b"}]


def test_run_job_stores_a_passed_record(repo):
    record = _run(repo, _args(repo))
    assert record["status"] == "passed"
    assert load_records(repo.parent / "state") == [record]


def test_run_job_stores_a_bad_request(repo):
    args = _args(repo)
    del args["files"]
    record = _run(repo, args)
    assert record["status"] == "failed"
    assert record["reason"] == "bad_request"
    assert load_records(repo.parent / "state") == [record]


def test_run_job_returns_the_record_when_the_store_fails(repo, monkeypatch):
    def fail(record: dict, state_dir: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(jobs, "append_record", fail)
    assert _run(repo, _args(repo))["status"] == "passed"


def test_cli_passes_options(tmp_path, monkeypatch, capsys):
    fake = FakeRunJob()
    monkeypatch.setattr(jobs, "run_job", fake)
    path = _args_file(tmp_path, _args(tmp_path))
    code = main(
        [
            "--args",
            str(path),
            "--base-url",
            "http://127.0.0.1:9999",
            "--no-sandbox",
            "--setup",
            "uv sync",
            "--setup",
            "make prep",
            "--state-dir",
            str(tmp_path / "st"),
        ]
    )
    assert code == 0
    assert len(fake.calls) == 1
    args, kwargs = fake.calls[0]
    assert args[0] == _args(tmp_path)
    assert kwargs["profile_name"] == "bonsai2-27b"
    assert kwargs["model"] == "local"
    assert kwargs["params"] == request_params(PROFILE, "agent")
    assert kwargs["narrated_markers"] == tuple(PROFILE["narrated_markers"])
    assert kwargs["setup"] == ("uv sync", "make prep")
    assert kwargs["state_dir"] == tmp_path / "st"
    assert kwargs["sandbox"] is False
    assert json.loads(capsys.readouterr().out) == {"job_id": "j-1", "status": "passed"}


def test_cli_defaults(tmp_path, monkeypatch):
    fake = FakeRunJob()
    monkeypatch.setattr(jobs, "run_job", fake)
    path = _args_file(tmp_path, _args(tmp_path))
    assert main(["--args", str(path)]) == 0
    kwargs = fake.calls[0][1]
    assert kwargs["setup"] == ()
    assert kwargs["state_dir"] == jobs.STATE_DIR
    assert kwargs["sandbox"] is True


def test_cli_exit_code_is_1_unless_passed(tmp_path, monkeypatch):
    path = _args_file(tmp_path, _args(tmp_path))
    for status in ("escalate", "failed"):
        monkeypatch.setattr(jobs, "run_job", FakeRunJob(status))
        assert main(["--args", str(path)]) == 1


def test_cli_schema_errors_reach_run_job(tmp_path, monkeypatch):
    fake = FakeRunJob()
    monkeypatch.setattr(jobs, "run_job", fake)
    path = _args_file(tmp_path, {"task": "x"})
    assert main(["--args", str(path)]) == 0
    assert fake.calls[0][0][0] == {"task": "x"}


def test_cli_bad_args_file(tmp_path, monkeypatch, capsys):
    fake = FakeRunJob()
    monkeypatch.setattr(jobs, "run_job", fake)
    assert main(["--args", str(tmp_path / "missing.json")]) == 2
    assert capsys.readouterr().err.startswith("bad arguments: ")
    not_json = tmp_path / "not.json"
    not_json.write_text("not json", encoding="utf-8")
    assert main(["--args", str(not_json)]) == 2
    assert capsys.readouterr().err.startswith("bad arguments: ")
    assert fake.calls == []


def test_cli_bad_profile(tmp_path, monkeypatch, capsys):
    fake = FakeRunJob()
    monkeypatch.setattr(jobs, "run_job", fake)
    path = _args_file(tmp_path, _args(tmp_path))
    assert main(["--args", str(path), "--profile", "nope"]) == 2
    assert capsys.readouterr().err.startswith("bad profile: ")
    assert fake.calls == []


def test_cli_bad_base_url(tmp_path, monkeypatch, capsys):
    fake = FakeRunJob()
    monkeypatch.setattr(jobs, "run_job", fake)
    path = _args_file(tmp_path, _args(tmp_path))
    assert main(["--args", str(path), "--base-url", "http://example.com:8080"]) == 2
    assert capsys.readouterr().err.startswith("bad base url: ")
    assert fake.calls == []


def test_cli_runs_a_job_and_stores_it(repo, monkeypatch, capsys):
    monkeypatch.setattr(
        jobs, "LLMClient", lambda base_url: ScriptClient([_write_x2(), _reply("done")])
    )
    path = _args_file(repo.parent, _args(repo))
    state = repo.parent / "state"
    assert main(["--args", str(path), "--no-sandbox", "--state-dir", str(state)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "passed"
    assert printed["model"] == "bonsai2-27b"
    assert load_records(state) == [printed]
