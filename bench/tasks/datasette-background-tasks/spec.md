# Supervised background tasks: `datasette.add_background_task()`

## Goal
Plugins that need long-lived work (a polling loop, a queue consumer, a scheduler) call
`asyncio.create_task()` themselves: the task can be garbage collected mid-flight, a crash becomes
an unseen "Task exception was never retrieved" warning, nothing cancels it, and it may start
before another plugin's `startup` hook has registered what it needs. Add a supervisor that owns
background tasks: plugins register work with `datasette.add_background_task(func, name=None)`,
usually from a `startup` hook; core launches every registered task only after all `startup`
hooks have finished, keeps a reference to each, logs crashes with their traceback and can cancel
them all. `datasette serve --get` must never launch them.

## Files to touch
- `datasette/background_tasks.py` (new)
- `datasette/app.py`
- `datasette/cli.py`
- `datasette/__init__.py`
- `tests/test_background_tasks.py` (new)
- `tests/test_cli_serve_get.py`

## Functions to add or change
### `datasette/background_tasks.py` (standard library only)
- `logger = logging.getLogger("datasette.background_tasks")`.
- `class BackgroundTask`, a handle: `__init__(self, name, func, plugin=None)` sets `name`,
  `state = "registered"`, `task = None` (the `asyncio.Task` once launched), `exception = None`,
  `started_at = None` (UTC ISO 8601 string once launched), `plugin`, and private `_func` and
  `_supervisor = None`. States: `registered` -> `running` -> one of `completed` (returned),
  `crashed` (raised anything but `CancelledError`) or `cancelled`.
  - `cancel()`: when launched, `self.task.cancel()` (the state turns `cancelled` later, in the
    done callback); when not launched, remove the handle from its supervisor so it never runs.
  - `__repr__`: `<BackgroundTask name='x' state='running'>`.
- `class BackgroundTaskSupervisor`, one per `Datasette`: `__init__(self, datasette)` keeps the
  instance, a list of handles in registration order, the set of names in use, a `_launched`
  flag (False) and an `asyncio.Lock`.
  - `add(func, name=None) -> BackgroundTask`: base name = `name`, else `func.__qualname__`,
    else `repr(func)`. If taken, append `-2`, `-3`, ... (the first free one), so three adds named
    `dup` give `dup`, `dup-2`, `dup-3`. Set `plugin` to the pluggy name of the registered
    plugin (from `datasette.plugins.pm`) whose module is `func`'s module or a parent package of
    it, else `None`; any exception there gives `None`. Record the handle and its name; if
    already launched, launch it now (so it returns in state `running` with `task` set).
  - `launch_all()` (async): idempotent and safe under concurrent calls. Return at once if
    launched; else take the lock, check again, set `_launched = True` and launch every handle
    whose `task` is `None`, in registration order.
  - Launching one handle: `state = "running"`, `started_at` = now in UTC as ISO 8601,
    `task = asyncio.create_task(handle._func(datasette), name=handle.name)`, and a done callback
    that sets the state: `cancelled` if the task was cancelled; `crashed` with `exception` set
    and `logger.error("Background task %r crashed", name, exc_info=exc)` (so the log has the
    name, message and traceback) if it raised; else `completed`. A crash never touches other
    tasks.
  - `cancel_all(grace: float = 5.0)` (async): cancel every launched task not yet done, return if
    there were none, else `asyncio.wait(pending, timeout=grace)`; when some are still running,
    `logger.warning` one line giving their count, the grace period and their names sorted and
    joined by `", "`. It never waits past `grace`.
  - `tasks() -> list[BackgroundTask]`: a copy of the handle list, launched or not, in
    registration order.

### `datasette/app.py`
- Import both classes from `.background_tasks`.
- `Datasette.__init__`: `self._suppress_background_tasks = False` and, after the asyncio locks
  are created, `self._background_tasks = BackgroundTaskSupervisor(self)`.
- `add_background_task(self, func, name=None) -> BackgroundTask`: delegate to the supervisor's
  `add`. Works any time after `__init__`; `func` is a coroutine function called as `func(self)`.
  Docstring says so.
- `async start_background_tasks(self)`: `await self.invoke_startup()`, then
  `await self._background_tasks.launch_all()`. The public entry point for tests, embedders and
  headless CLIs with no server and no request.
- `async _launch_background_tasks(self)`: return when `_suppress_background_tasks`, else
  `await self._background_tasks.launch_all()`.
- `app()`: both `AsgiLifespan` and `AsgiRunOnFirstRequest` get
  `on_startup=[self._startup_sequence, self._launch_background_tasks]`, in that order, so launch
  always follows every `startup` hook, whether the server sends a lifespan startup or the first
  request triggers it. Ten concurrent first requests launch each task once.

### `datasette/cli.py`
In `serve`, on the `--get` path, after the startup checks and before `TestClient(ds)` is built,
set `ds._suppress_background_tasks = True`, with a comment: the TestClient request goes through
the whole ASGI stack, first-request fallback included, which would otherwise launch them.
`startup` hooks still run and register tasks; only the launch is skipped.

### `datasette/__init__.py`
`from datasette.background_tasks import BackgroundTask, BackgroundTaskSupervisor  # noqa`,
after the `Event` import.

## Tests to add
- `tests/test_background_tasks.py` (use `@pytest.mark.asyncio`; register test plugins on
  `datasette.plugins.pm` with a `name=` and unregister them in `finally`):
  - two tasks added from one plugin's `startup` hook: after a lifespan startup (drive the ASGI
    app with one `lifespan.startup` message, never send shutdown, cancel the app task after
    `lifespan.startup.complete`), `tasks()` names them in order, both are `running` and have run
    their first line; clean up with `cancel_all(grace=1.0)`;
  - plugin A (`tryfirst=True`) and plugin B each add a task from `startup`; after
    `start_background_tasks()`, A's task saw B's task already registered;
  - ten concurrent `GET /-/versions.json` through `httpx2.ASGITransport`: one handle, run once;
  - added after launch: `running` at once, with `task` set; `cancel()` then awaiting `task`
    raises `CancelledError` and the state becomes `cancelled`;
  - added before launch: `registered`, `task is None`; `cancel()` removes it from `tasks()`;
  - a task raising `RuntimeError("kaboom")` ends `crashed` with that exception, a second task
    still `completed`, and the `datasette.background_tasks` log at ERROR holds the name,
    `kaboom`, `Traceback` and `RuntimeError`;
  - name collisions get `-2`, `-3`; an explicit name is kept; no name gives `__qualname__`;
  - `Datasette([])`: `_startup_invoked` is False, the task is `registered`; after
    `start_background_tasks()` it ran, `_startup_invoked` is True and the state is `completed`
    (yield once with `await asyncio.sleep(0)` before reading the state);
  - `cancel_all(grace=1.0)` cancels a running task and leaves a completed one `completed`;
  - a task that swallows `CancelledError` and keeps sleeping is named in the WARNING log after
    `cancel_all(grace=0.1)`.
- `tests/test_cli_serve_get.py`: a plugin in a `--plugins-dir` adds, from `startup`, a task
  that writes a sentinel file; `serve --memory --plugins-dir ... --get
  /_memory/-/query.json?sql=select+1` exits 0 and the file does not exist; unregister the plugin.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_background_tasks.py tests/test_cli_serve_get.py tests/test_lifespan.py tests/test_plugins.py`

## Out of scope
Docs, a `/-/tasks` endpoint, cancelling tasks at shutdown (`cancel_all` is not wired to
`lifespan.shutdown`), pruning finished handles, restarting crashed tasks.

## Open questions
None.
