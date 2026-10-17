# Keep duplicate column names in query results

## Goal
Every place that turns a cursor row into a dictionary builds the keys from `cursor.description` and does `dict(zip(keys, row))`. A query returning the same column name twice (a join of two tables that both have `id`, or `select id, id`) therefore loses values silently: the last duplicate wins. Add one helper, `dedupe_keys`, that renames later duplicates `id`, `id` → `id`, `id_2` (skipping any suffix another column already has), and use it everywhere rows become dictionaries: `Database.query`, `Queryable.rows_where`, `Table.search`, and the CLI's JSON output. CSV and table output keep the original duplicate headers, since they never build dictionaries.

## Files to touch
- `sqlite_utils/utils.py`
- `sqlite_utils/db.py`
- `sqlite_utils/cli.py`
- `tests/test_utils.py`
- `tests/test_query.py`
- `tests/test_rows.py`
- `tests/test_fts.py`
- `tests/test_cli.py`

## Functions to add or change
- `sqlite_utils/utils.py`: add `def dedupe_keys(keys: Iterable[str]) -> List[str]` after `hash_record`. Take the keys into a list; if they are already unique return that list unchanged. Otherwise walk them in order: a key seen before becomes `f"{key}_{n}"` with the smallest `n` from 2 up such that the new name is neither already produced nor anywhere in the original list. Docstring: it renames duplicates so every name is unique by appending `_2`, `_3`... to later occurrences, skipping any suffix that would collide with another column, for use when converting rows to dictionaries; `:param keys:` documented.
- `sqlite_utils/db.py`: import `dedupe_keys` from `.utils`. Replace `keys = [d[0] for d in cursor.description]` with `keys = dedupe_keys(d[0] for d in cursor.description)` at both places in `Database.query`, and `columns = [c[0] for c in cursor.description]` with `columns = dedupe_keys(c[0] for c in cursor.description)` in `Queryable.rows_where` and in `Table.search`.
- `sqlite_utils/cli.py`: import `dedupe_keys` from `.utils` (alphabetical in the existing import list). At the top of `output_rows(iterator, headers, nl, arrays, json_cols)` set `headers = dedupe_keys(headers)`, with a comment saying duplicates would collide as dictionary keys, and that CSV and table output keep the original headers because they never build dictionaries.

## Tests to add
- `tests/test_utils.py`: `test_dedupe_keys`, parametrized `(input, expected)`: `[]`→`[]`; `["id", "name"]` unchanged; `["id", "id"]`→`["id", "id_2"]`; `["id", "id", "id"]`→`["id", "id_2", "id_3"]`; `["id", "id", "id_2"]`→`["id", "id_3", "id_2"]`; `["id_2", "id", "id"]`→`["id_2", "id", "id_3"]`; `["id", "id", "id_2", "id_2"]`→`["id", "id_3", "id_2", "id_2_2"]`.
- `tests/test_query.py`: `test_query_duplicate_column_names_are_deduped`: tables `one` `{"id": 1, "value": "left"}` and `two` `{"id": 2, "value": "right"}`; `select one.id, two.id, one.value, two.value from one, two` gives `[{"id": 1, "id_2": 2, "value": "left", "value_2": "right"}]`. `test_query_deduped_column_avoids_existing_names`: `select 1 as id, 2 as id, 3 as id_2` gives `[{"id": 1, "id_3": 2, "id_2": 3}]`.
- `tests/test_rows.py`: `test_rows_where_duplicate_select_columns_are_deduped`: insert `{"id": 1, "name": "Cleo"}`; `rows_where(select="id, id, name")` gives `[{"id": 1, "id_2": 1, "name": "Cleo"}]`.
- `tests/test_fts.py`: `test_search_duplicate_columns_are_deduped`: with the file's `search_records` and FTS4 on `text` and `country`, `table.search("tanuki", columns=["text", "text"])` gives one row with `text` and `text_2`, both `"tanuki are running tricksters"`.
- `tests/test_cli.py`: `test_query_json_duplicate_columns_are_deduped`: `[db_path, "select 1 as id, 2 as id, 'x' as value, 'y' as value"]` prints `[{"id": 1, "id_2": 2, "value": "x", "value_2": "y"}]`. `test_query_csv_duplicate_columns_are_preserved`: the same with `select 1 as id, 2 as id` and `--csv` prints `id,id` then `1,2`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_utils.py tests/test_query.py tests/test_rows.py tests/test_fts.py tests/test_cli.py`

## Out of scope
CSV, TSV and table output paths, `Table.insert`/`upsert`, docs.

## Open questions
None.
