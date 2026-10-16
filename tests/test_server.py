import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from mcp.client import Client

from router.manager import JobManager
from router.server import build_server

ARGS_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "run_local_subagent.json").read_text(
        encoding="utf-8"
    )
)
ARGS = {
    "task": "Add a test for the parser's empty input case.",
    "files": ["tests/test_parser.py"],
    "acceptance_criteria": {"commands": ["true"]},
}
MODES = ["legacy", "2026-07-28"]


class FakeRun:
    """Stands in for run_job: runs until released or stopped, like a worker between turns."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.job_ids: list[str] = []
        self.args: list[dict] = []

    def __call__(self, args, *, job_id, should_stop, progress):
        self.job_ids.append(job_id)
        self.args.append(args)
        progress("attempt 1 of 3: turn 1 of 40")
        while not self.release.wait(0.01):
            reason = should_stop()
            if reason is not None:
                return {"job_id": job_id, "status": "failed", "reason": reason}
        return {"job_id": job_id, "status": "passed", "summary": "Añadido: 1 test"}


def serve(fake, tmp_path, scenario, *, mode="legacy", interval=0.05):
    manager = JobManager(fake, profile_name="test-profile", state_dir=tmp_path)
    server = build_server(manager, progress_interval_s=interval)

    async def main():
        async with Client(server, mode=mode) as client:
            return await scenario(client)

    try:
        return anyio.run(main)
    finally:
        fake.release.set()
        manager.close()


def payload(result):
    return json.loads(result.content[0].text)


def release_after(fake, seconds):
    threading.Timer(seconds, fake.release.set).start()


@pytest.mark.parametrize("mode", MODES)
def test_lists_the_four_tools(tmp_path, mode):
    async def scenario(client):
        return (await client.list_tools()).tools

    tools = serve(FakeRun(), tmp_path, scenario, mode=mode)
    assert [tool.name for tool in tools] == [
        "run_local_subagent",
        "job_status",
        "wait_jobs",
        "cancel_job",
    ]
    assert tools[0].input_schema == ARGS_SCHEMA
    assert tools[0].description == ARGS_SCHEMA["description"]
    assert all(tool.description for tool in tools)
    for tool in tools[1:]:
        assert tool.input_schema["additionalProperties"] is False


@pytest.mark.parametrize("mode", MODES)
def test_a_blocking_call_returns_the_record_as_compact_json(tmp_path, mode):
    fake = FakeRun()
    fake.release.set()

    async def scenario(client):
        return await client.call_tool("run_local_subagent", ARGS)

    result = serve(fake, tmp_path, scenario, mode=mode)
    assert result.is_error is False
    assert result.content[0].text == (
        '{"job_id":"' + fake.job_ids[0] + '","status":"passed","summary":"Añadido: 1 test"}'
    )
    assert "Añadido" in result.content[0].text
    assert fake.args == [ARGS]


@pytest.mark.parametrize("mode", MODES)
def test_a_blocking_call_reports_progress_until_done(tmp_path, mode):
    fake = FakeRun()
    seen = []

    async def on_progress(progress, total, message):
        seen.append((progress, message))

    async def scenario(client):
        release_after(fake, 0.4)
        return await client.call_tool("run_local_subagent", ARGS, progress_callback=on_progress)

    result = serve(fake, tmp_path, scenario, mode=mode)
    assert payload(result)["status"] == "passed"
    assert len(seen) >= 2
    assert "attempt 1 of 3: turn 1 of 40" in [message for _, message in seen]
    values = [progress for progress, _ in seen]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_a_queued_call_reports_how_many_jobs_are_ahead(tmp_path):
    fake = FakeRun()
    seen = []

    async def on_progress(progress, total, message):
        seen.append(message)

    async def scenario(client):
        first = payload(await client.call_tool("run_local_subagent", {**ARGS, "wait": False}))
        release_after(fake, 0.3)
        second = await client.call_tool("run_local_subagent", ARGS, progress_callback=on_progress)
        return first, payload(second)

    first, second = serve(fake, tmp_path, scenario)
    assert first["state"] in ("queued", "running")
    assert second["job_id"] == fake.job_ids[1]
    assert second["status"] == "passed"
    assert "queued, 1 ahead" in seen


def test_wait_false_returns_at_once_and_the_job_can_be_polled(tmp_path):
    fake = FakeRun()

    async def scenario(client):
        submitted = payload(await client.call_tool("run_local_subagent", {**ARGS, "wait": False}))
        job_id = submitted["job_id"]
        await anyio.sleep(0.2)
        running = payload(await client.call_tool("job_status", {"job_id": job_id}))
        fake.release.set()
        waited = payload(await client.call_tool("wait_jobs", {"job_ids": [job_id], "wait_s": 5}))
        return submitted, running, waited

    submitted, running, waited = serve(fake, tmp_path, scenario)
    assert submitted["state"] in ("queued", "running")
    assert running["state"] == "running"
    assert running["progress"] == "attempt 1 of 3: turn 1 of 40"
    assert waited == [
        {
            "job_id": submitted["job_id"],
            "state": "done",
            "record": {
                "job_id": submitted["job_id"],
                "status": "passed",
                "summary": "Añadido: 1 test",
            },
        }
    ]


def test_wait_jobs_returns_after_wait_s_when_a_job_is_still_running(tmp_path):
    fake = FakeRun()

    async def scenario(client):
        job_id = payload(await client.call_tool("run_local_subagent", {**ARGS, "wait": False}))[
            "job_id"
        ]
        start = time.monotonic()
        states = payload(await client.call_tool("wait_jobs", {"job_ids": [job_id], "wait_s": 0.3}))
        return states, time.monotonic() - start

    states, took = serve(fake, tmp_path, scenario)
    assert states[0]["state"] == "running"
    assert 0.25 <= took < 2


def test_cancel_job_ends_a_queued_job_at_once(tmp_path):
    fake = FakeRun()

    async def scenario(client):
        await client.call_tool("run_local_subagent", {**ARGS, "wait": False})
        queued = payload(await client.call_tool("run_local_subagent", {**ARGS, "wait": False}))
        return queued, payload(await client.call_tool("cancel_job", {"job_id": queued["job_id"]}))

    queued, cancelled = serve(fake, tmp_path, scenario)
    assert queued["state"] == "queued"
    assert cancelled["state"] == "done"
    assert cancelled["record"]["reason"] == "cancelled"
    assert cancelled["record"]["detail"] == "cancelled while queued"
    assert queued["job_id"] not in fake.job_ids


def test_cancel_job_stops_a_running_job(tmp_path):
    fake = FakeRun()

    async def scenario(client):
        job_id = payload(await client.call_tool("run_local_subagent", {**ARGS, "wait": False}))[
            "job_id"
        ]
        await anyio.sleep(0.2)
        await client.call_tool("cancel_job", {"job_id": job_id})
        return payload(await client.call_tool("wait_jobs", {"job_ids": [job_id], "wait_s": 5}))

    [state] = serve(fake, tmp_path, scenario)
    assert state["state"] == "done"
    assert state["record"]["reason"] == "cancelled"


@pytest.mark.parametrize("mode", MODES)
def test_a_client_that_gives_up_on_a_blocking_call_cancels_its_job(tmp_path, mode):
    fake = FakeRun()
    manager = JobManager(fake, profile_name="test-profile", state_dir=tmp_path)
    server = build_server(manager, progress_interval_s=0.05)

    async def main():
        async with Client(server, mode=mode) as client:
            with anyio.move_on_after(0.3):
                await client.call_tool("run_local_subagent", ARGS)
            await anyio.sleep(0.3)

    try:
        anyio.run(main)
        [state] = manager.wait(fake.job_ids, 5)
    finally:
        fake.release.set()
        manager.close()
    assert state["state"] == "done"
    assert state["record"]["reason"] == "cancelled"


@pytest.mark.parametrize("mode", MODES)
def test_giving_up_cancels_promptly_at_the_default_interval(tmp_path, mode):
    # without abandon_on_cancel the call would sit in manager.wait for the whole 30 s
    fake = FakeRun()
    manager = JobManager(fake, profile_name="test-profile", state_dir=tmp_path)
    server = build_server(manager, progress_interval_s=30)

    async def main():
        async with Client(server, mode=mode) as client:
            with anyio.move_on_after(0.3):
                await client.call_tool("run_local_subagent", ARGS)
            [state] = await anyio.to_thread.run_sync(manager.wait, fake.job_ids, 2)
            return state

    try:
        state = anyio.run(main)
    finally:
        fake.release.set()
        manager.close()
    assert state["state"] == "done"
    assert state["record"]["reason"] == "cancelled"


def test_bad_arguments_are_an_error_and_queue_nothing(tmp_path):
    fake = FakeRun()

    async def scenario(client):
        short = await client.call_tool("run_local_subagent", {**ARGS, "task": "too short"})
        no_id = await client.call_tool("job_status", {"job_id": "not-a-job"})
        too_many = await client.call_tool("wait_jobs", {"job_ids": ["j-20261015-abcdef"] * 11})
        return short, no_id, too_many

    for result in serve(fake, tmp_path, scenario):
        assert result.is_error is True
        assert payload(result)["error"].startswith("bad arguments: ")
    assert fake.job_ids == []


def test_an_unknown_tool_is_an_error(tmp_path):
    async def scenario(client):
        return await client.call_tool("run_remote_subagent", ARGS)

    result = serve(FakeRun(), tmp_path, scenario)
    assert result.is_error is True
    assert payload(result) == {"error": "unknown tool: run_remote_subagent"}


def test_an_unseen_job_id_is_unknown(tmp_path):
    async def scenario(client):
        return payload(await client.call_tool("job_status", {"job_id": "j-20261015-abcdef"}))

    assert serve(FakeRun(), tmp_path, scenario) == {
        "job_id": "j-20261015-abcdef",
        "state": "unknown",
    }


def test_a_closed_manager_is_an_internal_error(tmp_path):
    fake = FakeRun()
    manager = JobManager(fake, profile_name="test-profile", state_dir=tmp_path)
    server = build_server(manager)
    manager.close()

    async def main():
        async with Client(server, mode="legacy") as client:
            return await client.call_tool("run_local_subagent", ARGS)

    result = anyio.run(main)
    assert result.is_error is True
    assert payload(result) == {"error": "internal: RuntimeError: the job manager is closed"}


def test_the_router_modules_do_not_import_mcp():
    # the jobs command line and the manager start without paying for the SDK's import
    code = "import sys, router.jobs, router.manager; print('mcp' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "False"
