# click.edit accepts os.PathLike filenames

## Goal
`click.edit(..., filename=...)` in `src/click/termui.py` takes `str | Iterable[str] | None`. It wraps a `str` in a tuple and passes anything else to `Editor.edit_files` as an iterable, so a single `pathlib.Path` is iterated and fails, and the annotations reject paths in an iterable. `filename` must accept an `os.PathLike[str]`, or any iterable of `str` and `os.PathLike[str]`, and edit those files exactly as it edits string paths.

## Files to touch
- `src/click/termui.py`
- `src/click/_termui_impl.py`
- `tests/test_termui.py`
- `tests/typing/typing_edit.py`
- `CHANGES.md`

## Functions to add or change
- `src/click/termui.py`: `import os` (sorted with the other imports). In both `@t.overload` signatures of `edit` and in the implementation, annotate `filename: str | os.PathLike[str] | cabc.Iterable[str | os.PathLike[str]] | None = None`. In the body, wrap `filename` in a one-item tuple when `isinstance(filename, (str, os.PathLike))`. In the docstring's `:param filename:` text add "It accepts a path or any iterable of paths.", and after the `.. versionchanged:: 8.2.0` entry add `.. versionchanged:: 8.5.0` with ``` ``filename`` accepts ``os.PathLike`` values in addition to strings.```
- `src/click/_termui_impl.py`: `Editor.edit_files(self, filenames: cabc.Iterable[str | os.PathLike[str]]) -> None`. Check that its body builds the command correctly from a path object; change it only if it does not.
- `CHANGES.md`, at the end of the `Unreleased` list: ``- {func}`edit` accepts `os.PathLike` values for `filename`, in addition to strings. {issue}`2869` {pr}`3781` ``

## Tests to add
- `tests/test_termui.py`, after `test_edit`: `test_edit_pathlib(runner, tmp_path, use_iterable)`, skipped on Windows like `test_edit` (no `sed`), parametrized `use_iterable` in `[False, True]` with ids `single` and `iterable`. Write `"a\nb\n"` to `tmp_path / "file.txt"`; pass the `Path` itself or `[path]` as `filename` with `editor="sed -i~ 's/$/Test/'"`; the result is `None` and the file now reads `"aTest\nbTest\n"`.
- `tests/typing/typing_edit.py` (new): with `assert_type` from `typing_extensions`, assert that `click.edit(filename="f.txt")`, `click.edit(filename=Path("f.txt"))` and `click.edit(filename=["f.txt", Path("g.txt")])` are each typed `None`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_termui.py`

## Out of scope
`click.edit`'s text mode, `Editor.edit`, `launch`, other termui functions.

## Open questions
None.
