# one() and only() raise a falsy custom exception

## Goal
`more_itertools.one(iterable, too_short=None, too_long=None)` and `only(iterable, default=None, too_long=None)` pick the exception to raise with `raise too_long or ValueError(msg)` and `raise too_short or ValueError(...)`. An exception instance whose `__bool__` returns `False` is therefore ignored and the default `ValueError` is raised instead. Also, the default message, which calls `repr()` on the first two items, is built even when a custom `too_long` is given, so an item whose `repr` fails breaks the call. Both functions must raise the custom exception whenever it is not `None`, and build the default message only when they raise the default `ValueError`.

## Files to touch
- `more_itertools/more.py`
- `tests/test_more.py`

## Functions to add or change
- `one` in `more_itertools/more.py`: when a second item is found, `raise too_long` if `too_long is not None`, else raise `ValueError` with the current message (same f-string, unchanged). When the iterable is empty, `raise too_short` if `too_short is not None`, else raise the current `ValueError('too few items in iterable (expected 1)')`. Drop the `msg` variable.
- `only` in the same file: the same change for its `too_long` branch. The empty case still returns `default`.
- Docstrings stay as they are.

## Tests to add
In `tests/test_more.py`:
- `OneTests.test_falsy_custom_exception`: define `class FalsyError(Exception)` whose `__bool__` returns `False`; `mi.one(count(), too_long=FalsyError('too many'))` raises `FalsyError`, and `mi.one([], too_short=FalsyError('too few'))` raises `FalsyError`.
- `OneTests.test_too_long_does_not_evaluate_repr`: a class `NoRepr` whose `__repr__` raises `RuntimeError`; `mi.one((NoRepr() for _ in count()), too_long=OverflowError)` raises `OverflowError`.
- `OnlyTests.test_falsy_custom_exception`: `mi.only([1, 2], too_long=FalsyError('too many'))` raises `FalsyError`.
- `OnlyTests.test_too_long_does_not_evaluate_repr`: `mi.only([NoRepr(), NoRepr(), NoRepr()], too_long=OverflowError)` raises `OverflowError`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_more.py`

## Out of scope
Other functions in `more.py`, `more.pyi`, `docs/`.

## Open questions
None.
