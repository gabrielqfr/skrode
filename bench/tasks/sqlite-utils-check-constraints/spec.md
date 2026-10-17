# CHECK constraints and column comments survive `.transform()`

## Goal
SQLite has no pragma for CHECK constraints, so `table.transform()` rebuilds a table without
them, and drops every comment in the original `CREATE TABLE`. Add a small SQL lexer and parser
for `CREATE TABLE` statements that finds CHECK constraints and the comments that belong to each
column, expose the constraints as `table.checks`, `table.column_checks` and
`table.table_checks`, and make `.transform()` carry both constraints and column comments into
the rebuilt table, renaming columns inside CHECK expressions and refusing to drop a column that
a remaining constraint still uses.

## Files to touch
- `sqlite_utils/create_table_parser.py` (new)
- `sqlite_utils/db.py`
- `tests/test_create_table_parser.py` (new)
- `tests/test_introspect.py`
- `tests/test_transform.py`

## Functions to add or change
### `sqlite_utils/create_table_parser.py` (standard library only)
- `@dataclass class Check`: `check: str`, `name: str = ""`, `column: str = ""`,
  `options: list[Any] | None = None`, then `sql: str = ""`, `start: int = -1`, `end: int = -1`,
  each of the last three declared with `field(..., compare=False, repr=False)` so equality
  compares the constraint, not its position.
- `@dataclass(frozen=True) class ColumnComments`: `before: str = ""`, `after: str = ""`.
- `class ParseError(ValueError)`.
- A lexer turning SQL into tokens that keep their `start`/`end` offsets, of kinds: whitespace;
  comments (`-- ...` up to and including the newline, or to the end of input; `/* ... */`, where
  an unterminated `/*` is a `ParseError`); strings (`'...'` with `''` as an escaped quote,
  unterminated is a `ParseError`); quoted identifiers (`"..."` with `""`, `` `...` `` with
  ` `` `, `[...]`); words (bare identifiers and keywords: letters, digits, `_`, `$` and any
  character at or above U+0080, so `💩x` and `é` are one word); numbers; punctuation.
  Whitespace and comments are trivia: they never match a keyword, comma or parenthesis.
- `parse_checks(create_sql: str) -> list[Check]`: the statement must start with `CREATE`, then
  optional `TEMP`/`TEMPORARY`, then `TABLE` (anything else, or unbalanced parentheses, is a
  `ParseError`); `CREATE VIRTUAL TABLE` returns `[]`. Split the body between the table's
  parentheses at top-level commas. In order of appearance, return:
  - column constraints: in a column definition (an item not starting with `CONSTRAINT`,
    `PRIMARY`, `UNIQUE`, `CHECK` or `FOREIGN`), each `[CONSTRAINT name] CHECK (expr)` gives a
    `Check` with `column` = the column's name unquoted;
  - table constraints: an item `[CONSTRAINT name] CHECK (expr)` gives a `Check` with
    `column=""`.
  `check` is the source text between the CHECK's parentheses, `.strip()`ped, with comments and
  inner whitespace kept as written. `name` is the unquoted constraint name or `""`. `sql` is the
  source from `CONSTRAINT` (or `CHECK` when unnamed) through the closing parenthesis, and
  `create_sql[start:end] == sql`. `CHECK` not followed by `(` is a `ParseError`.
  `options`: when the expression, after removing parentheses that wrap all of it, is exactly
  `<the owning column> IN (v1, v2, ...)` (column compared unquoted and case-insensitively for
  ASCII) and every `v` is a literal, the list of their Python values: a string (unquoted), an
  integer (decimal or `0x` hex, optional sign), a float, `NULL` as `None`, `TRUE`/`FALSE`.
  Otherwise `None` (`NOT IN`, extra terms, another column, a function call, a `"double-quoted"`
  identifier). Table constraints never get options.
- `parse_column_comments(create_sql: str) -> dict[str, ColumnComments]`: for each column
  definition item (as above; table constraints are skipped), `before` is the item's text before
  its first non-trivia token and `after` the text after its last non-trivia token, both
  `.strip()`ped. Comments inside the definition (between name and type) belong to neither. Key:
  the column name unquoted; only columns with a non-empty `before` or `after` appear. Not a
  `CREATE TABLE` body: `{}`.
- `check_references_identifier(expression: str, identifier: str) -> bool`: True when a token
  of the expression is that identifier (unquoted, ASCII case-insensitive). A token is an
  identifier when it is quoted, or a word that is not an SQLite keyword (embed SQLite's keyword
  list as a frozenset), and it is not followed by `(` or `.` (a function or table name) and not
  preceded by `COLLATE` or `AS`. Strings and comments never match.
- `rewrite_check_expression(expression: str, rename: dict[str, str]) -> str`: replace each
  identifier token (same rule) whose unquoted, ASCII-folded text is a key of `rename`, leaving
  every other character as written. Keep the original quoting style (`"`, `` ` ``, `[]`); a bare
  original becomes bare when the new name is a valid bare identifier and not a keyword, else
  `"double-quoted"` with `"` doubled. An empty `rename` returns the expression unchanged.
- `sql_ends_in_line_comment(sql: str) -> bool`: True when the last token is a `--` comment that
  does not end in a newline (text appended after it would be commented out).

### `sqlite_utils/db.py`
- Import `Check`, `ColumnComments`, `ParseError` and the four functions above from
  `.create_table_parser`, so `from sqlite_utils.db import Check` works.
- `_check_constraint_sql(check) -> str`: `CONSTRAINT <quoted name> ` when named, then
  `CHECK (<check>)`, with a `\n` before the `)` when `sql_ends_in_line_comment(check.check)`.
- `_column_definition_with_comments(definition, comments) -> str`: `before` dedented and
  stripped, indented three spaces, on the line(s) before the definition; `after` dedented and
  stripped, after the definition and one space, followed by `\n` when it ends in a line comment.
- `Database.create_table_sql`: new keyword-only parameters `_checks: Iterable[Check] | None =
  None` and `_column_comments: Mapping[str, ColumnComments] | None = None` (names resolved with
  `resolve_casing`). A check with a `column` not in the table raises
  `AlterError("No such column for CHECK constraint: <column>")`. Column checks go at the end of
  that column's extras; table checks after every column definition and table-level foreign
  key; each column definition is wrapped by `_column_definition_with_comments`.
- `Table.checks` (property): `[]` when the table does not exist or is a virtual table, else
  `parse_checks(self.schema)`. `Table.column_checks`: `dict[str, list[Check]]` grouped by
  `column`, table checks left out. `Table.table_checks`: the checks with no `column`.
- `Table.transform_sql`: read `self.checks` and `parse_column_comments(self.schema)`; a
  `ParseError` becomes `TransformError(f"Could not parse table schema for table {self.name!r}:
  {ex}")`. For each check: skip it when its owning column is dropped; if its expression
  references a dropped column (`check_references_identifier`), raise
  `TransformError(f"Cannot drop column {col!r}: it is used by CHECK constraint {check.name or
  check.check!r}")` before anything is changed; otherwise keep it with its expression rewritten
  by `rename` and its owning column renamed. Comments of dropped columns are dropped, renamed
  columns keep theirs. Pass both to `create_table_sql`.

## Tests to add
- `tests/test_create_table_parser.py`: column and table checks with names, `options` and
  source spans; comments that look like `CHECK (...)` or contain `,` and `(` are ignored;
  `parse_column_comments` on line and block comments, one before a table constraint not
  attached; `options` only for an exact literal `IN` on the owning column (parametrized over the
  `None` cases above); unquoted Unicode column names; `ParseError` for `SELECT`, an unclosed
  parenthesis, an unterminated string and an unterminated `/*`; a virtual table gives `[]`;
  Hypothesis: comments and whitespace between any tokens, and arbitrary string contents, read
  back from `sqlite_master`.
- `tests/test_introspect.py`: `checks`, `column_checks`, `table_checks` and `sql` on a table
  with one named column check and one named table check; `[]` for a missing table and an FTS5
  table.
- `tests/test_transform.py`: checks survive `transform()` and still reject bad rows; a check
  ending in a `--` comment survives a type change; column comments follow their columns
  through rename, type change and reordering; a dropped column's comments and column check
  disappear; renames reach check expressions without touching strings, function names,
  `COLLATE` names or `CAST(... AS type)`; renaming to `select` quotes it; dropping a column a
  table check uses raises `TransformError` and leaves the schema unchanged.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_create_table_parser.py tests/test_introspect.py tests/test_transform.py`

## Out of scope
Docs and changelog, the CLI, foreign-key or index handling, `UNIQUE`/`DEFAULT` constraints,
comments outside column definitions, preserving any other formatting of the original SQL.

## Open questions
None.
