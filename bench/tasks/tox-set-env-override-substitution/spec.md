# Substitute inside `set_env` overrides, and keep override rules through references

## Goal
`Loader.load` (`src/tox/config/loader/api.py`) skips substitution for a `set_env` override
(`delay_replace`) and converts it with `_STR_CONVERT`, so no replacer is ever attached:
`-x testenv.set_env+=MAGIC={env_name}` sets `MAGIC` to the literal `{env_name}`, and
`{env:VAR}`, `{env:KEY:default}`, `{posargs:...}`, `file|path` and `{env:BLOCK}` lines (a
variable holding several `KEY=value` lines) never resolve. An appended override is merged with
`SetEnv.update(..., override=True)`, which reads values eagerly. And when another section
references an overridden `set_env` (INI `{[testenv]set_env}`, TOML `{replace="ref", of=[...,
"set_env"]}`), the override is pasted onto the raw text, losing these rules. Build a `set_env`
override like a value from the file, merge it lazily with the rules below, and apply the same
rules when the overridden `set_env` is reached through a reference.

## Rules (the tests pin each one)
1. `set_env=...` replaces the configured value; `set_env+=...` extends it. Both go through
   `Loader.build` with the `set_env` factory, so the loader attaches its replacer. `setenv` is
   an alias of `set_env` for overrides too. Overrides come from `-x` or `TOX_OVERRIDE` alike.
2. A key assigned directly in an appended override replaces the configured key, its value and
   its marker, even if the value was already read: `A=base` extended with `A=override` reads
   `override`; `A=base; sys_platform == 'nope'` + `A=override` gives `{"A": "override"}`;
   `A=base` + `A=override; sys_platform == 'nope'` gives `{}` (a false marker hides the key).
3. Keys an override pulls in lazily (`{env:BLOCK}` lines, `file|` lines) also replace configured
   keys, except keys assigned directly in the same override or in any later override. With base
   `MAGIC=base`, `BLOCK="MAGIC={env_name}\nNEW={env:MAGIC}"`, `SECOND_BLOCK=
   "MAGIC=second\nNEW={env:MAGIC}"`, `a.env` holding `MAGIC=file`, env `a`, the appended
   overrides give: `[{env:BLOCK}]` -> `MAGIC=a, NEW=a`; `[{env:BLOCK}, MAGIC=later]` ->
   `later, later`; `[MAGIC=earlier, {env:BLOCK}]` -> `a, a`; `[{env:BLOCK}, {env:SECOND_BLOCK}]`
   -> `second, second`; `["{env:BLOCK}\nMAGIC=local"]` -> `local, local`; `[{env:BLOCK},
   file|a.env]` -> `file, file`; `[file|a.env, {env:BLOCK}]` -> `a, a`; a block
   `MAGIC=disabled; sys_platform == 'nope'\nNEW={env:MAGIC}` (no `MAGIC` in the shell) hides
   `MAGIC` and gives `NEW=`.
4. Values resolve after merging: `{env:MAGIC}` inside any block reads the final `MAGIC`.
5. tox's own exported variables (`TOX_ENV_NAME`, `PYTHONIOENCODING`, ...) always win, over
   override blocks and env files alike, and a configured marker on such a key is dropped. A
   block `file|{env:TOX_ENV_NAME}.env` reads `a.env` for env `a`; a block or file line
   `TOX_ENV_NAME=wrong\nMAGIC={env:TOX_ENV_NAME}` gives `MAGIC=a, TOX_ENV_NAME=a`. An export
   callback may read another export: `values.export({"FIRST": lambda: values.load("SECOND"),
   "SECOND": lambda: "value"})` reads both as `"value"`.
6. A reference to an overridden `set_env` carries the override rules into the consuming env,
   and `{env_name}`, `{env:...}` and env-file paths resolve in the consuming env. The
   consumer's own keys still win: `[testenv:py] set_env = {[testenv]set_env}` plus
   `MAGIC=child` (or TOML `[ref, {MAGIC="child"}]`) keeps `MAGIC=child`, and an override on the
   consumer (`-x testenv:py.set_env+=MAGIC=cli` / `env.py.set_env+=...`) gives `cli`. Base
   `file|{env:FILE}` with consumer `FILE=child.env` reads `child.env`, with or without an
   override. A base whose value factor filtering empties (`win: MAGIC=base` on Linux) still
   takes an appended `MAGIC=appended`. A key literally named `file`, read from an env file,
   stays a key. In TOML, child entries keep their order around a reference: `[{file=
   "before.env"}, ref, {file="after.env"}, {MAGIC="last"}]` gives the last writer's values.
7. A `set_env` that references itself while it has an override fails (exit 1) with
   `circular set_env reference <chain joined by ' -> '>`.
8. TOML shape: a reference placed inside a `set_env` array, to a `set_env` that has overrides,
   must point at a table (no `extend`) or at an array with `extend = true`; otherwise it fails
   with `set_env arrays require table references or array references with extend=true`. A
   reference used as the whole `set_env` value accepts a table, an array or a string. A TOML
   value such as `"  café;#\nsecond line  "` passes through a reference unchanged.
9. A malformed env-file line fails cleanly (exit 1): `invalid line 'MISSING_SEPARATOR' in
   set_env` is now raised as `Fail`, not `ValueError`.

## Files to touch
- `src/tox/config/set_env.py`
- `src/tox/config/loader/api.py`
- `src/tox/config/loader/ini/replace.py`
- `src/tox/config/loader/toml/_api.py`
- `src/tox/config/loader/toml/_replace.py`
- `src/tox/config/sets.py`
- `tests/config/test_set_env.py`, `tests/config/loader/test_loader.py` (add at the end)
- `tests/config/loader/test_set_env_override.py` (new)
- `tests/config/loader/test_set_env_native.py` (new)

## Functions to add or change

### `src/tox/config/set_env.py`
- `SetEnvReference`, a `@dataclass(frozen=True)` `@final` class with docstring "Keep overrides
  pending until the consuming environment has registered its keys.", fields `config: SetEnv`,
  `args: ConfigLoadArgs`, `shape: Literal["string", "table", "array"]`. Add it to `__all__`.
  `SetEnvRaw` becomes `str | dict[str, str | SetEnvEntry] | SetEnvReference |
  list[dict[str, str | SetEnvEntry] | SetEnvReference]`.
- `SetEnv.__init__`: a `shape` attribute (`raw.shape` for a `SetEnvReference`, else `"array"`,
  `"table"` or `"string"` from the raw type). New state: `_overrides: list[tuple[SetEnv,
  set[str]]]` (queued override blocks, each with its protected keys), `_reference_entries:
  list[dict | SetEnvReference]`, `_override_keys: set[str]` (keys assigned directly by any
  override), `_args = ConfigLoadArgs([], name, env_name)`. A list goes to `_parse_list`; a
  `SetEnvReference` sets `_reference_entries = [raw]` and parses
  `raw.config.raw(raw.args, resolve=False).items()` with `_parse_dict`.
- `_parse_list(raw)`: with no `SetEnvReference` in it, stream the tables in order as today.
  Otherwise `_reference_entries = raw.copy()` and `_parse_dict` every entry's items (a
  reference's `config.raw(args, resolve=False)`), skipping `file` keys whose value is a string:
  those files are read in step c below.
- `use_replacer(value, args)`: only stores the replacer and `args.copy()`; files are no longer
  read here.
- `_resolve_files(args)`: the env-file reading that `use_replacer` did, moved here. A key from a
  file is skipped when it is in that file's keys-after set, in `_override_keys` or exported;
  otherwise it sets `_raw` and drops the key's cached value and marker. Then clear
  `_env_files`. `_stream_env_file` and `_extract_key_value` move below it unchanged, except
  that `_extract_key_value` raises `Fail`.
- `_resolve_replacements(args=None)` replaces `_iter_needs_replacement`. It returns at once
  while `_expanding`, or when there are no env files, lines needing replacement, queued
  overrides or reference entries. Otherwise, with `args` (default `self._args.copy()`) and
  `_expanding` set (cleared in `finally`), into one `entries` dict (key -> (value, marker)):
  a. `_resolve_files(args)`.
  b. `_expand` each line of `_needs_replacement`; then drop keys in `_raw` or `_defined_keys`.
  c. Each reference entry, in order. Its protected keys are `_override_keys` plus the direct
     keys of every later entry (a reference's `config.raw(args, resolve=False)` keys; a
     table's keys except a string `file`). A `SetEnvReference` resolves to
     `entry.config.raw(entry.args)`; a table to `SetEnv(table, name, env_name, root)` given
     this replacer and `args`, then `.raw(args)`. Drop keys in `_override_keys`, add the rest
     to `entries` (markers become `Marker`s), and `_merge_entries` the unprotected part at once
     so a later entry's file selector reads it.
  d. Each queued override block, in order: `block.raw(args)` minus its protected keys, added
     to `entries` and merged at once.
  Then clear `_needs_replacement`, `_overrides` and `_reference_entries`, `_merge_entries(
  entries)`, and set `changed = True`.
- `_merge_entries(entries)`: for each key not exported, set `_raw` and set or clear its marker;
  drop the merged keys from `_materialized`.
- `load` and `__iter__` call `_resolve_replacements()` first. `load` keeps exports in
  `_exported` (reads `self._exported[item]()` instead of popping). `__iter__` yields
  materialized keys whose marker matches, exported keys not materialized, then raw keys whose
  marker matches; it no longer yields from the replacement step.
- `_expand`: the nested block calls `block._resolve_files(args)` before its lines are read.
- `raw(self, args, *, resolve=True) -> dict[str, SetEnvEntry]` (docstring: "Preserve lazy
  values and markers when another section includes this configuration."): resolve first when
  asked, then every key of `_materialized` then `_raw` (raw wins) as `{"value": value}`, plus
  `"marker": str(marker)` when the key has one. Values stay unsubstituted.
- `export`: also drops the key's marker.
- `extend(self, other: SetEnv) -> None` (docstring: "Appended blocks override base keys;
  explicit keys in the same override win."): add `other._raw`'s keys to the protected set of
  every queued override and to `_override_keys`; copy each of those keys into `_raw` (drop its
  cached value, add it to `_defined_keys`, copy `other`'s marker or clear it); queue
  `(other, set())` when `other` has env files, lines needing replacement or reference entries;
  set `changed = True`. `update` stays as it is.

### `src/tox/config/loader/api.py`
- `load`: drop `delay_replace`; each override is `self._build_override(entry, of_type, factory,
  conf, args)`; an appended `SetEnv` uses `converted.extend(converted_override)`.
- `_build_override(self, entry, of_type, factory, conf, args) -> V` (import `SetEnv` inside it:
  `set_env` imports this module): when `conf` is set and `of_type` is a `SetEnv` subclass,
  `self.build(entry.key, of_type, factory, conf, entry.value, args)`; otherwise
  `_STR_CONVERT.to(...)` of `entry.value` when `conf is None`, else of `self.substitute(...)`.
- `set_env_reference(self, raw, overrides, conf, args) -> SetEnvReference` (imports inside):
  if `args.chain[-1] in args.chain[:-1]`, raise `ValueError(f"circular set_env reference
  {' -> '.join(args.chain)}")`. The factory is `SetEnv(value, "set_env", args.env_name,
  conf.core["tox_root"])`. `result = self.build("set_env", SetEnv, factory, conf, raw, args)`;
  remember `result.shape`; for each override, `_build_override` with that factory, then
  `result.extend(it)` if appended, else `result = it`. Return `SetEnvReference(result, args,
  shape)` with the remembered shape.

### `src/tox/config/loader/ini/replace.py`
`_resolve_section_proxy(self, src, key, args: ConfigLoadArgs)` (the caller passes `conf_args`).
`keys` is `{"set_env", "setenv"}` when `key` is one of them, else `{key}`; `overrides` are the
section's overrides whose key is in `keys`. With no `set_env` override, the result is as
today: `apply_overrides_to_raw(overrides, key, src[key])` through `process_raw`. With one,
`process_raw` gets `src[key]` as is. Either way a `KeyError` from `process_raw` gives `""`. For
`set_env` with overrides, copy `args`, append `f"{src.name}.{key}"` to its `chain`, take
`self.loader.set_env_reference(processed, overrides, self.conf, args).config.raw(args)` and
return one line per key, joined by `"\n"`: `f"{name}={value}"` with `;` in the value written
`\;`, plus `f"; {marker}"` when there is a marker.

### `src/tox/config/loader/toml/_api.py` and `_replace.py`
- `TomlTypes` gains `SetEnvReference` (imported at module level in both files).
- `Unroll.__call__`: a `SetEnvReference` passes through untouched. In a list, an item that
  unrolls to a `SetEnvReference` is appended as is after a shape check: its `shape` must be
  `"array"` when the item is a dict with a truthy `extend`, else `"table"`; otherwise raise
  `TypeError("set_env arrays require table references or array references with
  extend=true")`.
- `_replace_ref`: filter the namespace's overrides by key as the INI side does. When
  `skip_str` is true (the `set_env` path), the ref key is `set_env`/`setenv` and there are
  overrides, copy `self.args`, append the ref path to its `chain`, and the loaded value becomes
  `self.loader.set_env_reference(loaded, overrides, self.conf, args)`. Otherwise
  `apply_overrides_to_raw(overrides, ref_key, loaded)` as today.

### `src/tox/config/sets.py`
`set_env_post_process` exports `PYTHONIOENCODING` too: `values.export({"PYTHONIOENCODING":
lambda: "utf-8", **self.exported_set_env_loader()})` replaces the `update` + `export` pair.
`set_env_factory` accepts a `SetEnvReference`, and lists whose items are tables or
`SetEnvReference`s.

## Tests to add
Use `tox_project` (`ToxProjectCreator` under `TYPE_CHECKING`), `monkeypatch`, and
`project.run("c", "-e", <env>, "-k", "set_env", ...)` or `run("r", "-e", "py", ...)` with a
`python -c` command that prints the variable. Assert on the stripped output lines.
- `tests/config/test_set_env.py`: rule 2 on `SetEnv` objects, each with and without a `load`
  first; rule 5's export callback; an exported key's configured false marker is dropped.
- `tests/config/loader/test_loader.py`: rule 1 for TOML and INI, with and without a base value:
  `set_env+=MAGIC={env_name}`, `set_env=MAGIC={env_name}`, `{env:FROM_SHELL}`,
  `{env:KEEP:1}`, `file|{env_name}.env`, a false marker; rule 3's table for both formats over
  `-x` and `TOX_OVERRIDE` (`;`-joined).
- `tests/config/loader/test_set_env_override.py`: rule 5's two blocks; rule 6 through INI and
  TOML references with `set_env` and `setenv`, `-x` and `TOX_OVERRIDE`; rule 7 for both
  formats; rule 6's child keys, env-file keys, child selector, child file order, filtered base.
- `tests/config/loader/test_set_env_native.py`: rule 8 (four accepted shapes run, three
  rejected), the literal value, rule 6's `{env:FILE}` selector, an override selector read from
  an earlier override's file, rule 9 for INI and TOML.

## Commands that must pass
- `.venv/bin/python -m pytest -q -n 4 tests/config --ignore=tests/config/cli/test_argcomplete.py`

Existing tests must pass unchanged.

## Out of scope
Docs, changelog, `docs/plugin/api.rst`, `pyproject.toml`; `SetEnv.update`;
`apply_overrides_to_raw` itself; other config keys' overrides.

## Open questions
None.
