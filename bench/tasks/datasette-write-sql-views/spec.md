# CREATE VIEW and DROP VIEW in execute-write SQL

## Goal
`/<db>/-/execute-write` rejects `create view ...` and `drop view ...` as an unsupported SQL
operation: `decision_for_write_sql_operation` in `datasette/write_sql.py` only knows schema
operations whose target is a table or an index. Add two permission actions, `create-view` (on the
database) and `drop-view` (on the view, as a table resource), and map the two statements to them,
so an actor holding the right permission can run them and any other actor gets a 403 naming the
missing permission. Dropping a view also makes SQLite's authorizer report a spurious delete on the
view's name, the same thing it already does for a dropped table; that delete must not become a
`delete-row` requirement.

## Files to touch
- `datasette/default_actions.py`
- `datasette/write_sql.py`
- `datasette/utils/sql_analysis.py`
- `docs/authentication.rst`
- `tests/test_queries.py`

## Functions to add or change
- `register_actions` in `datasette/default_actions.py`: two new `Action`s, right after the
  existing `create-table` and `drop-table` entries respectively:
  `Action(name="create-view", abbr="cv", description="Create views", resource_class=DatabaseResource)`
  and
  `Action(name="drop-view", abbr="dv", description="Drop views", resource_class=TableResource)`.
- `decision_for_write_sql_operation` in `datasette/write_sql.py`, before the `alter`/`table`
  case, following the pattern of the cases already there:
  - operation `create`, target type `view`: with no database, `UnsupportedWriteSqlOperation` with
    the function's usual message; otherwise `RequireWriteSqlPermissions` with one
    `PermissionRequirement(action="create-view", resource=DatabaseResource(database=...))`.
  - operation `drop`, target type `view`, database and table both set:
    `RequireWriteSqlPermissions` with one `PermissionRequirement(action="drop-view",
    resource=TableResource(database=..., table=...))`.
- `analyze_sql_tables` in `datasette/utils/sql_analysis.py`: the set of dropped tables whose
  spurious `delete` is suppressed (`dropped_tables`, used by `key_is_drop_table_delete`) also
  includes keys whose operation is `drop` and target type is `view`. Rename it
  `dropped_tables_and_views`.
- `docs/authentication.rst`:
  - in the list of permissions `--root` grants, add ``create-view`` after ``create-table`` and
    ``drop-view`` after ``drop-table``;
  - after the `create-table` action section, a section `.. _actions_create_view:` / `create-view`:
    "Actor is allowed to create a database view." with the same `resource` lines as
    `create-table` (`DatabaseResource(database)`);
  - after the `drop-table` action section, a section `.. _actions_drop_view:` / `drop-view`:
    "Actor is allowed to drop a database view." with resource
    `datasette.resources.TableResource(database, table)`, `table` being "the name of the view".

## Tests to add
In `tests/test_queries.py`:
- `test_execute_write_create_view_uses_create_view_permission` (async): `Datasette(memory=True,
  default_deny=True, config=...)` where actor `creator` has `view-database`, `execute-write-sql`
  and `create-view` on database `data`, and `row-writer` has the first two plus instance-wide
  `insert-row` and `update-row`. A memory database named `data` with a `dogs` table;
  `await ds.invoke_startup()`.
  - `GET /data/-/execute-write/analyze` as `creator` with
    `sql=create view dog_names as select id, name from dogs`: 200, `ok` true, `execute_disabled`
    false, and `analysis_rows` is exactly one row: operation `create`, database `data`, table
    `dog_names`, `required_permission` `create-view`, `source` `None`, `allowed` true.
  - `POST /data/-/execute-write` as `creator` with that SQL: 200, `ok` true, message
    `"Query executed"`, and `db.view_exists("dog_names")`.
  - the same POST as `row-writer` creating `should_not_exist`: 403 with errors
    `["Permission denied: need create-view on data"]`, and the view does not exist.
- `test_execute_write_schema_operations_use_schema_permissions` (existing, parametrized): a
  fifth case, id `drop-view`: database `execute_write_drop_view`, allowed actor `dropper`, allowed
  SQL `drop view dogs_view`, denied SQL `drop view cats_view`, expected error
  `"Permission denied: need drop-view on data/cats_view"`, setup creating `dogs_view` and
  `cats_view` as `select * from` `dogs` and `cats`. The config grants `dropper` `drop-view` on the
  table entry `dogs_view`. Expected state: `dogs_view` no longer exists, `cats_view` still does.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_queries.py`

## Out of scope
Other view statements, `execute-write` UI templates, the changelog, any action other than the two
new ones.

## Open questions
None.
