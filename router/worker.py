"""The worker loop: one task in one directory, the local model calling tools until it stops.

S7 adds the worktree, the acceptance commands and the retries around it.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import Template
from typing import Protocol

from jsonschema import Draft202012Validator, exceptions

from router.llm import DEFAULT_BASE_URL, ChatReply, LLMClient, LLMError
from router.profiles import ProfileError, load_profile, request_params
from router.tools import TOOL_SPECS, Outcome, Workspace

_REPO = Path(__file__).resolve().parents[1]
PROMPTS_PATH = _REPO / "prompts" / "worker.md"
REPORT_SCHEMA: dict = json.loads(
    (_REPO / "schemas" / "worker_report.json").read_text(encoding="utf-8")
)
REPORT_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "worker_report",
        "strict": True,
        "schema": {k: v for k, v in REPORT_SCHEMA.items() if k != "$schema"},
    },
}
DEFAULT_MAX_TURNS = 15  # the default in schemas/run_local_subagent.json
BAD_CALL_LIMIT = 3  # more than this many bad calls ends the job (schema_errors)
REWRITE_LIMIT = 3  # a third write to one path ends it (rewrite_loop)
STOPS = (
    "finished",
    "must_not_touch",
    "schema_errors",
    "rewrite_loop",
    "turn_limit",
    "endpoint",
)
_JSON_CALL = re.compile(r'\{\s*"name"\s*:\s*"(?:read|write|edit|bash|grep)"')
ARGS_SCHEMA_PATH = _REPO / "schemas" / "run_local_subagent.json"
LOG_TEXT_LIMIT = 2_000


class ChatClient(Protocol):
    def chat(self, body: dict) -> ChatReply: ...


@dataclass
class WorkerResult:
    stop: str                 # one of STOPS
    detail: str               # the path, the count or the error; "" when finished
    turns: int                # model calls in the loop; the report call is not one
    bad_calls: int
    tokens: dict[str, int]    # {"prompt": n, "completion": n}, summed over every call
    report: dict | None       # the valid worker report, or None
    report_error: str         # why report is None after a report call; "" otherwise
    messages: list[dict] = field(default_factory=list)  # the conversation at the stop

    def to_dict(self) -> dict:
        data = asdict(self)
        del data["messages"]
        return data


def load_prompts(path: Path = PROMPTS_PATH) -> dict[str, str]:
    prompts: dict[str, str] = {}
    current = ""
    body: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                prompts[current] = "\n".join(body).strip()
            current = line[3:].strip()
            body = []
        else:
            body.append(line)
    if current:
        prompts[current] = "\n".join(body).strip()
    return prompts


def render(prompts: dict[str, str], name: str, **values: object) -> str:
    return Template(prompts[name]).substitute(values)


def bullets(items: list[str]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"- {item}" for item in items)


def task_message(prompts: dict[str, str], args: dict) -> str:
    criteria = args["acceptance_criteria"]
    return render(
        prompts,
        "task",
        task=args["task"],
        files=bullets(args["files"]),
        must_not_touch=bullets(criteria.get("must_not_touch", [])),
        commands=bullets(criteria["commands"]),
        max_turns=args.get("max_turns", DEFAULT_MAX_TURNS),
    )


def is_narrated(content: str, markers: Iterable[str] = ()) -> bool:
    return any(m in content for m in markers) or bool(_JSON_CALL.search(content))


def build_body(
    model: str, params: dict, messages: list[dict], *, report: bool = False
) -> dict:
    body = {"model": model, **params, "messages": list(messages)}
    if report:
        body["response_format"] = REPORT_FORMAT
    else:
        body["tools"] = TOOL_SPECS
        body["tool_choice"] = "auto"
    return body


def _guard(bad_calls: int, writes: dict[str, int]) -> tuple[str, str] | None:
    if bad_calls > BAD_CALL_LIMIT:
        return ("schema_errors", f"{bad_calls} bad calls")
    for path, count in writes.items():
        if count >= REWRITE_LIMIT:
            return ("rewrite_loop", path)
    return None


def _arguments_text(arguments: object) -> str:
    text = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return text[:LOG_TEXT_LIMIT]


def _log(log: Path | None, event: dict) -> None:
    if log is None:
        return
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_worker(
    args: dict,
    workspace: Workspace,
    client: ChatClient,
    *,
    model: str,
    params: dict,
    prompts: dict[str, str] | None = None,
    narrated_markers: tuple[str, ...] = (),
    log: Path | None = None,
    history: list[dict] | None = None,
) -> WorkerResult:
    if prompts is None:
        prompts = load_prompts()
    max_turns = args.get("max_turns", DEFAULT_MAX_TURNS)
    if history is None:
        messages = [
            {"role": "system", "content": prompts["system"]},
            {"role": "user", "content": task_message(prompts, args)},
        ]
    else:
        messages = list(history)
    turns = 0
    bad_calls = 0
    tokens: dict[str, int] = {"prompt": 0, "completion": 0}
    write_counts: dict[str, int] = {}
    stop = "finished"
    detail = ""

    while True:
        if turns == max_turns:
            stop = "turn_limit"
            detail = f"{max_turns} turns"
            break
        try:
            reply = client.chat(build_body(model, params, messages))
        except LLMError as error:
            stop = "endpoint"
            detail = str(error)[:200]
            break
        turns += 1
        tokens["prompt"] += reply.usage.get("prompt_tokens", 0)
        tokens["completion"] += reply.usage.get("completion_tokens", 0)

        message = reply.message
        content = message.get("content") or ""
        calls = message.get("tool_calls") or []

        _log(
            log,
            {
                "event": "model",
                "turn": turns,
                "finish_reason": reply.finish_reason,
                "usage": reply.usage,
                "timings": reply.timings,
                "reasoning_chars": len(message.get("reasoning_content") or ""),
                "content": content[:LOG_TEXT_LIMIT],
                "tool_calls": calls,
            },
        )

        if reply.finish_reason == "length":
            bad_calls += 1
            messages.append(
                {"role": "user", "content": render(prompts, "correction.length")}
            )
            guard = _guard(bad_calls, write_counts)
            if guard is not None:
                stop, detail = guard
                break
            continue

        if not calls and is_narrated(content, narrated_markers):
            bad_calls += 1
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {"role": "user", "content": render(prompts, "correction.narrated")}
            )
            guard = _guard(bad_calls, write_counts)
            if guard is not None:
                stop, detail = guard
                break
            continue

        if not calls:
            messages.append({"role": "assistant", "content": content})
            stop = "finished"
            detail = ""
            break

        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for i, call in enumerate(calls):
            name = call.get("function", {}).get("name", "")
            arguments = call.get("function", {}).get("arguments", "")
            arguments_text = _arguments_text(arguments)
            result = workspace.run(name, arguments)
            _log(
                log,
                {
                    "event": "tool",
                    "turn": turns,
                    "name": name,
                    "arguments": arguments_text,
                    "outcome": result.outcome.value,
                    "text": result.text[:LOG_TEXT_LIMIT],
                },
            )
            if result.outcome == Outcome.MUST_NOT_TOUCH:
                stop = "must_not_touch"
                detail = result.text
                break
            if result.outcome == Outcome.INVALID:
                bad_calls += 1
                reply_text = render(
                    prompts, "correction.invalid", tool=name, error=result.text
                )
            elif result.outcome == Outcome.UNKNOWN_TOOL:
                bad_calls += 1
                reply_text = render(
                    prompts, "correction.unknown_tool", tool=result.text
                )
            elif result.outcome == Outcome.OUTSIDE_FILES:
                reply_text = render(
                    prompts,
                    "correction.outside_files",
                    path=result.text,
                    files=", ".join(args["files"]),
                )
            else:
                reply_text = result.text
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{turns}_{i}",
                    "content": reply_text,
                }
            )
            if result.outcome == Outcome.OK and name == "write":
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                rel = workspace.relpath(parsed["path"])
                write_counts[rel] = write_counts.get(rel, 0) + 1
        else:
            guard = _guard(bad_calls, write_counts)
            if guard is not None:
                stop, detail = guard
        if stop in ("must_not_touch", "schema_errors", "rewrite_loop"):
            break

    report: dict | None = None
    report_error = ""
    if stop in ("finished", "schema_errors", "rewrite_loop", "turn_limit"):
        try:
            reply = client.chat(
                build_body(
                    model,
                    params,
                    [*messages, {"role": "user", "content": prompts["report"]}],
                    report=True,
                )
            )
        except LLMError as error:
            report_error = f"endpoint: {error}"
        else:
            tokens["prompt"] += reply.usage.get("prompt_tokens", 0)
            tokens["completion"] += reply.usage.get("completion_tokens", 0)
            content = reply.message.get("content") or ""
            _log(
                log,
                {
                    "event": "report",
                    "finish_reason": reply.finish_reason,
                    "usage": reply.usage,
                    "timings": reply.timings,
                    "reasoning_chars": len(reply.message.get("reasoning_content") or ""),
                    "content": content[:LOG_TEXT_LIMIT],
                },
            )
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                report_error = "report is not JSON"
            else:
                best = exceptions.best_match(
                    Draft202012Validator(REPORT_SCHEMA).iter_errors(parsed)
                )
                if best is not None:
                    report_error = f"report is invalid: {best.message[:200]}"
                else:
                    report = parsed

    return WorkerResult(
        stop=stop,
        detail=detail,
        turns=turns,
        bad_calls=bad_calls,
        tokens=tokens,
        report=report,
        report_error=report_error,
        messages=messages,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the worker loop once, in --repo itself (no worktree yet), and print the "
            "result as JSON."
        ),
    )
    parser.add_argument("--args", required=True)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--model", default="local")
    parser.add_argument("--profile", default="bonsai2-27b")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--log", default=None)
    parser.add_argument("--no-sandbox", action="store_true")
    ns = parser.parse_args(argv)

    try:
        raw = Path(ns.args).read_text(encoding="utf-8")
    except OSError as error:
        print(f"bad arguments: {error}", file=sys.stderr)
        return 2
    try:
        args: dict = json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"bad arguments: {error}", file=sys.stderr)
        return 2
    schema = json.loads(ARGS_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(args))
    if errors:
        best = exceptions.best_match(errors)
        print(f"bad arguments: {best.message}", file=sys.stderr)
        return 2

    repo = Path(ns.repo) if ns.repo else Path(args.get("repo") or ".")
    if not repo.is_dir():
        print(f"bad arguments: {repo}", file=sys.stderr)
        return 2

    try:
        profile = load_profile(ns.profile)
        params = request_params(profile, "agent")
    except ProfileError as error:
        print(f"bad profile: {error}", file=sys.stderr)
        return 2

    try:
        client = LLMClient(ns.base_url)
    except ValueError as error:
        print(f"bad base url: {error}", file=sys.stderr)
        return 2

    criteria = args["acceptance_criteria"]
    workspace = Workspace(
        root=repo,
        files=args["files"],
        must_not_touch=criteria.get("must_not_touch", []),
        sandbox=not ns.no_sandbox,
    )
    result = run_worker(
        args,
        workspace,
        client,
        model=ns.model,
        params=params,
        narrated_markers=tuple(profile.get("narrated_markers", [])),
        log=Path(ns.log) if ns.log else None,
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
