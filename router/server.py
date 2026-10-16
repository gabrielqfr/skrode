"""The MCP stdio server's tools: the job tools, stats and list_endpoints."""

import json
import time
from collections.abc import Callable

import anyio
import mcp_types as types
from jsonschema import Draft202012Validator, exceptions
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server

from router.jobs import ARGS_SCHEMA, _error_line, load_records
from router.manager import WAIT_LIMIT_S, JobManager
from router.stats import summarize

PROGRESS_INTERVAL_S = 30.0  # Claude Code drops a stdio call that is silent for 30 minutes

_JOB_ID = {"type": "string", "pattern": "^j-[0-9]{8}-[0-9a-f]{6}$"}
_ONE_JOB = {
    "type": "object",
    "additionalProperties": False,
    "required": ["job_id"],
    "properties": {"job_id": _JOB_ID},
}
SCHEMAS: dict[str, dict] = {
    "run_local_subagent": ARGS_SCHEMA,
    "job_status": _ONE_JOB,
    "wait_jobs": {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_ids"],
        "properties": {
            "job_ids": {"type": "array", "minItems": 1, "maxItems": 10, "items": _JOB_ID},
            "wait_s": {"type": "number", "minimum": 0, "maximum": WAIT_LIMIT_S, "default": 50},
        },
    },
    "cancel_job": _ONE_JOB,
    "stats": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"last": {"type": "integer", "minimum": 1, "maximum": 10000}},
    },
    "list_endpoints": {"type": "object", "additionalProperties": False, "properties": {}},
}
DESCRIPTIONS: dict[str, str] = {
    "run_local_subagent": ARGS_SCHEMA["description"],
    "job_status": (
        "State of one job: queued (with how many jobs are ahead of it), running (its latest "
        "progress line and elapsed seconds), done (its record), or unknown."
    ),
    "wait_jobs": (
        "Wait until every listed job is done or wait_s seconds pass (at most 50), then return "
        "each job's state as job_status does."
    ),
    "cancel_job": (
        "Cancel a job. A queued job ends at once; a running job stops before its next model "
        "call. Returns the job's state as job_status does."
    ),
    "stats": (
        "Totals over the router's finished jobs, all of them or the last N: how many passed, "
        "escalated or failed, each reason's count, median and total wall time in seconds, "
        "and local tokens."
    ),
    "list_endpoints": (
        "The model endpoints the router's jobs run on: name, base URL, served model alias, "
        "profile, and health (ok, loading or down), checked when called."
    ),
}


def compact(value: object) -> str:
    """One line of JSON: the client counts every space as a token."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _result(value: object, *, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=compact(value))], is_error=error
    )


def bad_arguments(name: str, arguments: dict) -> str | None:
    errors = list(Draft202012Validator(SCHEMAS[name]).iter_errors(arguments))
    if not errors:
        return None
    return f"bad arguments: {exceptions.best_match(errors).message}"[:300]


def progress_message(status: dict) -> str:
    if status["state"] == "queued":
        return f"queued, {status['ahead']} ahead"
    return status.get("progress", "")


async def run_blocking(
    ctx: ServerRequestContext,
    manager: JobManager,
    job_id: str,
    interval_s: float,
) -> dict:
    start = time.monotonic()
    try:
        while True:
            [status] = await anyio.to_thread.run_sync(
                manager.wait, [job_id], interval_s, abandon_on_cancel=True
            )
            if status["state"] == "done":
                return status["record"]
            await ctx.session.report_progress(
                time.monotonic() - start, None, progress_message(status)
            )
    except anyio.get_cancelled_exc_class():
        # the client gave up on the call, so nobody will read the record
        manager.cancel(job_id)
        raise


def build_server(
    manager: JobManager,
    *,
    list_endpoints: Callable[[], list[dict]] = list,
    progress_interval_s: float = PROGRESS_INTERVAL_S,
) -> Server:
    async def list_tools(
        ctx: ServerRequestContext,
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=DESCRIPTIONS[name],
                    input_schema=SCHEMAS[name],
                )
                for name in SCHEMAS
            ]
        )

    async def call_tool(
        ctx: ServerRequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        if name not in SCHEMAS:
            return _result({"error": f"unknown tool: {name}"}, error=True)
        message = bad_arguments(name, arguments)
        if message is not None:
            return _result({"error": message}, error=True)
        try:
            return await dispatch(ctx, name, arguments)
        except Exception as error:
            return _result({"error": f"internal: {_error_line(error)}"}, error=True)

    async def dispatch(
        ctx: ServerRequestContext, name: str, arguments: dict
    ) -> types.CallToolResult:
        if name == "run_local_subagent":
            job_id = manager.submit(arguments)
            if not arguments.get("wait", True):
                return _result(manager.status(job_id))
            return _result(await run_blocking(ctx, manager, job_id, progress_interval_s))
        if name == "job_status":
            return _result(manager.status(arguments["job_id"]))
        if name == "wait_jobs":
            states = await anyio.to_thread.run_sync(
                manager.wait,
                arguments["job_ids"],
                arguments.get("wait_s", WAIT_LIMIT_S),
                abandon_on_cancel=True,
            )
            return _result(states)
        if name == "cancel_job":
            return _result(manager.cancel(arguments["job_id"]))
        if name == "stats":
            records = await anyio.to_thread.run_sync(load_records, manager.state_dir)
            if "last" in arguments:
                records = records[-arguments["last"] :]
            return _result(summarize(records))
        if name == "list_endpoints":
            return _result(await anyio.to_thread.run_sync(list_endpoints))
        return _result({"error": f"unknown tool: {name}"}, error=True)

    return Server("hybrid-router", on_list_tools=list_tools, on_call_tool=call_tool)
