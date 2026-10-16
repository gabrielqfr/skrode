"""Tests for skrode.manager.JobManager: the queue, with a fake run_job and no model."""

import json
import queue
import threading
import time
from pathlib import Path

import pytest

from skrode.jobs import append_record, load_records
from skrode.manager import JobManager

EXAMPLE = Path(__file__).resolve().parents[1] / "schemas" / "examples" / "record-passed.json"


class FakeRun:
    """Stands in for run_job: a job runs until the test finishes it or it is told to stop."""

    def __init__(self) -> None:
        self.started: queue.Queue[str] = queue.Queue()
        self._go: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _event(self, job_id: str) -> threading.Event:
        with self._lock:
            return self._go.setdefault(job_id, threading.Event())

    def finish(self, job_id: str) -> None:
        self._event(job_id).set()

    def __call__(self, args: dict, *, job_id: str, should_stop, progress) -> dict:
        progress(f"working on {args['task']}")
        self.started.put(job_id)
        while not self._event(job_id).wait(0.01):
            reason = should_stop()
            if reason is not None:
                return {"job_id": job_id, "status": "failed", "reason": reason}
        if args.get("boom"):
            raise RuntimeError("boom")
        return {"job_id": job_id, "status": "passed"}


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake() -> FakeRun:
    return FakeRun()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def manager(fake, clock, tmp_path):
    manager = JobManager(
        fake, profile_name="test-profile", state_dir=tmp_path, job_timeout_s=60, clock=clock
    )
    yield manager
    manager.close()


def test_job_runs_to_done(manager, fake):
    job = manager.submit({"task": "a"})
    assert job.startswith("j-")
    assert fake.started.get(timeout=5) == job
    assert manager.status(job) == {
        "job_id": job,
        "state": "running",
        "progress": "working on a",
        "elapsed_s": 0.0,
    }
    fake.finish(job)
    assert manager.wait([job], 5) == [
        {"job_id": job, "state": "done", "record": {"job_id": job, "status": "passed"}}
    ]


def test_one_job_at_a_time_in_order(manager, fake):
    first = manager.submit({"task": "a"})
    second = manager.submit({"task": "b"})
    third = manager.submit({"task": "c"})
    assert fake.started.get(timeout=5) == first
    assert manager.status(second) == {"job_id": second, "state": "queued", "ahead": 1}
    assert manager.status(third) == {"job_id": third, "state": "queued", "ahead": 2}
    fake.finish(first)
    assert fake.started.get(timeout=5) == second
    assert manager.status(first)["state"] == "done"
    assert manager.status(third) == {"job_id": third, "state": "queued", "ahead": 1}
    fake.finish(second)
    fake.finish(third)
    states = manager.wait([third, first, second], 5)
    assert [s["job_id"] for s in states] == [third, first, second]
    assert [s["state"] for s in states] == ["done", "done", "done"]


def test_cancel_queued_job_ends_at_once(manager, fake, tmp_path):
    first = manager.submit({"task": "a"})
    second = manager.submit({"task": "b"})
    assert fake.started.get(timeout=5) == first
    status = manager.cancel(second)
    assert status["job_id"] == second
    assert status["state"] == "done"
    record = status["record"]
    assert record["status"] == "failed"
    assert record["reason"] == "cancelled"
    assert record["detail"] == "cancelled while queued"
    assert record["attempts"] == 0
    assert record["turns"] == 0
    assert record["branch"] is None
    assert record["base_commit"] == ""
    assert record["model"] == "test-profile"
    assert record["wall_s"] == 0.0
    assert record["logs"] == str(tmp_path / "jobs" / second)
    assert load_records(tmp_path) == [record]
    fake.finish(first)
    manager.wait([first], 5)
    assert fake.started.empty()


def test_cancel_running_job_stops_it(manager, fake):
    job = manager.submit({"task": "a"})
    assert fake.started.get(timeout=5) == job
    assert manager.cancel(job)["state"] == "running"
    assert manager.wait([job], 5) == [
        {
            "job_id": job,
            "state": "done",
            "record": {"job_id": job, "status": "failed", "reason": "cancelled"},
        }
    ]


def test_cancel_done_job_changes_nothing(manager, fake):
    job = manager.submit({"task": "a"})
    fake.finish(job)
    done = manager.wait([job], 5)[0]
    assert done["state"] == "done"
    assert manager.cancel(job) == done


def test_job_past_its_limit_times_out(manager, fake, clock):
    job = manager.submit({"task": "a"})
    assert fake.started.get(timeout=5) == job
    clock.now = 159.0
    assert manager.status(job)["elapsed_s"] == 59.0
    assert manager.wait([job], 0.1)[0]["state"] == "running"
    clock.now = 160.0
    assert manager.wait([job], 5)[0]["record"]["reason"] == "timeout"


def test_wait_returns_after_its_timeout(manager, fake):
    job = manager.submit({"task": "a"})
    assert fake.started.get(timeout=5) == job
    start = time.monotonic()
    states = manager.wait([job], 0.2)
    waited = time.monotonic() - start
    assert states[0]["state"] == "running"
    assert 0.15 <= waited < 3
    start = time.monotonic()
    assert manager.wait([job], -1)[0]["state"] == "running"
    assert time.monotonic() - start < 1


def test_unknown_job(manager):
    assert manager.status("j-20261015-000000") == {
        "job_id": "j-20261015-000000",
        "state": "unknown",
    }
    assert manager.cancel("j-20261015-000000")["state"] == "unknown"
    start = time.monotonic()
    assert manager.wait(["j-20261015-000000"], 5) == [
        {"job_id": "j-20261015-000000", "state": "unknown"}
    ]
    assert time.monotonic() - start < 1


def test_job_from_an_earlier_server_is_read_from_the_store(manager, tmp_path):
    record = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    newer = dict(record, summary="the newer record")
    append_record(record, tmp_path)
    append_record(newer, tmp_path)
    assert manager.status(record["job_id"]) == {
        "job_id": record["job_id"],
        "state": "done",
        "record": newer,
    }


def test_runner_error_is_stored_as_internal(manager, fake, tmp_path):
    job = manager.submit({"task": "a", "boom": True})
    fake.finish(job)
    record = manager.wait([job], 5)[0]["record"]
    assert record["status"] == "failed"
    assert record["reason"] == "internal"
    assert record["detail"] == "RuntimeError: boom"
    assert load_records(tmp_path) == [record]
    after = manager.submit({"task": "b"})
    assert fake.started.get(timeout=5) == job
    assert fake.started.get(timeout=5) == after


def test_close_cancels_queued_and_running_jobs(fake, clock, tmp_path):
    manager = JobManager(fake, profile_name="test-profile", state_dir=tmp_path, clock=clock)
    first = manager.submit({"task": "a"})
    second = manager.submit({"task": "b"})
    assert fake.started.get(timeout=5) == first
    manager.close()
    assert manager.status(first)["record"] == {
        "job_id": first,
        "status": "failed",
        "reason": "cancelled",
    }
    assert manager.status(second)["record"]["detail"] == "cancelled while queued"
    with pytest.raises(RuntimeError, match="the job manager is closed"):
        manager.submit({"task": "c"})


def test_wait_never_blocks_past_its_limit(manager, fake, monkeypatch):
    monkeypatch.setattr("skrode.manager.WAIT_LIMIT_S", 0.2)
    job = manager.submit({"task": "a"})
    assert fake.started.get(timeout=5) == job
    start = time.monotonic()
    assert manager.wait([job], 5)[0]["state"] == "running"
    assert time.monotonic() - start < 1


def test_cancel_wakes_a_waiter(manager, fake):
    first = manager.submit({"task": "a"})
    second = manager.submit({"task": "b"})
    assert fake.started.get(timeout=5) == first
    results: list[list[dict]] = []
    waiter = threading.Thread(target=lambda: results.append(manager.wait([second], 5)))
    waiter.start()
    time.sleep(0.1)
    start = time.monotonic()
    manager.cancel(second)
    waiter.join(5)
    assert time.monotonic() - start < 1
    assert results[0][0]["record"]["detail"] == "cancelled while queued"


def test_submit_racing_close_is_refused(manager, monkeypatch):
    def close_then_id() -> str:
        manager.close()
        return "j-20261015-abcdef"

    monkeypatch.setattr("skrode.manager.new_job_id", close_then_id)
    with pytest.raises(RuntimeError, match="the job manager is closed"):
        manager.submit({"task": "a"})
    assert manager.status("j-20261015-abcdef") == {
        "job_id": "j-20261015-abcdef",
        "state": "unknown",
    }


def test_two_slots_run_two_jobs_at_once_in_order(fake, clock, tmp_path):
    manager = JobManager(
        fake, profile_name="test-profile", state_dir=tmp_path, clock=clock, concurrency=2
    )
    try:
        first = manager.submit({"task": "a"})
        second = manager.submit({"task": "b"})
        third = manager.submit({"task": "c"})
        started = {fake.started.get(timeout=5), fake.started.get(timeout=5)}
        assert started == {first, second}
        assert manager.status(first)["state"] == "running"
        assert manager.status(second)["state"] == "running"
        assert manager.status(third) == {"job_id": third, "state": "queued", "ahead": 2}
        fake.finish(second)
        assert fake.started.get(timeout=5) == third
        assert manager.status(first)["state"] == "running"
        assert manager.status(second)["state"] == "done"
        fake.finish(first)
        fake.finish(third)
        states = manager.wait([first, second, third], 5)
        assert [s["state"] for s in states] == ["done", "done", "done"]
    finally:
        manager.close()


def test_the_default_is_one_job_at_a_time(fake, tmp_path):
    manager = JobManager(fake, profile_name="test-profile", state_dir=tmp_path)
    assert manager.concurrency == 1
    manager.close()


@pytest.mark.parametrize("concurrency", [0, -1])
def test_concurrency_below_one_is_refused(fake, tmp_path, concurrency):
    with pytest.raises(ValueError, match=f"concurrency must be at least 1, not {concurrency}"):
        JobManager(
            fake, profile_name="test-profile", state_dir=tmp_path, concurrency=concurrency
        )


def test_close_cancels_every_running_job(fake, clock, tmp_path):
    manager = JobManager(
        fake, profile_name="test-profile", state_dir=tmp_path, clock=clock, concurrency=2
    )
    first = manager.submit({"task": "a"})
    second = manager.submit({"task": "b"})
    third = manager.submit({"task": "c"})
    started = {fake.started.get(timeout=5), fake.started.get(timeout=5)}
    assert started == {first, second}
    manager.close()
    assert manager.status(first)["record"]["reason"] == "cancelled"
    assert manager.status(second)["record"]["reason"] == "cancelled"
    assert manager.status(third)["record"]["detail"] == "cancelled while queued"
    assert fake.started.empty()
