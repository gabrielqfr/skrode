# Make Opus 5.5 the default model

## Goal
The default Claude model for generated reports is `claude-opus-5`. Change it to `claude-opus-5-5`, which is cheaper per token and the one model measured with prompt caching. A model set in `config.toml` still wins; only the default changes.

## Files to touch
- `src/sensei_top/llm/config.py`
- `src/sensei_top/review/narrative.py`
- `src/sensei_top/cli.py`
- `tests/test_llm.py`

## Functions to add or change
- `src/sensei_top/llm/config.py`: `DEFAULT_MODEL = "claude-opus-5-5"`.
- `src/sensei_top/review/narrative.py`: its own `DEFAULT_MODEL = "claude-opus-5-5"`.
- `src/sensei_top/cli.py`, `build_parser()`: the `--set-model` argument's help becomes `"e.g. claude-opus-5-5"`.
- Do not touch the price and name tables in `src/sensei_top/llm/client.py`: `claude-opus-5` stays a known model.

## Tests to add
In `tests/test_llm.py`, update the two expectations of the default: `test_env_key_alone_falls_back_to_defaults` expects `model="claude-opus-5-5"`, and `test_write_llm_config_round_trips_the_coach_table_and_preserves_other_keys` expects `cfg.model == "claude-opus-5-5"`. Leave other tests that pass `model="claude-opus-5"` explicitly as they are.

## Commands that must pass
- `uv run pytest -q tests/test_llm.py`
- `uv run ruff check .`

## Out of scope
`llm/client.py`, effort defaults, docs.

## Open questions
None.
