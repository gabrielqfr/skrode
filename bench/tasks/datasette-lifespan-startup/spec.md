# Run Datasette startup through the ASGI lifespan

## Goal
Datasette's startup (the table-count precompute for immutable databases, then
`invoke_startup()` and its `startup` plugin hooks) runs lazily on the first HTTP request, through
`AsgiRunOnFirstRequest` in `Datasette.app()`. The app already wraps itself in `AsgiLifespan`, but
only for shutdown. Run startup from `lifespan.startup` instead, so a server that sends lifespan
events finishes startup before it accepts a request, and keep the first-request path only as a
fallback for hosts that never send them (`httpx.ASGITransport`, which `ds.client` uses). Both
paths, and `datasette serve`, call one idempotent, lock-guarded method, so startup runs once
however many callers race for it. A startup exception during lifespan becomes a
`lifespan.startup.failed` message instead of escaping. Three files and one behaviour; read
`Datasette.app()`, `Datasette.invoke_startup()`, `AsgiLifespan` and `AsgiRunOnFirstRequest`
before writing.

## Files to touch
- `datasette/app.py`
- `datasette/utils/asgi.py`
- `datasette/cli.py`
- `tests/test_lifespan.py` (new)

## Functions to add or change
- `Datasette.__init__` in `datasette/app.py`: `self._setup_db_done = False`, and
  `self._startup_lock = asyncio.Lock()` created next to `self._refresh_schemas_lock`, in both the
  normal path and the `RuntimeError` workaround branch.
- New `async def _startup_sequence(self)` on `Datasette`, with a docstring saying it is the single
  idempotent entry point used by `AsgiLifespan`, `AsgiRunOnFirstRequest` and `datasette serve`:
  - return at once when `self._startup_invoked and self._setup_db_done`;
  - otherwise, under `self._startup_lock`, check that again; if `_setup_db_done` is false, call
    `await database.table_counts(limit=60 * 60 * 1000)` for every database that is not mutable
    and set `_setup_db_done = True`; then `await self.invoke_startup()` (which is itself a no-op
    once `_startup_invoked` is set).
  It checks both flags because `serve` or a caller may run a bare `invoke_startup()` first, which
  sets only `_startup_invoked`; the precompute must still run once afterwards.
- `Datasette.app()`: remove the local `setup_db` function (its body moved into
  `_startup_sequence`). Wrap with
  `AsgiLifespan(asgi, on_startup=[self._startup_sequence], on_shutdown=[_close_on_shutdown])`,
  then `AsgiRunOnFirstRequest(asgi, on_startup=[self._startup_sequence])`, in the current order.
- `AsgiLifespan.__call__` in `datasette/utils/asgi.py`: run the startup functions inside
  `try/except Exception`; on an exception send
  `{"type": "lifespan.startup.failed", "message": str(e)}` and return. Same for shutdown with
  `lifespan.shutdown.failed`. Success messages are unchanged.
- `AsgiRunOnFirstRequest` in the same file (`import asyncio` at the top): `__init__` creates
  `self._lock = asyncio.Lock()`. `__call__` does nothing for a `scope["type"] == "lifespan"`
  scope but pass it on (it wraps `AsgiLifespan`, so running hooks there would let a startup
  exception escape before `AsgiLifespan` reports it). For any other scope while not started: take
  the lock, re-check, run the hooks, and only then set `_started = True`, so concurrent first
  requests all wait for startup to finish.
- `serve` in `datasette/cli.py`: the call `await ds.invoke_startup()` (inside the
  `try/except StartupError`) becomes `await ds._startup_sequence()`; update its comment to say it
  is the same entry point the ASGI wrappers use.

## Tests to add
`tests/test_lifespan.py` (new; `pytest.mark.asyncio`, `httpx`, `hookimpl`, `pm`). A helper
`_drive_lifespan_startup(app)` sends one `lifespan.startup` message to `app` with scope
`{"type": "lifespan"}` and returns the messages sent back, cancelling the app once a startup
reply arrives (it never sends shutdown).
- `test_lifespan_startup_runs_before_any_request`: `Datasette(memory=True)`; after driving
  startup, `{"type": "lifespan.startup.complete"}` was sent, `_startup_invoked` is true, and
  `catalog_databases` in the internal database has at least one row, with no HTTP request made.
- `test_lifespan_startup_failure_reports_lifespan_startup_failed`: a registered plugin whose
  `startup` hook raises `RuntimeError("boom from startup hook")`: the messages are exactly
  `[{"type": "lifespan.startup.failed", "message": "boom from startup hook"}]` and
  `_startup_invoked` stays false. Unregister the plugin in `finally`.
- `test_startup_runs_exactly_once_across_lifespan_and_first_request`: a counting `startup` hook;
  one `app = ds.app()` used for the lifespan drive and then for two `GET /-/versions.json`
  through `httpx.ASGITransport(app=app)`: both 200, the hook ran once.
- `test_no_lifespan_first_request_still_triggers_startup` and
  `test_datasette_client_first_request_triggers_startup`: one request through
  `httpx.ASGITransport` (and through `ds.client`) returns 200 and sets `_startup_invoked`.
- `test_concurrent_first_requests_all_wait_for_slow_startup`: a `startup` hook that counts and
  sleeps 0.2 s; ten concurrent `GET /-/versions.json` through `httpx.ASGITransport`: all 200,
  the hook ran once.
- `test_setup_db_still_runs_when_invoke_startup_ran_first`: an immutable database file in
  `tmp_path`, `Database.table_counts` monkeypatched to count calls; `await ds.invoke_startup()`
  makes no call; then `await ds._startup_sequence()` makes exactly one and sets
  `_setup_db_done`; a second `_startup_sequence()` makes none.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_lifespan.py tests/test_plugins.py`

## Out of scope
A `shutdown` plugin hook, `datasette/plugins.py`, `hookspecs.py`, docs, `invoke_startup()`'s own
body, `DatasetteClient`.

## Open questions
None.
