# Store `response.json()` on turns, condensed with condense-json

## Goal
Since logging moved to the message store, the raw provider payload (`response.response_json`) is
dropped: `llm logs --json` always shows `"response_json": null`. Store it again, in a new
nullable `turns.response_json` column, but **condensed** with the `condense-json` library: long
strings and JSON subtrees that the turn's own messages, its tools, its schema or the model's
boilerplate dictionary already hold are replaced with `{"$": "<key>"}` references. The
replacement dict is never stored; it is rebuilt at read time from the same rows, so the same
walk gives the same dict on both sides. Also make the OpenAI Responses plugin store one
reasoning ciphertext, not two, when streaming.

## condense-json 1.1, the library to use
`from condense_json import UncondenseError, condense_json, uncondense_json`.
- `condense_json(obj, replacements) -> obj`: `replacements` maps keys to values. String values
  are matched as substrings of strings in `obj`; dict and list values are matched as whole
  subtrees by structural equality (key order does not matter); the outermost match wins. A
  match becomes `{"$": key}`. Input that already looks like a marker is escaped, so the round
  trip is lossless.
- `uncondense_json(obj, replacements) -> obj` reverses it; an unknown key raises
  `UncondenseError`.
It is already installed in `.venv`; read its source there if you need more.

## Files to touch
- `pyproject.toml`: the dependency condense-json>=0.1.3 becomes condense-json>=1.1
- `llm/migrations.py`
- `llm/logs.py`
- `llm/default_plugins/openai_models.py`
- `tests/test_logs_store.py`, `tests/test_openai_responses.py` (add tests at the end)

## Functions to add or change

### `llm/migrations.py`: `m027_turns_response_json(db)`
A new `@migration` after `m026_message_tree_view`: `db["turns"].add_column("response_json",
str)`. NULL for turns logged before it and for models with no raw payload.

### `llm/logs.py`: module level
Import `UncondenseError, condense_json, uncondense_json` from `condense_json`. Add:

- `_CONDENSE_MIN_LENGTH = 64`.
- `TURN_TOOLS_SQL`: selects `tools.name, tools.description` from `turn_tools` joined to `tools`
  on `tools.id = turn_tools.tool_id`, `where turn_tools.turn_id = ?`.
- `_payload_replacements(messages, tools=(), schema=None, model_replacements=None) ->
  dict[str, Any]`. Rules:
  - A string is offered only when its length is `>= _CONDENSE_MIN_LENGTH`. A dict or list is
    offered only when it is non-empty and `len(json.dumps(value, separators=(",", ":")))` is
    `>= _CONDENSE_MIN_LENGTH` (skip it if `json.dumps` raises `TypeError`/`ValueError`).
    Anything else (None, numbers, short values) is never offered.
  - For message index `mi` and `message_dict = message.to_dict()`, for each part index `pi` in
    `message_dict.get("parts", [])`, with `base = f"{mi}.{pi}"`:
    - `f"{base}.text"` = the part's `"text"`; `f"{base}.output"` = its `"output"`;
    - when the part has truthy `"arguments"`: `f"{base}.args"` =
      `json.dumps(arguments, separators=(",", ":"))`; `f"{base}.args2"` =
      `json.dumps(arguments)` only if it differs from the compact form; `f"{base}.argsv"` = the
      arguments dict itself (the dict rule);
    - the part's `"provider_metadata"` (or `{}`) is walked under the prefix `f"{base}.pm"`.
  - Then the message's own `"provider_metadata"` (or `{}`) is walked under `f"{mi}.pm"`.
  - Walking `obj` under `prefix`: a dict is offered at `prefix` (dict rule), then each
    `key, value` is walked under `f"{prefix}.{key}"`; a list is offered at `prefix`, then each
    item under `f"{prefix}.{index}"`; anything else is offered at `prefix` (string rule).
  - `tools` is an iterable of `(name, description)` pairs. Skip a pair whose name is falsy or
    whose description is not a string of at least `_CONDENSE_MIN_LENGTH`. A name seen with two
    different descriptions is dropped entirely, whatever the order of the pairs. Each remaining
    name gives `f"tool.{name}.description"` = its description.
  - `schema` (a dict or None) is offered at `"schema"` (dict rule).
  - Each `key, value` of `model_replacements` (a dict or None) is added as `f"m.{key}"` with no
    length rule.
  Example: one assistant message with a `ReasoningPart(text="r" * 70, provider_metadata=
  {"openai": {"encrypted_content": blob}})` (blob of 80 chars) and a text part `"t" * 64` gives
  exactly `{"0.0.text": "r"*70, "0.0.pm": {"openai": {...}}, "0.0.pm.openai":
  {"encrypted_content": blob}, "0.0.pm.openai.encrypted_content": blob, "0.1.text": "t"*64}`.
  `[llm.assistant("short")]` gives `{}`.
- `condense_payload(payload, messages, tools=(), schema=None, model_replacements=None) -> str |
  None`: `None` when `payload` is `None`, else `json.dumps(condense_json(payload,
  _payload_replacements(...)))`.
- `resolve_payload(condensed, messages, tools=(), schema=None, model_replacements=None)`:
  `None` when `condensed` is `None`, else `uncondense_json(json.loads(condensed),
  _payload_replacements(...))`. Lets `UncondenseError` propagate.
- `_model_json_replacements(model_id)`: `None` for a falsy id; otherwise `llm.get_model(model_id)`
  (import `UnknownModelError, get_model` from `llm` inside the function, to avoid an import
  cycle) and return its `json_replacements` attribute or `None`; an `UnknownModelError` returns
  `None`, so markers that needed the dictionary fail later with `UncondenseError`.

### `LogStore.log(response, thread_id=None)`
Keep the messages passed to `ensure_chain` in a variable (`own_messages =
response._messages_now()`). In the `turns` row add `"response_json": condense_payload(
getattr(response, "response_json", None), own_messages, [(tool.name, tool.description) for tool
in response.prompt.tools], schema=response.prompt.schema, model_replacements=getattr(
response.model, "json_replacements", None))`.

### `LogStore.turn_response_json(turn_id) -> Any` (new, public)
Query the turn's `model`, `parent_message_hash`, `tip_message_hash`, `response_json` and
`schemas.content as schema_json` (left join `schemas` on `turns.schema_id = schemas.id`). Return
`None` for an unknown turn or a NULL `response_json`. Otherwise the turn's own messages are
`outputs = load_chain(tip)[len(load_chain(parent)):]`, and the result is
`resolve_payload(row["response_json"], outputs, <tool pairs>, schema=_load(schema_json),
model_replacements=_model_json_replacements(model))`. It raises `UncondenseError` when a
reference no longer resolves. Add `LogStore._turn_tool_pairs(turn_id) -> list[tuple[str, str]]`
running `TURN_TOOLS_SQL`; both read paths use it.

### `llm logs` rows (`merged_log_rows`, `_LogRowBuilder`)
Add `turns.response_json` to the select that `merged_log_rows` runs (the one listing
`turns.tip_message_hash`). In `_LogRowBuilder.build`, `"response_json"` stops being `None`: it is
`self._resolve_response_json(row, outputs)`, a new method returning `None` when the row's value
is empty, else `json.dumps(resolve_payload(...))` with the same arguments as
`turn_response_json` (tools from `self.store._turn_tool_pairs(row["id"])`, schema from
`row.get("schema_json")`, model from `row.get("model")`). On `UncondenseError` it returns
`None`: one broken payload must not fail the whole listing. `prompt_json` stays `None`.

### `llm/default_plugins/openai_models.py`: streamed reasoning refresh
While streaming, `Responses.execute` yields a reasoning `StreamEvent` at
`response.output_item.done`, but the `response.completed` payload holds a different
`encrypted_content` for the same item (OpenAI encrypts per event), so the stored part and
`response_json` disagree. The framework (`_resolve_part_index` in `llm/models.py`) sets
`part_index` on each yielded event in place before the generator resumes.
- In both `Responses.execute` and `AsyncResponses.execute`, in the streaming branch: keep
  `reasoning_done_events: dict[str, StreamEvent] = {}`. Where the reasoning event for a done
  item is built, store it under the item's `id` (when it has one) before yielding it.
- After `response.response_json = remove_dict_none_values(final_response_dict)`, yield every
  event from `self._reasoning_refresh_events(response.response_json, reasoning_done_events)`
  (`yield from` in the sync one, a `for` loop in the async one).
- `_SharedResponses._reasoning_refresh_events(self, response_json, done_events) ->
  list[StreamEvent]`: for each item of `response_json.get("output") or []` that is a dict with
  `"type" == "reasoning"`, find `done_events.get(item.get("id"))`; skip it when missing or its
  `part_index` is `None`. `meta` = the item's `"id"`, `"encrypted_content"` and `"summary"`
  where truthy. If `meta` is not empty, add `StreamEvent(type="reasoning", chunk="",
  part_index=prior.part_index, provider_metadata={"openai": meta})`.
Result: still one `ReasoningPart`, its `provider_metadata["openai"]["encrypted_content"]` equals
the final payload's, and its text is unchanged.

## Tests to add
At the end of `tests/test_logs_store.py` (fixtures `store`, `mock_model`; `merged_log_rows` is
already imported there). Log with `mock_model.enqueue([...])`, `response =
mock_model.prompt(...)`, `response.text()`, then set `response.response_json = payload` and call
`store.log(response)`:
- a payload `{"content": LONG, "id": ...}` where `LONG` is the response text (64+ chars): the
  stored column has `"content": {"$": "0.0.text"}` and not `LONG`; `turn_response_json` returns
  the payload; `merged_log_rows` gives it back in `row["response_json"]` (JSON text);
- no payload: NULL column, `turn_response_json` is `None`; unknown turn id gives `None`;
- a short answer is stored verbatim; a payload with `{"$": "not-a-marker"}` round trips;
- overwrite the column with `{"content": {"$": "9.9.text"}}`: the row's `response_json` is
  `None`, `turn_response_json` raises `UncondenseError`;
- `_payload_replacements`: the example above; tool arguments give `args` and `args2`; a tool
  result's output; tool descriptions; conflicting descriptions dropped in either order; short or
  `None` descriptions skipped;
- a tool (`llm.Tool.function(..., description=<64+ chars>)`) echoed in the payload's `"tools"`
  becomes `{"$": "tool.<name>.description"}` and resolves through `turn_tools`;
- a schema echoed with keys reordered becomes `{"$": "schema"}` and resolves to the stored
  schema; object-form tool arguments round trip; a reasoning `summary` list in provider metadata
  condenses to one `"0.0.pm.openai.summary"` reference;
- `mock_model.json_replacements = {"tool_usage_0": {...}}` (delete it in a `finally`): the
  matching subtree is stored as `{"$": "m.tool_usage_0"}` and resolves; after
  `update turns set model = 'gone-model'`, `turn_response_json` raises `UncondenseError` and the
  row's `response_json` is `None`.
At the end of `tests/test_openai_responses.py`: a streamed Responses call (reuse
`_responses_reasoning_summary_stream`, `_responses_sse`, `IteratorStream` and `httpx_mock` as
the existing tests do) whose `response.completed` output carries `"encrypted_content":
"encrypted-final"` for `rs_1`: one `ReasoningPart`, carrying `"encrypted-final"`, text
`"Thinking aloud"`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_logs_store.py tests/test_openai_responses.py tests/test_migrate.py tests/test_llm_logs.py`

Existing tests must pass unchanged.

## Out of scope
Docs, README and changelog; `json_replacements` dictionaries for the OpenAI model classes;
storing `prompt_json`; `cli.py` (it already decodes `*_json` keys); `tool_calls` in streamed
chat-completions payloads.

## Open questions
None.
