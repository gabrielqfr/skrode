# Substitute inside override values

## Goal
An override (`--override`/`-x`, or `TOX_OVERRIDE`) reaches tox as a raw command line string, and
`Loader.load` in `src/tox/config/loader/api.py` converts it without the substitution pass every
value read from a configuration file goes through. `{posargs}`, `{posargs:default}`,
`{env:VAR}` and `{env_name}` inside an override therefore arrive as literal text:
`tox -x 'env_run_base.commands=pytest {posargs}' -- tests src` runs pytest on a directory named
`{posargs}`. Run each override value through the substitution pass of the loader it overrides
(ini or TOML) before converting it. The conversion itself is unchanged: an override is still read
with the ini string rules whatever the file format. Two exceptions keep their raw value:
`set_env` (of type `SetEnv`), which expands late for file values too, and a load with no `conf`
(the CLI's own config file, which never substitutes).

## Files to touch
- `src/tox/config/loader/api.py`
- `src/tox/config/loader/ini/__init__.py`
- `src/tox/config/loader/toml/__init__.py`
- `docs/changelog/4047.bugfix.rst` (new)
- `docs/reference/config.rst`
- `tests/config/loader/test_loader.py`

## Functions to add or change
- `Loader` in `src/tox/config/loader/api.py`: a new method
  `substitute(self, value: str, conf: Config, args: ConfigLoadArgs) -> str` after `build`, with a
  docstring in the style of `build`'s (":param value: the raw string", ":param conf: the global
  config", ":param args: env args", ":returns: the string with every substitution this loader
  understands resolved"). The base raises `NotImplementedError`.
- `Loader.load` in the same file (`import inspect` at the top): before the override loop,
  `delay_replace = inspect.isclass(of_type) and issubclass(of_type, SetEnv)`. In the loop, the
  string converted by `_STR_CONVERT.to(...)` is `override.value` unchanged when `delay_replace`
  is true or `conf is None`, and `self.substitute(override.value, conf, args)` otherwise. A short
  comment says why: an override is a raw CLI string that has not been through the loader's
  substitution pass. The append and replace logic after it is unchanged.
- `IniLoader.substitute` in `src/tox/config/loader/ini/__init__.py`, after `build`:
  `return replace(conf, ReplaceReferenceIni(conf, self), value, args)` (both names are already
  imported there).
- `TomlLoader.substitute` in `src/tox/config/loader/toml/__init__.py`, after `build`:
  `return replace(conf, TomlReplaceLoader(conf, self), value, args)`.
- `docs/changelog/4047.bugfix.rst` (new, two lines): "Values passed via ``--override``/``-x`` or
  ``TOX_OVERRIDE`` now resolve substitutions such as ``{posargs}``, ``{env:VAR}`` and
  ``{env_name}``, instead of reaching the environment as literal text - by :user:`gaborbernat`."
- `docs/reference/config.rst`: in the part that documents overrides, a subsection
  "Substitutions inside an override" (`.. versionadded:: 4.61`): an override value goes through
  the same substitution pass as a value in the file, an example
  `tox -x env_run_base.commands='pytest {posargs:tests}' -e py -- -k slow` that runs
  `pytest -k slow`, and a note that overrides use the ini spelling of a substitution even in a
  TOML project.

## Tests to add
- `tests/config/loader/test_loader.py` (import `ToxProjectCreator` from `tox.pytest` under
  `TYPE_CHECKING`, next to `CaptureFixture`): `test_override_value_is_substituted`, using the
  `tox_project` and `monkeypatch` fixtures, parametrized twice:
  - `(filename, content, namespace)`: id `toml`, `tox.toml` with
    `env_list = ["a"]`, `[env_run_base]`, `skip_install = true`, `commands = [["python"]]`,
    namespace `env_run_base`; id `ini`, `tox.ini` with `[tox]` `env_list = a` and `[testenv]`
    `skip_install = true`, `commands = python`, namespace `testenv`.
  - `(override, posargs, expected)`: `posargs`: `python {posargs}` with
    `["--", "tests", "src"]` gives `python tests src`; `posargs-default`:
    `python {posargs:tests}`, no posargs, gives `python tests`; `env`: `python {env:MAGIC}` with
    `MAGIC=from-env` set gives `python from-env`; `env-name`: `python {env_name}` gives
    `python a`.
  The test runs `project.run("c", "-e", "a", "-k", "commands", "-x",
  f"{namespace}.commands={override}", *posargs)`, asserts success and
  `outcome.assert_out_err(f"[testenv:a]\ncommands = {expected}\n", "")`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/config/loader/test_loader.py`

## Out of scope
`set_env` overrides, `apply_overrides_to_raw`, the conversion rules (`_STR_CONVERT`), other
loaders, `docs/explanation.rst`, `docs/how-to/usage.rst`, `docs/tutorial/`.

## Open questions
None.
