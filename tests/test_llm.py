"""Tests for skrode.llm: the chat-completion client for the local model server."""

import contextlib
import http.client
import json
import socket
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from skrode.llm import LLMClient, LLMError


@pytest.fixture
def fake_server():
    @contextlib.contextmanager
    def make(responses: list[tuple[int, bytes]], pre_delay: float = 0.0):
        received: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(length)
                received.append(
                    {
                        "path": self.path,
                        "content_type": self.headers.get("Content-Type"),
                        "body": json.loads(payload),
                    }
                )
                item = responses.pop(0)
                if len(item) == 3:
                    status, body, headers = item
                else:
                    status, body = item
                    headers = {}
                if pre_delay:
                    time.sleep(pre_delay)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

            def do_GET(self) -> None:
                received.append({"path": self.path, "content_type": None, "body": None})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}", received
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    yield make


def _ok_reply(content: str) -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ]
        }
    ).encode()


def test_posts_body_to_chat_path(fake_server):
    body = {
        "model": "local",
        "messages": [{"role": "user", "content": "hello"}],
    }
    reply_body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        "timings": {"total": 0.05, "tts": 0.02},
    }
    with fake_server([(200, json.dumps(reply_body).encode())]) as (base_url, received):
        answer = LLMClient(base_url=base_url, timeout_s=5).chat(body)
    assert answer.message == {"role": "assistant", "content": "hi"}
    assert answer.finish_reason == "stop"
    assert answer.usage == {"prompt_tokens": 3, "completion_tokens": 1}
    assert answer.timings == {"total": 0.05, "tts": 0.02}
    assert len(received) == 1
    assert received[0]["path"] == "/v1/chat/completions"
    assert received[0]["body"] == body
    assert received[0]["content_type"] == "application/json"


def test_missing_fields_default_empty(fake_server):
    scripted = [
        (
            200,
            json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
            ).encode(),
        )
    ]
    with fake_server(scripted) as (base_url, received):
        reply = LLMClient(base_url=base_url, timeout_s=5).chat({"a": 1})
    assert reply.message == {"role": "assistant", "content": "hi"}
    assert reply.finish_reason == ""
    assert reply.usage == {}
    assert reply.timings == {}
    assert len(received) == 1


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8080",
        "http://10.0.0.1:8080",
        "https://127.0.0.1:8080",
        "http://example.com",
        "http://user@127.0.0.1:8080",
    ],
)
def test_refuses_hosts_other_than_loopback(base_url):
    with pytest.raises(ValueError) as excinfo:
        LLMClient(base_url=base_url)
    assert str(excinfo.value) == f"the model server must be on http://127.0.0.1, not {base_url}"


def test_ignores_proxy_environment(fake_server, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with fake_server([(200, _ok_reply("hi"))]) as (base_url, received):
        LLMClient(base_url=base_url, timeout_s=5).chat({"a": 1})
    assert len(received) == 1


def test_retries_server_errors(fake_server):
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    scripted = [
        (500, b"boom"),
        (503, b"unavailable"),
        (200, _ok_reply("ok")),
    ]
    with fake_server(scripted) as (base_url, received):
        reply = LLMClient(base_url=base_url, timeout_s=5, sleep=sleep).chat({"a": 1})
    assert reply.message == {"role": "assistant", "content": "ok"}
    assert len(received) == 3
    assert sleeps == [1, 2]


def test_gives_up_after_attempts(fake_server):
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    scripted = [(500, b"boom") for _ in range(3)]
    with fake_server(scripted) as (base_url, received), \
        pytest.raises(LLMError) as excinfo:
        LLMClient(base_url=base_url, timeout_s=5, sleep=sleep).chat({"a": 1})
    assert str(excinfo.value).startswith("endpoint failed after 3 attempts")
    assert len(received) == 3
    assert sleeps == [1, 2]


def test_client_error_not_retried(fake_server):
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    scripted = [(400, b"bad request")]
    with fake_server(scripted) as (base_url, received), \
        pytest.raises(LLMError) as excinfo:
        LLMClient(base_url=base_url, timeout_s=5, sleep=sleep).chat({"a": 1})
    assert str(excinfo.value) == "HTTP 400: bad request"
    assert len(received) == 1
    assert sleeps == []


def test_connection_refused_is_retried():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(LLMError) as excinfo:
        LLMClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout_s=5,
            attempts=2,
            sleep=sleep,
        ).chat({"a": 1})
    assert str(excinfo.value).startswith("endpoint failed after 2 attempts")
    assert sleeps == [1]


def test_timeout_not_retried(fake_server):
    scripted = [(200, _ok_reply("ok"))]
    with fake_server(scripted, pre_delay=1.0) as (base_url, received), \
        pytest.raises(LLMError) as excinfo:
        LLMClient(base_url=base_url, timeout_s=0.2).chat({"a": 1})
    assert str(excinfo.value) == "no reply within 0.2 s"
    assert len(received) == 1


def test_malformed_replies(fake_server):
    scripted = [
        (200, b"not json"),
        (200, b'{"choices": []}'),
    ]
    with fake_server(scripted) as (base_url, received):
        with pytest.raises(LLMError) as excinfo:
            LLMClient(base_url=base_url, timeout_s=5).chat({"a": 1})
        assert str(excinfo.value) == "reply is not JSON"
        with pytest.raises(LLMError) as excinfo:
            LLMClient(base_url=base_url, timeout_s=5).chat({"a": 1})
        assert str(excinfo.value) == "reply has no choices"
    assert len(received) == 2


def test_redirect_not_followed(fake_server):
    with fake_server([(200, _ok_reply("ok"))]) as (second_url, second_received):
        scripted = [(302, b"", {"Location": second_url + "/v1/chat/completions"})]
        with fake_server(scripted) as (first_url, first_received), \
            pytest.raises(LLMError) as excinfo:
            LLMClient(base_url=first_url, timeout_s=5).chat({"a": 1})
    assert str(excinfo.value).startswith("HTTP 302")
    assert len(first_received) == 1
    assert len(second_received) == 0


def test_incomplete_reply_is_retried():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        port = listener.getsockname()[1]
        sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        def serve() -> None:
            for _ in range(2):
                conn, _ = listener.accept()
                data = b""
                while b"\r\n\r\n" not in data:
                    data += conn.recv(65536)
                head, _, body = data.partition(b"\r\n\r\n")
                length = 0
                for line in head.splitlines():
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1].strip())
                while len(body) < length:
                    body += conn.recv(65536)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{\"cho")
                conn.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        with pytest.raises(LLMError) as excinfo:
            LLMClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout_s=5,
                attempts=2,
                sleep=sleep,
            ).chat({"a": 1})
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert str(excinfo.value).startswith("endpoint failed after 2 attempts: IncompleteRead")
    assert sleeps == [1]


def test_non_utf8_body_is_not_json(fake_server):
    with fake_server([(200, b"\x80")]) as (base_url, received), \
        pytest.raises(LLMError) as excinfo:
        LLMClient(base_url=base_url, timeout_s=5).chat({"a": 1})
    assert str(excinfo.value) == "reply is not JSON"
    assert len(received) == 1


def test_error_text_cut_by_characters(fake_server):
    with fake_server([(400, ("é" * 300).encode())]) as (base_url, received), \
        pytest.raises(LLMError) as excinfo:
        LLMClient(base_url=base_url, timeout_s=5).chat({"a": 1})
    assert str(excinfo.value) == "HTTP 400: " + "é" * 200
    assert len(received) == 1


class _RaisingOpener:
    """Stands in for LLMClient.opener: records each request, then raises."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls: list[tuple[str, str, float]] = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout))
        raise self.error


def test_health_is_ok_when_the_server_answers(fake_server):
    with fake_server([]) as (base_url, received):
        assert LLMClient(base_url=base_url).health() == "ok"
    assert received == [{"path": "/health", "content_type": None, "body": None}]


def test_health_is_down_when_nothing_listens():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert LLMClient(base_url=f"http://127.0.0.1:{port}").health() == "down"


def test_health_is_loading_on_503():
    client = LLMClient(base_url="http://127.0.0.1:9999")
    error = urllib.error.HTTPError(
        "http://127.0.0.1:9999/health", 503, "Service Unavailable", {}, None
    )
    client.opener = _RaisingOpener(error)
    assert client.health() == "loading"
    assert client.opener.calls == [("http://127.0.0.1:9999/health", "GET", 2.0)]


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("http://127.0.0.1:9999/health", 500, "Error", {}, None),
        urllib.error.HTTPError("http://127.0.0.1:9999/health", 302, "Found", {}, None),
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        TimeoutError("timed out"),
        ConnectionResetError(104, "Connection reset by peer"),
        http.client.BadStatusLine("not http"),
    ],
)
def test_health_is_down_on_any_other_failure(error):
    client = LLMClient(base_url="http://127.0.0.1:9999")
    client.opener = _RaisingOpener(error)
    assert client.health() == "down"


def test_health_uses_its_own_timeout():
    client = LLMClient(base_url="http://127.0.0.1:9999/", timeout_s=600.0)
    client.opener = _RaisingOpener(TimeoutError("timed out"))
    assert client.health(timeout_s=0.5) == "down"
    assert client.opener.calls == [("http://127.0.0.1:9999/health", "GET", 0.5)]
