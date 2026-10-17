# on_setattr hooks may be generator functions

## Goal
An `on_setattr` hook today is a plain callable `hook(instance, attribute, value)` whose return
value is stored. A hook cannot run code after the attribute has been set. Let a hook be a
generator function instead: the generated `__setattr__` calls it, takes the value it yields as the
value to set, sets the attribute, then resumes the generator so the code after its `yield` runs
with the new value already in place. The generator must yield exactly once. Plain hooks keep
working as before, and hooks still never run in `__init__`.

## Files to touch
- `src/attr/_compat.py`
- `src/attr/_make.py`
- `src/attr/_next_gen.py`
- `src/attr/setters.py`
- `src/attrs/__init__.pyi`
- `docs/examples.md`
- `tests/test_compat.py`
- `tests/test_setattr.py`

## Functions to add or change
- `src/attr/_compat.py`: a module-level cache `_IS_GENERATOR_RESULTS = {}` and
  `_lazy_is_generator(f: Callable) -> Callable[[], bool]`. It returns a closure that answers
  whether `f` is a generator function (`inspect.isgeneratorfunction`, imported inside the closure
  so importing attrs stays cheap), storing the answer in `_IS_GENERATOR_RESULTS[f]` the first time.
  If `f` is not hashable, the closure returns the uncached answer instead of raising. The check is
  lazy because `_make_own_setattr` runs at class creation for every attrs class with hooks.
- `_ClassBuilder._make_own_setattr` in `src/attr/_make.py`: each entry of `sa_attrs` becomes
  `(attribute, hook, _lazy_is_generator(hook))`. In the generated `__setattr__`:
  - a name with no hook is set with `_OBJ_SETATTR` and the method returns;
  - when the closure says the hook is a generator: create it with `hook(self, a, val)`, take the
    first value with `next(gen)` and set it with `_OBJ_SETATTR`, then call `next(gen)` again. If
    that raises `StopIteration`, return. If it yields a second value, call `gen.close()` and raise
    `RuntimeError("Generator on_setattr hook yielded more than once.")` (the first value stays
    set). An exception raised before the first yield propagates and nothing is set; one raised
    after the yield propagates with the value already set;
  - otherwise the existing behaviour: set `hook(self, a, val)`.
  Add to `attrib`'s docstring: `.. versionchanged:: 26.2.0` "*on_setattr* hooks can now be
  generator functions to run code before and after an attribute is set."
- `src/attr/_next_gen.py`: in `define`'s docstring, under *on_setattr*, after the sentence about
  the return value: "If the callable is a generator, it may yield exactly once and runs before and
  after the assignment and its yield value is used as the new value." In `field`'s docstring,
  under *on_setattr*: "May be a generator function that yields exactly once and runs before and
  after the assignment and its yield value is used as the new value." Both docstrings get a
  `.. versionchanged:: 26.2.0` "*on_setattr* hooks can now be generator functions that yield
  exactly once."
- `setters.pipe` in `src/attr/setters.py`: a `.. warning::` in its docstring, "Generator functions
  are not allowed in ``pipe()``." (`pipe` is not changed).
- `src/attrs/__init__.pyi`: `_OnSetAttrType` also accepts
  `Callable[[Any, "Attribute[Any]", Any], Generator[Any]]` (import `Generator` from `typing`).
- `docs/examples.md`: before the `(metadata)=` label, a section "## Running Custom Code
  Before/After Setting Attributes" with a `{doctest}` block: a generator hook that prints the
  incoming value, yields `int(value) * 2 + 286`, then prints `set x to 420`; a `@define` class
  `C` with `x: int = field(on_setattr=gen_hook)`; `C(42)` prints nothing, `c.x = "67"` prints
  both lines, and `c.x` is `420`.

## Tests to add
- `tests/test_compat.py`, class `TestLazyIsGenerator`: a generator function gives `True` and
  `_IS_GENERATOR_RESULTS[gen] is True`; a plain function gives `False` and is cached as `False`;
  a non-hashable object (`{}`) gives `False` without raising.
- `tests/test_setattr.py`, in the class holding the existing hook tests (`@attr.s` with
  `attr.ib(on_setattr=hook)` unless stated):
  - `test_change_with_generator_hook`: a hook yielding `"yielded!"`; init keeps `"x"`, assigning
    `"xxx"` stores `"yielded!"`.
  - `test_generator_hook_executes_after_yielding`: the hook records `("pre", instance.x)` before
    its yield and `("post", instance.x)` after; after `h.x = "xxx"` the calls are
    `[("pre", "x"), ("post", "xxx")]`, and none ran during init.
  - `test_generator_hook_not_called_in_init`: init keeps the passed value; assignment stores the
    yielded `"hooked"`.
  - `test_generator_hook_with_define`: `@attr.define` with `attr.field(on_setattr=hook)` yielding
    `value.upper()`: `"world"` is stored as `"WORLD"`.
  - `test_generator_hook_class_level`: `@attr.s(on_setattr=hook)` with two attributes; the hook
    runs once per assignment, never in init.
  - `test_generator_pre_yield_exception`: a hook raising `ValueError("bad value")` before its
    yield: the assignment raises it and the old value stays.
  - `test_generator_post_yield_exception`: a hook raising `RuntimeError("post-yield failure")`
    after its yield: the assignment raises it and the new value is set.
  - `test_generator_yields_multiple_times`: a hook yielding `"first"` then `"second"` inside
    `try/finally` that records cleanup: the assignment raises `RuntimeError` matching "Generator
    on_setattr hook yielded more than once", `c.x == "first"`, and the `finally` ran.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_compat.py tests/test_setattr.py`

## Out of scope
Generator hooks inside `setters.pipe` (only the docstring warning), the changelog,
`typing-examples/`, `docs/init.md`, `__init__` behaviour, any change to `_OBJ_SETATTR`.

## Open questions
None.
