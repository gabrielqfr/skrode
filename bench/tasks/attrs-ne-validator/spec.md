# Add the ne() validator

## Goal
`attrs.validators` has `lt`, `le`, `ge` and `gt`, all built on `_NumberValidator(bound, compare_op, compare_func)` in `src/attr/validators.py`, but nothing rejects one specific value. Add `ne(val)`: a validator that raises `ValueError` when the attribute equals `val`, with the same message and repr style as the other four (`'x' must be != 42: 42`).

## Files to touch
- `src/attr/validators.py`
- `src/attr/validators.pyi`
- `tests/test_validators.py`
- `docs/api.rst`
- `changelog.d/1571.change.md`

## Functions to add or change
- `src/attr/validators.py`: add `"ne"` to `__all__` in alphabetical order (between `"min_len"` and `"not_"`). Add `def ne(val):` right after `gt`, returning `_NumberValidator(val, "!=", operator.ne)`. Docstring in the style of `gt`'s: it raises `ValueError` if the initializer is called with a number equal to *val*; it uses `operator.ne` to compare; `Args:` `val: The value that is not allowed.`; `.. versionadded:: 26.2.0`.
- `src/attr/validators.pyi`: `def ne(val: _T) -> _ValidatorType[_T]: ...` after the `gt` line.
- `docs/api.rst`: after the `.. autofunction:: attrs.validators.gt` block, an `.. autofunction:: attrs.validators.ne` block in the same form, with a doctest: a `@define` class `C` with `x = field(validator=attrs.validators.ne(42))`; `C(43)` gives `C(x=43)`; `C(42)` raises `ValueError: ("'x' must be != 42: 42")`.
- `changelog.d/1571.change.md` (new), one line: ``Added new validator `attrs.validators.ne()` (not equal).``

## Tests to add
In `tests/test_validators.py`: import `ne` with the other validators (alphabetical). Rename `class TestLtLeGeGt` to `TestLtLeGeGtNe` and its docstring to ``Tests for `Lt, Le, Ge, Gt, Ne`.``, then add `ne` to every list of validators in that class: the `__all__` test, `test_retrieve_bound`, `test_repr`; add `(ne, 3)` and `(ne, 5)` to the `test_check_valid` cases (bound is 4) and `(ne, 4)` to `test_check_invalid`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_validators.py`

## Out of scope
`_NumberValidator` itself, other validators and their docstrings, `src/attrs/`.

## Open questions
None.
