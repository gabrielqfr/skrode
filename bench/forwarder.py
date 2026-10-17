"""The DeepSeek row's forwarder: the worker's chat-completions calls on 127.0.0.1, passed on.

Bench tooling, not part of skrode (D22). skrode stays unchanged: it talks to
http://127.0.0.1:PORT/<tag>, believing it is llama-server. Only this process reads the key.

DeepSeek speaks the same OpenAI format, so this is a pass-through with three fixes:
- reasoning_content: in thinking mode with tools, DeepSeek returns 400 unless every earlier
  assistant turn carries its reasoning_content back. The worker keeps only content and
  tool_calls, so it is put back here, keyed by tool-call ids (or the content, for a turn without
  calls).
- response_format: DeepSeek takes json_object only, not json_schema. The worker's report prompt
  already asks for JSON and names the fields; the worker validates the reply against the schema.
- usage: dollars per call from prompt_cache_hit_tokens / prompt_cache_miss_tokens, at the peak or
  off-peak rate for the call's UTC time, logged per call; calls are refused once the budget is
  spent.

Run: DEEPSEEK_API_KEY=... python3 bench/forwarder.py --port 8091 --budget-usd 3 --log usage.jsonl
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "https://api.deepseek.com/chat/completions"
# $ per MTok, peak; off-peak is half (api-docs.deepseek.com/quick_start/pricing, 15 Oct 2026)
PRICES = {
    "deepseek-flash": {"hit": 0.006, "miss": 0.30, "out": 1.20},
    "deepseek-v4-pro": {"hit": 0.044, "miss": 1.32, "out": 3.96},
}
RETRY_STATUS = {429, 500, 502, 503, 504}


def peak(now: datetime) -> bool:
    # 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday (Chinese public holidays ignored)
    return now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)


def cost_usd(model: str, usage: dict, now: datetime) -> float:
    price = PRICES[model]
    factor = 1.0 if peak(now) else 0.5
    hit = usage.get("prompt_cache_hit_tokens") or 0
    miss = usage.get("prompt_cache_miss_tokens") or 0
    out = usage.get("completion_tokens") or 0
    return factor * (hit * price["hit"] + miss * price["miss"] + out * price["out"]) / 1e6


def turn_key(message: dict) -> tuple:
    calls = message.get("tool_calls") or []
    if calls:
        return ("calls", tuple(c["id"] for c in calls))
    return ("text", message.get("content") or "")


class UpstreamError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Forwarder:
    def __init__(self, key: str, budget: float, log: Path, upstream: str = UPSTREAM) -> None:
        self.key = key
        self.upstream = upstream
        self.budget = budget
        self.log = log
        self.lock = threading.Lock()
        self.reasoning: dict[str, dict[tuple, str]] = {}
        self.spent = sum(
            json.loads(line).get("cost_usd", 0.0)
            for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
        ) if log.exists() else 0.0

    def _write(self, event: dict) -> None:
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            self.upstream, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        last = ""
        for i in range(5):
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                text = error.read().decode("utf-8", errors="replace")[:500]
                if error.code not in RETRY_STATUS:
                    raise UpstreamError(error.code, text) from None
                last = f"HTTP {error.code}: {text}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last = str(error)
            time.sleep(2 ** i)
        raise UpstreamError(502, f"upstream failed after 5 attempts: {last}")

    def handle(self, tag: str, body: dict) -> dict:
        model = body.get("model")
        if model not in PRICES:
            raise UpstreamError(400, f"forwarder: unknown model {model!r}")
        with self.lock:
            if self.spent >= self.budget:
                raise UpstreamError(402, f"forwarder: budget ${self.budget:.2f} spent")
            seen = self.reasoning.setdefault(tag, {})
        body = dict(body)
        messages = []
        missing = 0
        for m in body["messages"]:
            if m.get("role") == "assistant":
                m = dict(m)
                reasoning = seen.get(turn_key(m))
                if reasoning is None:
                    missing += 1
                else:
                    m["reasoning_content"] = reasoning
            messages.append(m)
        body["messages"] = messages
        fmt = body.get("response_format")
        if fmt and fmt.get("type") == "json_schema":
            body["response_format"] = {"type": "json_object"}
        start = time.monotonic()
        try:
            reply = self._post(body)
        except UpstreamError as error:
            self._write({"event": "error", "tag": tag, "status": error.status,
                         "detail": str(error)[:500]})
            raise
        now = datetime.now(UTC)
        usage = reply.get("usage") or {}
        cost = cost_usd(model, usage, now)
        choice = (reply.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        with self.lock:
            self.spent += cost
            seen[turn_key(message)] = message.get("reasoning_content") or ""
            self._write({
                "event": "call", "tag": tag, "model": model, "peak": peak(now),
                "report": "response_format" in body, "messages": len(messages),
                "missing_reasoning": missing, "finish_reason": choice.get("finish_reason"),
                "tool_calls": len(message.get("tool_calls") or []),
                "reasoning_chars": len(message.get("reasoning_content") or ""),
                "usage": usage, "cost_usd": cost, "spent_usd": self.spent,
                "latency_s": round(time.monotonic() - start, 2),
            })
        reply.setdefault("usage", {})["cost_usd"] = cost
        return reply


def make_handler(forwarder: Forwarder):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.endswith("/health"):
                self._send(200, {"status": "ok", "spent_usd": forwarder.spent})
            else:
                self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:
            parts = self.path.strip("/").split("/")
            # benchrun prefixes the tag (/bench/<task>.r<k>/...); the tag is the last segment
            if parts[-3:] != ["v1", "chat", "completions"] or len(parts) < 4:
                self._send(404, {"error": {"message": "POST /[prefix/]<tag>/v1/chat/completions"}})
                return
            tag = parts[-4]
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self._send(200, forwarder.handle(tag, body))
            except UpstreamError as error:
                self._send(error.status, {"error": {"message": str(error)}})
            except Exception as error:  # a forwarder bug must reach the router as a 500, not hang
                forwarder._write({"event": "error", "tag": tag, "status": 500,
                                  "detail": f"{type(error).__name__}: {error}"[:500]})
                self._send(500, {"error": {"message": f"forwarder: {error}"}})

        def log_message(self, *args) -> None:
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--budget-usd", type=float, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--upstream", default=UPSTREAM)
    ns = parser.parse_args()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        print("forwarder: set DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    forwarder = Forwarder(key, ns.budget_usd, Path(ns.log), ns.upstream)
    server = ThreadingHTTPServer(("127.0.0.1", ns.port), make_handler(forwarder))
    print(f"forwarding 127.0.0.1:{ns.port} to DeepSeek, spent ${forwarder.spent:.4f} of "
          f"${ns.budget_usd:.2f}", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
