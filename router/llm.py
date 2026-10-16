"""Chat-completion client for the local model server, which must be on 127.0.0.1."""

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
CHAT_PATH = "/v1/chat/completions"
HEALTH_PATH = "/health"


class LLMError(RuntimeError):
    """The model server is down, returned an error, or sent something that is not a reply."""


@dataclass(frozen=True)
class ChatReply:
    message: dict        # choices[0]["message"], as the server sent it
    finish_reason: str   # choices[0]["finish_reason"], "" when absent
    usage: dict          # the reply's "usage", {} when absent
    timings: dict        # llama-server's "timings", {} when absent


# a redirect could lead off 127.0.0.1, so a 3xx ends the request like a 4xx
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LLMClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 600.0,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or not re.fullmatch(r"127\.0\.0\.1(:\d+)?", parsed.netloc)
        ):
            raise ValueError(
                f"the model server must be on http://127.0.0.1, not {base_url}"
            )
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.attempts = attempts
        self.sleep = sleep
        # no proxy whatever the environment says: the worker talks to 127.0.0.1 only
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def health(self, timeout_s: float = 2.0) -> str:
        """Return ok, loading (llama-server answers 503 while it loads its model) or down."""
        request = urllib.request.Request(self.base_url + HEALTH_PATH)
        try:
            with self.opener.open(request, timeout=timeout_s):
                return "ok"
        except urllib.error.HTTPError as exc:
            return "loading" if exc.code == 503 else "down"
        except (OSError, http.client.HTTPException):
            return "down"

    def chat(self, body: dict) -> ChatReply:
        data = json.dumps(body).encode()
        request = urllib.request.Request(
            self.base_url + CHAT_PATH,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        last = ""
        for i in range(self.attempts):
            try:
                with self.opener.open(request, timeout=self.timeout_s) as response:
                    raw = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    text = exc.read().decode("utf-8", errors="replace")[:200]
                    raise LLMError(f"HTTP {exc.code}: {text}") from None
                last = f"HTTP {exc.code}"
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, TimeoutError):
                    raise LLMError(f"no reply within {self.timeout_s:g} s") from None
                last = str(exc.reason)
            except TimeoutError:
                raise LLMError(f"no reply within {self.timeout_s:g} s") from None
            except (ConnectionError, http.client.HTTPException) as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                return _parse_reply(raw)
            if i != self.attempts - 1:
                self.sleep(2 ** i)
        raise LLMError(f"endpoint failed after {self.attempts} attempts: {last}") from None


def _parse_reply(raw: bytes) -> ChatReply:
    try:
        reply = json.loads(raw)
    except ValueError:
        raise LLMError("reply is not JSON") from None
    if not isinstance(reply, dict):
        raise LLMError("reply has no choices") from None
    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("reply has no choices") from None
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(
        choice.get("message"), dict
    ):
        raise LLMError("reply has no choices") from None
    return ChatReply(
        message=choice["message"],
        finish_reason=choice.get("finish_reason") or "",
        usage=reply.get("usage") or {},
        timings=reply.get("timings") or {},
    )
