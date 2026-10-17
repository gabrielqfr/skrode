# show_envvar handles a sequence or an empty envvar

## Goal
With `show_envvar=True`, `Option.get_error_hint` appends `f" (env var: '{self.envvar}')"`, so an `envvar` given as a list or tuple prints the sequence's `repr` (`'['A', 'B']'`). And because both it and `Option.get_help_extra` test `envvar is not None`, an empty `envvar` (`[]` or `""`) prints `(env var: '')` in errors and `[env var: ]` in help, and it also blocks the auto-prefix variable that `None` would show. The error hint must quote each variable of a sequence, and an empty `envvar` must behave exactly like `None` in both places.

## Files to touch
- `src/click/core.py`
- `tests/test_options.py`
- `CHANGES.md`

## Functions to add or change
- `Option.get_error_hint(self, ctx)` in `src/click/core.py`: append the hint only when `self.show_envvar and self.envvar` (truthy). Make `envvars` a one-item list for a `str`, else the sequence itself; join `f"'{var}'"` for each with `", "`; append `f" (env var: {var_str})"`. Add a comment saying the quotes are there because a variable name may contain a space or a comma on every platform.
- `Option.get_help_extra(self, ctx)` in the same file, inside `if self.show_envvar:`: use the auto-prefix name (`f"{ctx.auto_envvar_prefix}_{self.name.upper()}"`, under the same three conditions as now) when `envvar` is falsy rather than only when it is `None`, and set `extra["envvars"]` only when `envvar` is truthy. Merge the nested `if` into one condition. Nothing else in the method changes.
- `CHANGES.md`, under `Unreleased`, after the last bullet: two bullets, ending with `{pr}`3884``: one saying an option's error message with `show_envvar=True` now prints every variable of an `envvar` sequence rather than the sequence's `repr`; one saying an empty `envvar` such as `[]` or `""` now shows the same help and error message as no `envvar` (the help screen used to show `[env var: ]`).

## Tests to add
In `tests/test_options.py`, using the file's `runner` fixture:
- After `test_show_envvar_auto_prefix_dash_in_command`: `test_show_envvar_empty`, parametrized over `envvar` in `[None, [], ""]` and over `(auto_envvar_prefix, expected)` in `[(None, "  --units TEXT\n"), ("WEATHER", "  --units TEXT  [env var: WEATHER_UNITS]\n")]`. Build `click.Option(["--units"], envvar=envvar, show_envvar=True)` in `click.Command("cli", params=[option])`, invoke with `["--help"]` and that `auto_envvar_prefix`; no exception and `expected` in the output.
- After `test_missing_envvar`: `test_missing_envvar_sequence`, parametrized `(envvar, expected)`: `"UNITS"` and `["UNITS"]` → `"'--units' (env var: 'UNITS')"`; `["WEATHER_UNITS", "UNITS"]` and the same tuple → `"'--units' (env var: 'WEATHER_UNITS', 'UNITS')"`; `[]` and `""` → `"'--units'"`. A required option (`required=True`, `show_envvar=True`) invoked with no arguments exits 2 with `f"Error: Missing option {expected}."` in the output.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_options.py`

## Out of scope
`Parameter` and `Argument`, how `envvar` values are read from the environment, docs.

## Open questions
None.
