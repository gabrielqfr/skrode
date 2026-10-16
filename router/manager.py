"""The MCP server's job queue: one job at a time, first in first out (one GPU, one model)."""

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from router.jobs import (
    STATE_DIR,
    _error_line,
    append_record,
    build_record,
    load_records,
    new_job_id,
)

JOB_TIMEOUT_S = 45 * 60  # a job's wall-clock limit; the worker stops at its next model call
WAIT_LIMIT_S = 50.0  # each wait ends under Codex's 60 s tool_timeout_sec default

# run(args, *, job_id, should_stop, progress) -> record: run_job with its other arguments bound
RunFn = Callable[..., dict]


@dataclass
class _Job:
    job_id: str
    args: dict
    submitted: float
    state: str = "queued"  # "queued", "running" or "done"
    started: float = 0.0
    progress: str = ""
    cancel: bool = False
    record: dict | None = None


class JobManager:
    def __init__(
        self,
        run: RunFn,
        *,
        profile_name: str,
        state_dir: Path = STATE_DIR,
        job_timeout_s: float = JOB_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run = run
        self.profile_name = profile_name
        self.state_dir = state_dir
        self.job_timeout_s = job_timeout_s
        self._clock = clock
        self._jobs: dict[str, _Job] = {}
        self._queue: deque[str] = deque()
        self._running: str | None = None
        self._closed = False
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._loop, name="job-manager", daemon=True)
        self._thread.start()

    def submit(self, args: dict) -> str:
        job_id = new_job_id()
        with self._cond:
            # checked under the lock: a close() between check and append would strand the job
            if self._closed:
                raise RuntimeError("the job manager is closed")
            self._jobs[job_id] = _Job(job_id=job_id, args=args, submitted=self._clock())
            self._queue.append(job_id)
            self._cond.notify_all()
        return job_id

    def status(self, job_id: str) -> dict:
        job = None
        with self._cond:
            job = self._jobs.get(job_id)
            if job is not None:
                if job.state == "queued":
                    ahead = list(self._queue).index(job.job_id) + (
                        1 if self._running is not None else 0
                    )
                    return {"job_id": job_id, "state": "queued", "ahead": ahead}
                if job.state == "running":
                    return {
                        "job_id": job_id,
                        "state": "running",
                        "progress": job.progress,
                        "elapsed_s": round(self._clock() - job.started, 1),
                    }
                return {"job_id": job_id, "state": "done", "record": job.record}
        for record in reversed(load_records(self.state_dir)):
            if record.get("job_id") == job_id:
                return {"job_id": job_id, "state": "done", "record": record}
        return {"job_id": job_id, "state": "unknown"}

    def wait(self, job_ids: list[str], timeout_s: float) -> list[dict]:
        limit = max(0.0, min(timeout_s, WAIT_LIMIT_S))
        start = time.monotonic()
        with self._cond:
            while True:
                remaining = start + limit - time.monotonic()
                if remaining <= 0:
                    break
                done = True
                for job_id in job_ids:
                    job = self._jobs.get(job_id)
                    if job is not None and job.state != "done":
                        done = False
                        break
                if done:
                    break
                self._cond.wait(remaining)
        return [self.status(job_id) for job_id in job_ids]

    def cancel(self, job_id: str) -> dict:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is not None:
                if job.state == "queued":
                    self._queue.remove(job.job_id)
                    job.record = self._stored_failure(job, "cancelled", "cancelled while queued")
                    job.state = "done"
                    self._cond.notify_all()
                elif job.state == "running":
                    job.cancel = True
        return self.status(job_id)

    def close(self, timeout_s: float = 5.0) -> None:
        with self._cond:
            self._closed = True
            while self._queue:
                job = self._jobs[self._queue.popleft()]
                job.record = self._stored_failure(job, "cancelled", "cancelled while queued")
                job.state = "done"
            if self._running is not None:
                self._jobs[self._running].cancel = True
            self._cond.notify_all()
        self._thread.join(timeout_s)

    def _stored_failure(self, job: _Job, reason: str, detail: str) -> dict:
        record = build_record(
            job_id=job.job_id,
            status="failed",
            reason=reason,
            detail=detail,
            attempts=0,
            turns=0,
            tokens={"prompt": 0, "completion": 0},
            checks=[],
            report=None,
            report_error="",
            turn_limited=False,
            files_changed=[],
            diffstat="",
            branch=None,
            base_commit="",
            model=self.profile_name,
            wall_s=self._clock() - job.submitted,
            logs=self.state_dir / "jobs" / job.job_id,
        )
        with contextlib.suppress(OSError):
            append_record(record, self.state_dir)
        return record

    # the two hooks only touch this manager's state, so they never raise into run_job
    def _should_stop(self, job: _Job) -> str | None:
        if job.cancel:
            return "cancelled"
        if self._clock() - job.started >= self.job_timeout_s:
            return "timeout"
        return None

    def _progress(self, job: _Job, line: str) -> None:
        job.progress = line

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait()
                if not self._queue and self._closed:
                    return
                job_id = self._queue.popleft()
                job = self._jobs[job_id]
                job.state = "running"
                job.started = self._clock()
                job.progress = "starting"
                self._running = job_id
            try:
                result = self._run(
                    job.args,
                    job_id=job.job_id,
                    should_stop=partial(self._should_stop, job),
                    progress=partial(self._progress, job),
                )
            except Exception as error:
                result = self._stored_failure(job, "internal", _error_line(error))
            with self._cond:
                self._jobs[job_id].record = result
                self._jobs[job_id].state = "done"
                self._running = None
                self._cond.notify_all()
