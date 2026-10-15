"""Tests for router.worker: the worker loop, driven by a fake client."""

import contextlib
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from router.llm import ChatReply, LLMError
from router.profiles import load_profile, request_params
from router.tools import TOOL_SPECS, Workspace
from router.worker import (
    LOG_TEXT_LIMIT,
    REPORT_FORMAT,
    WorkerResult,
    build_body,
    is_narrated,
    load_prompts,
    main,
    render,
    run_worker,
    task_message,
)

TASK = "Change a.py so the value x becomes 2 instead of 1."


def _args(max_turns: int | None = None) -> dict:
    args = {
        "task": TASK,
        "files": ["a.py"],
        "acceptance_criteria": {
            "must_not_touch": ["tests/"],
            "commands": ["pytest -q"],
        },
    }
    if max_turns is not None:
        args["max_turns"] = max_turns
    return args


def _report(outcome: str, summary: str, concerns: list[str]) -> str:
    return json.dumps({"outcome": outcome, "summary": summary, "concerns": concerns})


def _reply(
    content: str,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    usage: dict | None = None,
) -> ChatReply:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return ChatReply(
        message=message,
        finish_reason=finish_reason,
        usage=usage or {},
        timings={},
    )


def _call(name: str, **kwargs) -> dict:
    return {
        "id": f"call_{name}",
        "function": {"name": name, "arguments": json.dumps(kwargs)},
    }


class FakeClient:
    def __init__(self, items: list) -> None:
        self.items = items
        self.bodies: list[dict] = []

    def chat(self, body: dict) -> ChatReply:
        self.bodies.append(copy.deepcopy(body))
        item = self.items.pop(0)
        if isinstance(item, LLMError):
            raise item
        return item


def _ws(tmp_path: Path) -> Workspace:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    return Workspace(
        root=tmp_path,
        files=["a.py"],
        must_not_touch=["tests/"],
        sandbox=False,
    )


def test_load_prompts_sections():
    prompts = load_prompts()
    assert set(prompts) == {
        "system",
        "task",
        "retry",
        "correction.invalid",
        "correction.unknown_tool",
        "correction.narrated",
        "correction.length",
        "correction.outside_files",
        "report",
    }
    assert prompts["system"].startswith("You are a coding worker.")
    for body in prompts.values():
        assert "Every message the router sends" not in body


def test_task_message():
    prompts = load_prompts()
    text = task_message(prompts, _args())
    assert TASK in text
    assert "- a.py" in text
    assert "- tests/" in text
    assert "- pytest -q" in text
    assert "15 turns" in text
    no_protect = {
        "task": TASK,
        "files": ["a.py"],
        "acceptance_criteria": {"commands": ["pytest -q"]},
    }
    assert "(none)" in task_message(prompts, no_protect)


def test_is_narrated():
    assert is_narrated("CALL: read a.py", ("CALL:",))
    assert not is_narrated("CALL: read a.py")
    assert is_narrated('{"name": "bash", "arguments": {}}')
    assert not is_narrated("I changed a.py.")
    assert not is_narrated('{"name": "x"}')


def test_build_body_copies_messages():
    messages = [{"role": "user", "content": "hi"}]
    body = build_body("local", {}, messages)
    assert body["messages"] == messages
    assert body["messages"] is not messages


def test_happy_path(tmp_path):
    prompts = load_prompts()
    ws = _ws(tmp_path)
    write_reply = _reply(
        "",
        tool_calls=[_call("write", path="a.py", content="x = 2\n")],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    done_reply = _reply("done", usage={"prompt_tokens": 10, "completion_tokens": 5})
    report_reply = _reply(
        _report("done", "changed a.py", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([write_reply, done_reply, report_reply])

    result = run_worker(
        _args(),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
        narrated_markers=("CALL:",),
    )

    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    assert result.stop == "finished"
    assert result.detail == ""
    assert result.turns == 2
    assert result.bad_calls == 0
    assert result.tokens == {"prompt": 21, "completion": 11}
    assert result.report == {"outcome": "done", "summary": "changed a.py", "concerns": []}
    assert result.report_error == ""

    body1 = client.bodies[0]
    assert body1["model"] == "local"
    assert body1["temperature"] == 1.0
    assert body1["tools"] == TOOL_SPECS
    assert body1["tool_choice"] == "auto"
    assert body1["messages"][0] == {"role": "system", "content": prompts["system"]}
    assert body1["messages"][1] == {"role": "user", "content": task_message(prompts, _args())}
    assert len(body1["messages"]) == 2

    body2 = client.bodies[1]
    last = body2["messages"][-1]
    assert last["role"] == "tool"
    assert last["tool_call_id"] == "call_write"
    assert last["content"].startswith("wrote a.py")

    body3 = client.bodies[2]
    assert "tools" not in body3
    assert body3["response_format"] == REPORT_FORMAT
    assert body3["messages"][-1] == {"role": "user", "content": prompts["report"]}


def test_invalid_arguments_get_correction(tmp_path):
    prompts = load_prompts()
    ws = _ws(tmp_path)
    bad_read = _reply(
        "",
        tool_calls=[_call("read")],
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    report = _reply(_report("done", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([bad_read, done, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.bad_calls == 1
    last = client.bodies[1]["messages"][-1]
    assert last["role"] == "tool"
    assert last["content"] == render(
        prompts,
        "correction.invalid",
        tool="read",
        error=ws.run("read", "{}").text,
    )


def test_unknown_tool_gets_correction(tmp_path):
    prompts = load_prompts()
    ws = _ws(tmp_path)
    bad = _reply(
        "",
        tool_calls=[_call("delete")],
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    report = _reply(_report("done", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([bad, done, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.bad_calls == 1
    last = client.bodies[1]["messages"][-1]
    assert last["role"] == "tool"
    assert last["content"] == render(prompts, "correction.unknown_tool", tool="delete")


def test_outside_files_is_refused_not_counted(tmp_path):
    prompts = load_prompts()
    ws = _ws(tmp_path)
    outside = _reply(
        "",
        tool_calls=[_call("write", path="b.py", content="y")],
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    report = _reply(_report("done", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([outside, done, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.bad_calls == 0
    assert not (tmp_path / "b.py").is_file()
    last = client.bodies[1]["messages"][-1]
    assert last["role"] == "tool"
    assert last["content"] == render(
        prompts, "correction.outside_files", path="b.py", files="a.py"
    )


def test_narrated_call_gets_correction(tmp_path):
    prompts = load_prompts()
    ws = _ws(tmp_path)
    narrated = _reply(
        "CALL: read a.py",
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    report = _reply(_report("done", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([narrated, done, report])

    result = run_worker(
        _args(),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
        narrated_markers=("CALL:",),
    )
    assert result.stop == "finished"
    assert result.bad_calls == 1
    msgs = client.bodies[1]["messages"]
    assert msgs[-2] == {"role": "assistant", "content": "CALL: read a.py"}
    assert msgs[-1] == {"role": "user", "content": render(prompts, "correction.narrated")}


def test_length_gets_correction(tmp_path):
    prompts = load_prompts()
    ws = _ws(tmp_path)
    cut = _reply(
        "this reply was cut off mid call",
        finish_reason="length",
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    report = _reply(_report("done", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([cut, done, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.bad_calls == 1
    msgs = client.bodies[1]["messages"]
    assert msgs[-1] == {"role": "user", "content": render(prompts, "correction.length")}
    for body in client.bodies:
        for msg in body["messages"]:
            assert "this reply was cut off mid call" not in str(msg)


def test_schema_errors_after_four_bad_calls(tmp_path):
    ws = _ws(tmp_path)
    replies = [
        _reply(
            "",
            tool_calls=[_call("delete")],
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        )
        for _ in range(4)
    ]
    report = _reply(_report("blocked", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([*replies, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "schema_errors"
    assert result.detail == "4 bad calls"
    assert result.turns == 4
    assert result.bad_calls == 4
    assert result.report == {"outcome": "blocked", "summary": "x", "concerns": []}
    assert result.report_error == ""
    assert len(client.bodies) == 5


def test_schema_errors_counts_narrated_and_length(tmp_path):
    ws = _ws(tmp_path)
    replies = [
        _reply("CALL: read a.py"),
        _reply("cut", finish_reason="length"),
        _reply("CALL: read a.py"),
        _reply("cut", finish_reason="length"),
    ]
    report = _reply(
        _report("done", "x", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([*replies, report])

    result = run_worker(
        _args(),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
        narrated_markers=("CALL:",),
    )
    assert result.stop == "schema_errors"
    assert result.detail == "4 bad calls"
    assert result.turns == 4
    assert result.bad_calls == 4
    assert result.report == {"outcome": "done", "summary": "x", "concerns": []}
    assert result.report_error == ""
    assert len(client.bodies) == 5


def test_schema_errors_before_rewrite_loop(tmp_path):
    ws = _ws(tmp_path)
    writes_a = [
        _reply(
            "",
            tool_calls=[_call("write", path="a.py", content=f"v{i}\n")],
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        )
        for i in range(2)
    ]
    mixed = _reply(
        "",
        tool_calls=[
            _call("write", path="a.py", content="v3\n"),
            _call("delete"),
            _call("delete"),
            _call("delete"),
            _call("delete"),
        ],
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    report = _reply(
        _report("blocked", "x", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([*writes_a, mixed, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "schema_errors"
    assert result.detail == "4 bad calls"
    assert result.turns == 3
    assert result.bad_calls == 4
    assert result.report == {"outcome": "blocked", "summary": "x", "concerns": []}
    assert result.report_error == ""
    assert len(client.bodies) == 4


def test_must_not_touch_stops_at_once(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("orig\n", encoding="utf-8")
    ws = _ws(tmp_path)
    reply = _reply(
        "",
        tool_calls=[
            _call("write", path="tests/test_a.py", content="hack"),
            _call("write", path="a.py", content="x = 2\n"),
        ],
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    client = FakeClient([reply])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "must_not_touch"
    assert result.detail == "tests/test_a.py"
    assert result.turns == 1
    assert result.report is None
    assert result.report_error == ""
    assert len(client.bodies) == 1
    assert (tmp_path / "tests" / "test_a.py").read_text(encoding="utf-8") == "orig\n"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_tool_call_id_fallback(tmp_path):
    ws = _ws(tmp_path)
    read_reply = _reply(
        "",
        tool_calls=[
            {"function": {"name": "read", "arguments": json.dumps({"path": "a.py"})}}
        ],
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    report = _reply(
        _report("done", "x", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([read_reply, done, report])

    run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    tool_msg = client.bodies[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_1_0"


def test_rewrite_loop_on_third_write(tmp_path):
    ws = _ws(tmp_path)
    paths = ["a.py", "./a.py", "a.py"]
    writes = [
        _reply(
            "",
            tool_calls=[_call("write", path=p, content=f"v{i}\n")],
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        )
        for i, p in enumerate(paths)
    ]
    report = _reply(_report("done", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([*writes, report])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "rewrite_loop"
    assert result.detail == "a.py"
    assert result.turns == 3
    assert result.report == {"outcome": "done", "summary": "x", "concerns": []}
    assert result.report_error == ""
    assert len(client.bodies) == 4


def test_turn_limit(tmp_path):
    ws = _ws(tmp_path)
    reads = [
        _reply(
            "",
            tool_calls=[_call("read", path="a.py")],
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        )
        for _ in range(2)
    ]
    report = _reply(_report("blocked", "x", []), usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([*reads, report])

    result = run_worker(
        _args(max_turns=2),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
    )
    assert result.stop == "turn_limit"
    assert result.detail == "2 turns"
    assert result.turns == 2
    assert result.report == {"outcome": "blocked", "summary": "x", "concerns": []}
    assert result.report_error == ""
    assert len(client.bodies) == 3


def test_endpoint_error_stops(tmp_path):
    ws = _ws(tmp_path)
    client = FakeClient([LLMError("down")])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "endpoint"
    assert result.detail == "down"
    assert result.turns == 0
    assert result.report is None
    assert result.report_error == ""
    assert len(client.bodies) == 1


def test_endpoint_detail_is_cut(tmp_path):
    ws = _ws(tmp_path)
    client = FakeClient([LLMError("x" * 300)])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "endpoint"
    assert result.detail == "x" * 200
    assert result.turns == 0
    assert result.report is None
    assert result.report_error == ""
    assert len(client.bodies) == 1


def test_report_not_json(tmp_path):
    ws = _ws(tmp_path)
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    bad = _reply("nope", usage={"prompt_tokens": 1, "completion_tokens": 1})
    client = FakeClient([done, bad])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.report is None
    assert result.report_error == "report is not JSON"


def test_report_invalid(tmp_path):
    ws = _ws(tmp_path)
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    bad = _reply(
        json.dumps({"outcome": "maybe", "summary": "x", "concerns": []}),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([done, bad])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.report is None
    assert result.report_error.startswith("report is invalid:")


def test_report_endpoint_error(tmp_path):
    ws = _ws(tmp_path)
    done = _reply("done", usage={"prompt_tokens": 2, "completion_tokens": 1})
    client = FakeClient([done, LLMError("down")])

    result = run_worker(_args(), ws, client, model="local", params={"temperature": 1.0})
    assert result.stop == "finished"
    assert result.report is None
    assert result.report_error == "endpoint: down"


@pytest.fixture
def fake_server():
    @contextlib.contextmanager
    def make(responses: list[tuple[int, bytes]]):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                status, body = responses.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    return make


def _completion(content: str, finish_reason: str = "stop") -> bytes:
    message: dict = {"role": "assistant", "content": content}
    return json.dumps(
        {
            "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode("utf-8")


def _args_file(tmp_path: Path) -> Path:
    path = tmp_path / "args.json"
    path.write_text(
        json.dumps(
            {
                "task": "Change a.py so the value x becomes 2 instead of 1.",
                "files": ["a.py"],
                "acceptance_criteria": {"commands": ["pytest -q"]},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_schema_errors_on_narrated_calls(tmp_path):
    ws = _ws(tmp_path)
    replies = [
        _reply(
            "CALL: read a.py",
            usage={"prompt_tokens": 2, "completion_tokens": 1},
        )
        for _ in range(4)
    ]
    report = _reply(
        _report("blocked", "x", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([*replies, report])

    result = run_worker(
        _args(),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
        narrated_markers=("CALL:",),
    )

    assert result.stop == "schema_errors"
    assert result.detail == "4 bad calls"
    assert result.turns == 4
    assert result.bad_calls == 4
    assert result.report == {"outcome": "blocked", "summary": "x", "concerns": []}
    assert result.report_error == ""
    assert len(client.bodies) == 5


def test_log_events(tmp_path):
    ws = _ws(tmp_path)
    write_reply = _reply(
        "",
        tool_calls=[_call("write", path="a.py", content="x = 2\n")],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    done_reply = _reply("done", usage={"prompt_tokens": 10, "completion_tokens": 5})
    report_reply = _reply(
        _report("done", "changed a.py", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([write_reply, done_reply, report_reply])
    log = tmp_path / "run.jsonl"

    run_worker(
        _args(),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
        log=log,
    )

    events = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["model", "tool", "model", "report"]
    model_keys = {
        "event",
        "turn",
        "finish_reason",
        "usage",
        "timings",
        "reasoning_chars",
        "content",
        "tool_calls",
    }
    tool_keys = {"event", "turn", "name", "outcome", "text"}
    report_keys = {
        "event",
        "finish_reason",
        "usage",
        "timings",
        "reasoning_chars",
        "content",
    }
    assert set(events[0]) == model_keys
    assert set(events[1]) == tool_keys
    assert set(events[2]) == model_keys
    assert set(events[3]) == report_keys
    assert events[1]["name"] == "write"
    assert events[1]["outcome"] == "ok"


def test_log_cuts_long_content(tmp_path):
    ws = _ws(tmp_path)
    long_reply = _reply(
        "y" * 5000,
        usage={"prompt_tokens": 2, "completion_tokens": 1},
    )
    report = _reply(
        _report("done", "x", []),
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    client = FakeClient([long_reply, report])
    log = tmp_path / "run.jsonl"

    run_worker(
        _args(),
        ws,
        client,
        model="local",
        params={"temperature": 1.0},
        log=log,
    )

    model_event = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert len(model_event["content"]) == LOG_TEXT_LIMIT
    assert model_event["content"] == "y" * LOG_TEXT_LIMIT


def test_cli_runs_against_fake_server(tmp_path, capsys, fake_server):
    report = _report("done", "changed a.py", [])
    args_path = _args_file(tmp_path)
    with fake_server([(200, _completion("done")), (200, _completion(report))]) as url:
        code = main(
            [
                "--args",
                str(args_path),
                "--repo",
                str(tmp_path),
                "--base-url",
                url,
                "--no-sandbox",
            ]
        )
    parsed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert parsed["stop"] == "finished"
    assert parsed["report"] == {"outcome": "done", "summary": "changed a.py", "concerns": []}


def test_cli_passes_profile_markers(tmp_path, monkeypatch):
    recorded: list[dict] = []

    def fake_worker(*args: object, **kwargs: dict) -> WorkerResult:
        recorded.append(kwargs)
        return WorkerResult("finished", "", 0, 0, {"prompt": 0, "completion": 0}, {}, "")

    monkeypatch.setattr("router.worker.run_worker", fake_worker)
    args_path = _args_file(tmp_path)
    code = main(
        [
            "--args",
            str(args_path),
            "--repo",
            str(tmp_path),
            "--base-url",
            "http://127.0.0.1:9999",
            "--no-sandbox",
        ]
    )
    assert code == 0
    assert recorded[0]["narrated_markers"] == (
        tuple(load_profile("bonsai2-27b")["narrated_markers"])
    )
    assert recorded[0]["params"] == request_params(load_profile("bonsai2-27b"), "agent")


def test_cli_bad_args(tmp_path, capsys):
    args_path = tmp_path / "args.json"
    args_path.write_text(
        json.dumps(
            {
                "task": "Change a.py so the value x becomes 2 instead of 1.",
                "acceptance_criteria": {"commands": ["pytest -q"]},
            }
        ),
        encoding="utf-8",
    )
    code = main(["--args", str(args_path), "--repo", str(tmp_path)])
    assert code == 2
    assert capsys.readouterr().err.startswith("bad arguments: ")


def test_cli_bad_profile(tmp_path, capsys):
    args_path = _args_file(tmp_path)
    code = main(
        [
            "--args",
            str(args_path),
            "--repo",
            str(tmp_path),
            "--profile",
            "nope",
        ]
    )
    assert code == 2
    assert capsys.readouterr().err.startswith("bad profile: ")


def test_cli_bad_base_url(tmp_path, capsys):
    args_path = _args_file(tmp_path)
    code = main(
        [
            "--args",
            str(args_path),
            "--repo",
            str(tmp_path),
            "--base-url",
            "http://example.com:8080",
        ]
    )
    assert code == 2
    assert capsys.readouterr().err.startswith("bad base url: ")
