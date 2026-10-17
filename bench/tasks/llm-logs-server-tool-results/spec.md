# llm logs shows server-side tool results

## Goal
A model can execute a tool on the provider's side during its response (a `ToolCallPart` or
`ToolResultPart` with `server_executed=True`). The result then sits among the turn's output
parts, but `log_row_extras` in `llm/logs.py` only collects `ToolResultPart`s from the turn's
input parts, so `llm logs` never shows it. Collect results from the output parts too, mark every
result (and every tool call) with a `server_executed` key, give server results their `parts` row
id from the turn's own output messages, and render them in the Markdown log under `## Response`,
after `### Tool calls` and before the response text. They also appear in `--json` and `--short`,
which already print `row["tool_results"]`. Locally executed results keep their current place,
in the prompt section.

## Files to touch
- `llm/logs.py`
- `llm/cli.py`
- `docs/changelog.md`
- `tests/test_llm_logs.py`
- `tests/test_plugins.py`

## Functions to add or change
- `_LogRowBuilder` in `llm/logs.py`: next to `_input_segment_hashes`, a method
  `_output_segment_hashes(self, tip_hash: str | None, parent_hash: str | None) -> list[str]`:
  walk from `tip_hash` through `messages.parent_hash` (one
  `select parent_hash from messages where hash = ?` per step) until the hash is empty or equals
  `parent_hash`, stopping if a hash is missing from the table; return the hashes visited, oldest
  first. Where the builder sets `_input_message_hashes`, also set `"_output_message_hashes":
  self._output_segment_hashes(row["tip_message_hash"], row["parent_message_hash"])`.
- `log_row_extras` in `llm/logs.py`:
  - `output_message_hashes = row.get("_output_message_hashes") or [row.get("_tip_message_hash")]`;
    tool call ids come from `_part_ids(store, output_message_hashes, "tool_call")` (no longer from
    the tip hash alone), and `server_result_ids = _part_ids(store, output_message_hashes,
    "tool_result")` beside the existing `result_ids` from the input hashes.
  - each `tool_calls` entry gains `"server_executed": part.server_executed`.
  - the result parts are the input `ToolResultPart`s paired with `False`, then the output
    `ToolResultPart`s paired with `True`. Each `tool_results` entry takes its `id` from
    `server_result_ids` or `result_ids` accordingly, and gains `"server_executed": <bool>` placed
    right after `"exception"` (key order matters: `--json` output is compared as text).
- `annotate_log_rows` in `llm/cli.py`: `"_output_message_hashes"` joins the list of private keys
  removed from each row (after `"_input_message_hashes"`), so it never reaches the output.
- `logs_list` in `llm/cli.py`, Markdown mode:
  - move the loop that prints each tool result (name, `tool_call_id`, fenced output, `**Error**`
    line, attachments) into a nested helper `_display_tool_results(tool_results)`, next to
    `_display_fragments`, with the output format unchanged;
  - split `row["tool_results"]` into local results (no `server_executed`) and server results.
    The existing `### Tool results` heading in the prompt section prints only local results,
    through the helper;
  - after the `### Tool calls` block of the response and before the response text, when there
    are server results: `click.echo("### Tool results\n")`, the helper, then `click.echo("")`.
- `docs/changelog.md`: under the unreleased section, a bullet: `llm logs` now includes the output
  of server-side tool calls in a **Tool results** section within the response, also in
  `llm logs --json` and `--short`, with a new `server_executed` key distinguishing them from
  locally executed results ([#1629](https://github.com/simonw/llm/issues/1629)).

## Tests to add
- `tests/test_llm_logs.py`, `test_logs_server_side_tool_results(logs_db, mock_model)`: enqueue
  `llm.parts.StreamEvent`s, all with `tool_call_id="tc_server_1"` and `server_executed=True`
  except the last: `tool_call_name` chunk `"shell"`, `tool_call_args` chunk
  `'{"commands": ["python fib.py"]}'`, `tool_result` chunk `"17711"` with `tool_name="shell"`,
  then a plain `text` chunk `"Fibonacci(22) = 17711"`. Prompt, `.text()`, `log_to_db(logs_db)`.
  - `llm logs -c` exits 0 and its output contains, in order: `## Response`, a blank line,
    `### Tool calls`, the `shell` call with its `commands` argument, a blank line,
    `### Tool results`, a blank line, ``- **shell**: `tc_server_1`  `` with `17711` in an
    indented fence, a blank line, and `Fibonacci(22) = 17711`.
  - `llm logs --json`: the row's first tool call has `server_executed` true; its only tool
    result has name `shell`, output `"17711"`, `tool_call_id` `tc_server_1`, `server_executed`
    true and an `id` that is not `None`.
  - `llm logs --short` output contains `shell: 17711`.
- `tests/test_plugins.py`, `test_register_tools`: the three expected `tool_results` JSON strings
  gain `"server_executed": false` between `"exception": null` and `"instance": null`.

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_llm_logs.py tests/test_plugins.py`

## Out of scope
How tool calls are executed or stored, `llm/parts.py`, `llm/models.py`, the `--short` and
`--json` code paths in `logs_list` (they read the new entries as they are), other docs.

## Open questions
None.
