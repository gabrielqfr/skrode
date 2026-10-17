# Compound (multi-column) foreign keys and `ON DELETE`/`ON UPDATE` actions

## Goal
`ForeignKey` is a namedtuple of one `column` and one `other_column`, so a table with
`FOREIGN KEY (a, b) REFERENCES other(x, y)` is read back as two unrelated keys, `transform()`
rebuilds it as two broken ones, and every key loses its `ON DELETE`/`ON UPDATE` actions. Make
`ForeignKey` a dataclass that can hold several columns and both actions, read compound keys and
actions from `PRAGMA foreign_key_list`, and accept, create, keep, rename, drop and index compound
keys everywhere foreign keys are handled today.

## Files to touch
- `sqlite_utils/db.py`
- `tests/test_foreign_keys.py` (new)

## Functions to add or change (all in `sqlite_utils/db.py`)
### `ForeignKey`
Replace the namedtuple with `@dataclass(order=True) class ForeignKey`, fields in this order:
- `table: str`
- `column: Optional[str] = field(compare=False)` (`None` for a compound key)
- `other_table: str`
- `other_column: Optional[str] = field(compare=False)` (`None` for a compound key)
- `columns: Tuple[str, ...] = ()`, `other_columns: Tuple[str, ...] = ()`
- `is_compound: bool = False`
- `on_delete: str = "NO ACTION"`, `on_update: str = "NO ACTION"`

`column`/`other_column` are left out of comparison so sorting a mix of compound (`None`) and
single keys never compares `None` with `str`. `__post_init__`: a non-empty `columns` becomes
`tuple(columns)` (lists normalised); an empty one becomes `(column,)`, or `()` when `column` is
`None`. Same for `other_columns` from `other_column`. So `ForeignKey("books", "author_id",
"authors", "id")` has `columns == ("author_id",)` and still equals the key read back from the
database. It is no longer a tuple: unpacking it or `fk[0]` raises `TypeError`.

Add `_fk_actions_sql(fk) -> str`: `" ON UPDATE <on_update>"` when `on_update` is set and not
`"NO ACTION"`, then `" ON DELETE <on_delete>"` likewise; `""` when neither. ON UPDATE comes
first: a key with both gives `... ON UPDATE RESTRICT ON DELETE CASCADE`.

Type aliases: `ForeignKeyColumns = Union[str, Tuple[str, ...], List[str]]` and
`ForeignKeyTuple = Tuple[str, ForeignKeyColumns, str, ForeignKeyColumns]`; widen
`ForeignKeyIndicator` so its 2-, 3- and 4-tuples take `ForeignKeyColumns` in column positions.

### `Table.foreign_keys`
Group the `PRAGMA foreign_key_list` rows by `id` (in ascending `id` order), order each group's
rows by `seq`. Per group: `columns` = the `from` values, `other_columns` = the `to` values. When
every `to` is `None` (`REFERENCES other` with no columns), use `tuple(other_table.pks)` if it has
as many columns as `columns`. A group of more than one row is compound: `column=None`,
`other_column=None`, `is_compound=True`; otherwise `column`/`other_column` are the single values.
`on_update`/`on_delete` come from the pragma's columns of those names (`"NO ACTION"` when unset).

### `Database.resolve_foreign_keys`
A list of `ForeignKey` objects and a list of column-name strings work as now. For tuples: a
4-tuple must name this table first (existing `ValueError`) and is then treated as its last three
items; anything other than 2 or 3 items is the existing `ValueError`. When the first item is a
list or tuple, the key is compound:
- 3 items: the third must be a list or tuple, else `ValueError("Compound foreign key <tuple>
  should reference a tuple of other columns")`; 2 items: `other_columns` = the other table's
  `pks`;
- different lengths on the two sides: `ValueError("Compound foreign key <tuple> should have the
  same number of columns on both sides")`;
- one column on each side (a one-item list): an ordinary single-column `ForeignKey`;
- otherwise `ForeignKey(name, None, other_table, None, columns=..., other_columns=...,
  is_compound=True)`.
A string first item keeps today's behaviour (3 items: given other column; 2: guessed).

### `Database.create_table_sql`
- `foreign_keys_by_column` holds only non-compound keys. Their inline clause becomes
  `REFERENCES "other"("col")` followed by `_fk_actions_sql(fk)`.
- The existing check that referenced columns exist loops over every name in `other_columns`
  (`AlterError("No such column: <other_table>.<other_column>")`, `rowid` and self-references
  allowed as now).
- After the column definitions (and before a table-level `PRIMARY KEY` clause), each compound
  key adds one more definition line:
  `   FOREIGN KEY ("a", "b") REFERENCES "other"("x", "y")` plus `_fk_actions_sql(fk)`, every name
  through `quote_identifier`. If any of its `columns` is not a column of the new table, raise
  `AlterError("No such column: " + ", ".join(sorted(missing)))`.
- Exact result for `courses` with `pk="course_code"` and one compound key to `departments`:
  ```
  CREATE TABLE "courses" (
     "course_code" TEXT PRIMARY KEY,
     "campus_name" TEXT,
     "dept_code" TEXT,
     FOREIGN KEY ("campus_name", "dept_code") REFERENCES "departments"("campus_name", "dept_code")
  )
  ```

### `Database.add_foreign_keys(foreign_keys)`
Accept `ForeignKey` objects as well as 4-tuples (anything else: the existing `ValueError`). A
4-tuple's column items may be a string or a tuple/list; one column on each side gives a single
key, more gives a compound one. A `ForeignKey` passed in is used as is, keeping its actions.
Check every name in `columns` and `other_columns` with the existing `AlterError` messages; skip a
key when the table already has one with equal `columns`, `other_table` and `other_columns`; group
the `ForeignKey` objects by `table` and pass them to `transform(add_foreign_keys=...)`.

### `Database.index_foreign_keys`
Existing indexes are the set of `tuple(index.columns)`. For each key whose `columns` is not in
it, `create_index(fk.columns, find_unique_name=True)` and add it to the set: a compound key gets
one composite index, never one index per member column.

### `Table.transform` / `Table.transform_sql`
`drop_foreign_keys` items may be a column name (drops every key that column belongs to) or a
tuple/list of names (drops the key whose `columns` equal it exactly); update both docstrings.
When copying the current keys: drop a key that matches `drop_foreign_keys` or has any column in
`drop`; rename its `columns` through `rename` (its `other_columns` never change); keep
`on_delete`, `on_update` and `is_compound`. Keys from `add_foreign_keys` (after
`resolve_foreign_keys`) get the same renaming. Single keys stay single (`column` set), compound
stay compound (`column=None`).

### `Table.add_foreign_key`
Signature: `(column, other_table=None, other_column=None, ignore=False, on_delete="NO ACTION",
on_update="NO ACTION")`.
`column` and `other_column` may each be a string or a tuple/list.
- Every column must exist (`AlterError("No such column: <col>")`).
- No `other_table`: guessed from the column as now; for more than one column,
  `ValueError("other_table must be specified for a compound foreign key")`.
- No `other_column`: the other table's `pks` for a compound key, else guessed as now.
- Different lengths: `ValueError("Compound foreign key must have the same number of columns on
  both sides")`.
- Every other column must exist (or be `rowid`): `AlterError("No such column:
  <other_table>.<other_col>")`.
- An equal key already there: return `self` when `ignore`, else `AlterError("Foreign key already
  exists for <columns joined by ', '> => <other_table>.<other columns joined by ', '>")`.
- Build one `ForeignKey` with the actions (compound when more than one column) and pass it to
  `self.db.add_foreign_keys([fk])`. Return `self`.

## Tests to add
`tests/test_foreign_keys.py`, with a fixture of `departments` (primary key
`(campus_name, dept_code)`) and `courses` (`FOREIGN KEY (campus_name, dept_code) REFERENCES
departments(campus_name, dept_code)`):
- introspection: one compound key with `columns`, `other_columns`, `column is None`; a single key
  gets one-item `columns`; unpacking or indexing a key raises `TypeError`; sorting single keys,
  and a mix of compound and single;
- `create_table` with a compound key given as a `ForeignKey`, a 3-tuple, a 2-tuple (guessed
  primary key) and lists: the exact schema above; the key is enforced with `PRAGMA foreign_keys
  = ON`; a missing other column raises `AlterError`;
- `transform`: keeps the key on an unrelated rename; renames a member column; dropping a member
  column drops the key and its `FOREIGN KEY` clause; `drop_foreign_keys` by column name and by
  tuple, columns kept;
- `add_foreign_key` with tuples (returns the table), lists with guessed other columns, existing
  key (`"already exists"`, and `ignore=True`), missing column; `add_foreign_keys` with a compound
  4-tuple; `index_foreign_keys` makes one composite index;
- actions: read from the schema; default `"NO ACTION"`; `create_table` with
  `on_delete="CASCADE"`; `transform` keeps `ON DELETE CASCADE` on single and compound keys;
  `add_foreign_keys` with `ForeignKey` objects keeps actions; `add_foreign_key(...,
  on_delete="CASCADE", on_update="RESTRICT")` gives `ON UPDATE RESTRICT ON DELETE CASCADE` and
  the cascade deletes the row; a compound key with `on_delete="SET NULL"`;
- `REFERENCES authors` and `REFERENCES departments` with no columns resolve to the primary key;
  a `ForeignKey` built with lists has tuple `columns` and `other_columns`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_foreign_keys.py tests/test_create.py tests/test_transform.py tests/test_m2m.py tests/test_extracts.py tests/test_cli.py`

The existing test files must pass unchanged: they build ForeignKey with four positional
arguments and compare it with keys read back.

## Out of scope
Docs, changelog and upgrade notes, the CLI, `MATCH` clauses, deferrable constraints, compound
keys in `extract()`, `m2m()` or `lookup()`.

## Open questions
None.
