# A /-/tasks introspection endpoint for background tasks

## Goal
Background tasks (`Datasette.add_background_task`, supervised by `BackgroundTaskSupervisor` in `datasette/background_tasks.py`) cannot be inspected from outside. Add `/-/tasks` and `/-/tasks.json`, built like `/-/threads`: a `JsonDataView` gated by the `permissions-debug` permission that lists every task's name, state, function, start time and exception, plus whether the tasks have been launched. At the same time replace `BackgroundTask.plugin`, a best-effort guess at the owning plugin made by `_resolve_plugin_name`, with `BackgroundTask.function`, the callable's dotted path, which needs no guessing. This touches the supervisor module, the app and its routes together.

## Files to touch
- `datasette/background_tasks.py`
- `datasette/app.py`
- `tests/test_tasks_endpoint.py`
- `tests/test_permissions.py`
- `tests/test_success_envelope.py`
- `docs/introspection.rst`
- `docs/internals.rst`

## Functions to add or change
- `datasette/background_tasks.py`:
  - Delete `_resolve_plugin_name` and the `inspect` import if nothing else uses it.
  - Add `def _function_path(func: Callable) -> str`, docstring `"""Describe the callable without guessing which plugin registered it."""`: unwrap `functools.partial` objects (repeatedly) to their `.func`; if the result has no `__qualname__` (a callable instance), use `type(func).__call__` instead; return `f"{func.__module__}.{func.__qualname__}"`. So a bound method gives `module.Class.method`, an instance with `__call__` gives `module.Class.__call__`.
  - `BackgroundTask.__init__(self, name, func)`: drop the `plugin` parameter and attribute; set `self.function = _function_path(func)`.
  - `BackgroundTaskSupervisor.add`: build `BackgroundTask(actual_name, func)` without the plugin lookup.
  - `BackgroundTaskSupervisor`: add a read-only `launched` property returning `self._launched`, with a docstring saying it lets `/-/tasks` tell "no tasks registered" apart from "tasks registered but not launched yet".
- `datasette/app.py`:
  - Next to `_threads`, add `def _tasks(self)` returning `{"tasks": [...], "launched": self._background_tasks.launched}`, one dict per `self._background_tasks.tasks()` with keys `name`, `state`, `function`, `started_at`, and `exception` (`repr(t.exception)` when set, else `None`). No `plugin` key.
  - In the routes, right after the `/-/threads` route, add the same form of route: `JsonDataView.as_view(self, "tasks.json", self._tasks, permission="permissions-debug")` on `r"/-/tasks(\.(?P<format>json))?$"`.
  - In `add_background_task`'s docstring, the handle's attribute list names `.function` instead of `.plugin`.
- `docs/introspection.rst`: a `/-/tasks` section in the style of the `/-/threads` one: what it lists, the `permissions-debug` requirement, and an example JSON response with the keys above.
- `docs/internals.rst`: wherever the `BackgroundTask` handle's attributes are listed, `.function` replaces `.plugin`.

## Tests to add
- `tests/test_tasks_endpoint.py` (new, `pytest.mark.asyncio`; each test makes `Datasette(memory=True)` with `root_enabled = True` and requests as `actor={"id": "root"}`):
  - `test_task_function_path`, parametrized over a module-level `async def example_task(datasette)`, `functools.partial(example_task)`, the bound method `ExampleWorker().run`, and an `ExampleWorker()` instance with `async __call__`, expecting the qualified names `example_task`, `example_task`, `ExampleWorker.run`, `ExampleWorker.__call__`. Register with `name="custom-name"`; `/-/tasks.json` returns 200 and its first task has that name, `function == f"{__name__}.{qualified_name}"` equal to the handle's `.function`, and no `plugin` key; the HTML `/-/tasks` page returns 200 and contains the function path. Shut down with `await ds.invoke_shutdown()`.
  - `test_tasks_requires_permissions_debug`: anonymous `/-/tasks.json` is 403; as root it is 200 with `ok` true and `tasks` and `launched` keys.
  - `test_running_and_crashed_task_states`: a task that waits forever (`long-runner`) and one that raises `RuntimeError("kaboom")` (`crashing_task`); after `start_background_tasks()` and the crash completing, `launched` is true, `long-runner` is `running` with `exception` `None` and `started_at` set, and `crashing_task` is `crashed` with a string `exception` containing `RuntimeError` and `kaboom`.
- `tests/test_permissions.py`: add `"/-/tasks"` after `"/-/threads"` in `test_view_instance`'s path list (each path, and its `.json`, is 403 for a client denied `view-instance`).
- `tests/test_success_envelope.py`: add `"/-/tasks.json"` after `"/-/threads.json"` in `test_success_object_has_ok_true`'s path list.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_permissions.py tests/test_success_envelope.py tests/test_tasks_endpoint.py`

## Out of scope
How tasks are launched, supervised, cancelled or shut down; `JsonDataView`; other introspection endpoints; `docs/changelog.rst`.

## Open questions
None.
