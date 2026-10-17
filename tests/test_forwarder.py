"""Tests for bench/forwarder.py: the DeepSeek row's pass-through, against a fake upstream."""

import importlib.util
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "forwarder", Path(__file__).resolve().parents[1] / "bench" / "forwarder.py"
)
forwarder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(forwarder)

USAGE = {
    "prompt_cache_hit_tokens": 1000,
    "prompt_cache_miss_tokens": 2000,
    "completion_tokens": 500,
}


@pytest.fixture
def upstream():
    """A fake chat-completions endpoint: records each request, answers with a tool call."""
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"auth": self.headers["Authorization"], "body": body})
            message = {
                "role": "assistant",
                "content": "",
                "reasoning_content": f"thought {len(seen)}",
                "tool_calls": [{"id": f"call{len(seen)}", "type": "function"}],
            }
            data = json.dumps({"choices": [{"message": message}], "usage": USAGE}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/chat/completions", seen
    server.shutdown()


def body(messages: list[dict], **extra) -> dict:
    return {"model": "deepseek-flash", "messages": messages, **extra}


def test_reasoning_goes_back_by_tool_call_id_and_the_schema_becomes_json_object(
    tmp_path, upstream
):
    url, seen = upstream
    fwd = forwarder.Forwarder("k", 1.0, tmp_path / "usage.jsonl", url)
    first = fwd.handle("t1", body([{"role": "user", "content": "go"}]))
    assistant = {k: first["choices"][0]["message"][k] for k in ("role", "content", "tool_calls")}
    fwd.handle(
        "t1",
        body(
            [{"role": "user", "content": "go"}, assistant],
            response_format={"type": "json_schema", "json_schema": {}},
        ),
    )
    assert seen[0]["auth"] == "Bearer k"
    sent = seen[1]["body"]
    assert sent["messages"][1]["reasoning_content"] == "thought 1"
    assert sent["response_format"] == {"type": "json_object"}
    calls = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert [c["missing_reasoning"] for c in calls] == [0, 0]


def test_cost_is_half_off_peak():
    peak_time = datetime(2026, 10, 15, 2, tzinfo=UTC)  # a Thursday, 02:00 UTC
    off_time = datetime(2026, 10, 15, 12, tzinfo=UTC)
    full = (1000 * 0.006 + 2000 * 0.30 + 500 * 1.20) / 1e6
    assert forwarder.cost_usd("deepseek-flash", USAGE, peak_time) == pytest.approx(full)
    assert forwarder.cost_usd("deepseek-flash", USAGE, off_time) == pytest.approx(full / 2)


def test_a_spent_budget_refuses_without_calling_upstream(tmp_path, upstream):
    url, seen = upstream
    log = tmp_path / "usage.jsonl"
    log.write_text(json.dumps({"event": "call", "cost_usd": 2.0}) + "\n")
    fwd = forwarder.Forwarder("k", 1.0, log, url)
    with pytest.raises(forwarder.UpstreamError) as error:
        fwd.handle("t1", body([{"role": "user", "content": "go"}]))
    assert error.value.status == 402
    assert seen == []


def test_an_unknown_model_is_refused(tmp_path, upstream):
    url, seen = upstream
    fwd = forwarder.Forwarder("k", 1.0, tmp_path / "usage.jsonl", url)
    with pytest.raises(forwarder.UpstreamError) as error:
        fwd.handle("t1", {"model": "gpt-x", "messages": []})
    assert error.value.status == 400
    assert seen == []
