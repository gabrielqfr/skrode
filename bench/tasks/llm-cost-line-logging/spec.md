# The generate call's cost line reaches the console and the log file again

## Goal
`sensei_top.llm.client` logs one INFO line per generate call (model, tokens, cost), and users are promised they see it. But `logs.install` sets the console handler to ERROR (WARNING with `--verbose`) and the file handler to WARNING (DEBUG with `--verbose`), so that INFO line reaches the file only under `--verbose` and the console never. Make both handlers pass INFO records from `sensei_top.llm` and its submodules, while every other logger keeps today's floors.

## Files to touch
- `src/sensei_top/logs.py`
- `tests/test_logs.py`

## Functions to add or change
In `src/sensei_top/logs.py`:
- New `class _Level(logging.Filter)`, next to `_Session`: `__init__(self, floor: int)` stores `self.floor`; `filter(record)` returns True when `record.levelno >= self.floor`, otherwise True only when `record.levelno >= logging.INFO` and the record's name is exactly `sensei_top.llm` or starts with `sensei_top.llm.` (so `sensei_top.llmx` does not pass).
- `install(db_path, command, *, verbose)`:
  - console handler: `setLevel(logging.INFO)` and `addFilter(_Level(logging.WARNING if verbose else logging.ERROR))` instead of today's level;
  - file handler: `setLevel(logging.DEBUG if verbose else logging.INFO)`; after the existing `file_handler.handle(_header(command, db))` line, when not verbose, `file_handler.addFilter(_Level(logging.WARNING))`. The filter goes on after the header, or the header (an INFO record from another logger) would be dropped.
  - Keep `root.setLevel(min(handler.level for handler in _installed))` as it is.
- Update the module docstring's "Levels" bullet to say both handlers also take `sensei_top.llm`'s INFO lines.

## Tests to add
In `tests/test_logs.py`, using the file's existing `run(tmp_path, *argv)` and `lines(path)` helpers:
- a context manager `bare_root()` that removes pytest's handlers from the root logger for the block and puts them back after, so `install` adds its console handler;
- `test_the_generate_calls_info_line_reaches_the_console_and_the_file`, parametrized on `verbose` False/True: inside `bare_root()`, `run(tmp_path, ["--verbose"]?, "config")`, clear capsys, log `"the cost line"` at INFO on `sensei_top.llm.client`; stderr lines equal `["INFO the cost line"]` and the file's messages after the header equal `["the cost line"]`;
- `test_every_other_loggers_info_line_stays_out`, parametrized on `verbose` and on name `sensei_top.probe`, `httpx`, `sensei_top.llmx`: an INFO line on that logger gives empty stderr; the file holds it only under `--verbose`.

## Commands that must pass
- `uv run pytest -q tests/test_logs.py`
- `uv run ruff check .`

## Out of scope
`llm/client.py` and what it logs, the file format, rotation, docs.

## Open questions
None.
