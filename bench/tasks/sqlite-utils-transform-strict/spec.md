# transform(strict=) and transform --strict/--no-strict

## Goal
`Table.transform()` rebuilds a table but always keeps its current STRICT mode: `transform_sql` passes `strict=self.strict` to `create_table_sql`. There is no way to turn a table strict or back. Add a `strict: Optional[bool] = None` parameter to `Table.transform` and `Table.transform_sql` (`None` keeps the current mode, `True` makes the table strict, `False` non-strict), and a `--strict/--no-strict` option on the `sqlite-utils transform` command that passes it through. This is one feature across the Python API and the CLI.

## Files to touch
- `sqlite_utils/db.py`
- `sqlite_utils/cli.py`
- `tests/test_transform.py`
- `tests/test_cli.py`
- `docs/python-api.rst`
- `docs/cli.rst`

## Functions to add or change
- `Table.transform_sql` in `sqlite_utils/db.py`: new last keyword parameter `strict: Optional[bool] = None`, documented in the docstring as `:param strict: Set to ``True`` to make the table strict or ``False`` to make it non-strict. Defaults to ``None``, which preserves the existing strict mode.` At the start of the body, `if strict is True and not self.db.supports_strict: raise TransformError("SQLite does not support STRICT tables")`. Where it builds the new table's SQL, pass `strict=self.strict if strict is None else strict`.
- `Table.transform` in the same file: the same new parameter and docstring line, passed on to `self.transform_sql(..., strict=strict)`. After the transformation succeeds (just before `return self`), when `strict is not None`, store it in `self._defaults["strict"]` so that a later `create(..., replace=True)` on this `Table` object uses the new mode.
- Converting to strict validates every existing row as it is copied; a row that does not fit must raise `sqlite3.IntegrityError` and leave the original table, its mode and its rows as they were, with no leftover temporary table. Check that the existing transaction handling already gives this; change it only if it does not.
- `transform` command in `sqlite_utils/cli.py`: a `@click.option("--strict/--no-strict", default=None, help="Enable or disable STRICT mode (default: preserve current mode)")` placed right above the `--sql` option, a `strict` parameter in the function after `drop_foreign_keys`, and `strict=strict` passed to both `transform_sql` (the `--sql` path) and `transform`.
- `docs/python-api.rst`: before the `.. _python_api_transform_rename_columns:` label, a section `.. _python_api_transform_strict:` titled "Changing strict mode": `table.transform(strict=True)` and `strict=False` examples, that the default `None` preserves the mode, that `strict=True` raises `sqlite_utils.db.TransformError` without STRICT support, and that existing rows are validated as they are copied.
- `docs/cli.rst`: in the transform command's option list, after `--add-foreign-key`, entries for `--strict` (convert to a STRICT table; fails without STRICT support) and `--no-strict` (convert a strict table back).

## Tests to add
Each test skips with `pytest.skip("SQLite version does not support strict tables")` when `supports_strict` is false, except the last in `test_transform.py`.
- `tests/test_transform.py` (uses `fresh_db`):
  - `test_transform_strict`, parametrized `(initial_strict, transform_strict, expected_strict)` over `(False, None, False)`, `(True, None, True)`, `(False, True, True)`, `(True, False, False)`: `fresh_db.table("dogs", strict=initial_strict)`, `dogs.transform(strict=...)`, then `dogs.strict is expected_strict`.
  - `test_transform_to_strict_with_invalid_data`: create `dogs` with `{"id": int}`, insert `{"id": "not-an-integer"}`; `transform(strict=True)` raises `sqlite3.IntegrityError`; afterwards `dogs.strict is False`, the rows are unchanged and `fresh_db.table_names() == ["dogs"]`.
  - `test_transform_strict_updates_default`: `fresh_db.table("items", strict=True)`, create `{"id": int}`, `transform(strict=False)`, then `create({"id": int}, replace=True)`: `strict is False` after both.
  - `test_transform_to_strict_not_supported`, parametrized over `"transform"` and `"transform_sql"`: set `fresh_db._supports_strict = False`; calling the method with `strict=True` raises `TransformError` matching `SQLite does not support STRICT tables`, and the table stays non-strict.
- `tests/test_cli.py` (uses `db_path`, `CliRunner`):
  - `test_transform_strict_option`, parametrized `(initial_strict, args, expected_strict)` over `(False, [], False)`, `(True, [], True)`, `(False, ["--strict"], True)`, `(True, ["--no-strict"], False)`: exit code 0 and the resulting mode.
  - `test_transform_strict_option_sql`: with `--strict` on a non-strict table and `--no-strict` on a strict one, plus `--sql`: exit 0, `") STRICT;"` appears in the output exactly when the target is strict, and the table itself is unchanged.
  - `test_transform_strict_option_with_invalid_data`: the CLI with `--strict` on the bad row exits 1 with `result.exception` a `sqlite3.IntegrityError`, the table unchanged and no table name starting with `dogs_new_`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_transform.py tests/test_cli.py`

## Out of scope
`create_table_sql`, `Table.create`, the `create-table` command's own `--strict`, `docs/cli-reference.rst` (generated by cog), the changelog.

## Open questions
None.
