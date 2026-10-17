# natural_list accepts any iterable

## Goal
`humanize.natural_list(items: list[Any]) -> str` joins items as `"a, b and c"`, but it indexes and slices `items` and calls `len()` on it, so tuples work by accident and dict views, generators and other one-pass iterables fail or are mishandled. It must accept any iterable: consume it once, then format the same way it does today for a list.

## Files to touch
- `src/humanize/lists.py`
- `tests/test_lists.py`

## Functions to add or change
- `natural_list` in `src/humanize/lists.py`: annotate `items: Iterable[Any]`, importing `Iterable` from `collections.abc` inside the existing `if TYPE_CHECKING:` block next to `Any`. First build `item_list = [str(item) for item in items]`, then work only on `item_list`: empty returns `""`; one item returns it; two return `f"{a} and {b}"`; more return the items but the last joined with `", "`, then `f" and {last}"`. Output for lists stays exactly as it is now.
- Its docstring: "Convert a list of items" becomes "Convert an iterable of items", and the `Args` line becomes `items (Iterable): An iterable of items.`

## Tests to add
In `tests/test_lists.py`, add cases to the existing `test_natural_list` parametrize list (each case wraps the argument in a one-element list, as the existing ones do):
- `("one", "two", "three")` → `"one, two and three"`; `("one", "two")` → `"one and two"`; `("one",)` → `"one"`;
- `{"one": 1, "two": 2}.keys()` → `"one and two"`;
- a generator `(x for x in ["one", "two", "three"])` → `"one, two and three"`;
- `range(1, 4)` → `"1, 2 and 3"`.
Change the test function's `test_args` annotation to `Iterable[Any]` (imports from `collections.abc` and `typing`).

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_lists.py`

## Out of scope
Other modules, locales, docs.

## Open questions
None.
