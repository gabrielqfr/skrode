"""The S3 contract: schemas, example records, profiles and worker prompts agree."""

import copy
import json
import re
from pathlib import Path
from string import Template

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
EXAMPLES = SCHEMAS / "examples"
RECORD_BUDGET_CHARS = 6000  # about 1.5k tokens at 4 characters a token

# The guide's profile block, as written there ("Per-family profiles"), split per file.
GUIDE_BONSAI = """
agent:            # tool-using worker turns
  temperature: 1.0
  top_p: 0.95
  top_k: 20
  min_p: 0.05
  max_tokens: 16384
  chat_template_kwargs: {reasoning_effort: medium}   # never "low": behaves like xhigh
oneshot:          # no-tool tasks (docstrings, commit messages)
  temperature: 0.7
  top_p: 0.80
  top_k: 20
  presence_penalty: 1.5
"""
GUIDE_QWEN = """
agent:
  temperature: 0.6
  top_p: 0.95
  top_k: 20
  max_tokens: 32000
  chat_template_kwargs: {enable_thinking: true, preserve_thinking: true}
  reasoning_effort: medium
"""


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load(SCHEMAS / name))


def tool_validators() -> dict[str, Draft202012Validator]:
    tools = load(SCHEMAS / "worker_tools.json")["tools"]
    return {t["function"]["name"]: Draft202012Validator(t["function"]["parameters"]) for t in tools}


@pytest.mark.parametrize(
    "name", ["run_local_subagent.json", "job_record.json", "worker_report.json", "profile.json"]
)
def test_each_schema_is_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(load(SCHEMAS / name))


def test_the_worker_has_exactly_the_five_tools():
    assert sorted(tool_validators()) == ["bash", "edit", "grep", "read", "write"]


def keywords(schema: dict) -> set[str]:
    found = set(schema)
    for name, sub in schema.items():
        children = sub.values() if name == "properties" else [sub]
        found |= {k for child in children if isinstance(child, dict) for k in keywords(child)}
    return found


def test_tool_parameters_stay_plain_for_the_grammar():
    # llama-server compiles tool schemas into a grammar; refs and regexes risk a rejected request.
    for tool in load(SCHEMAS / "worker_tools.json")["tools"]:
        params = tool["function"]["parameters"]
        Draft202012Validator.check_schema(params)
        assert not keywords(params) & {"$ref", "$defs", "pattern"}, tool["function"]["name"]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read", {"path": "router/llm.py"}),
        ("read", {"path": "router/llm.py", "offset": 201, "limit": 200}),
        ("write", {"path": "router/new.py", "content": ""}),
        ("edit", {"path": "router/llm.py", "old": "x = 1", "new": "x = 2"}),
        ("bash", {"cmd": "uv run pytest -q", "timeout_s": 300}),
        ("grep", {"pattern": "def \\w+", "glob": "*.py"}),
    ],
)
def test_valid_tool_calls_pass(tool, args):
    tool_validators()[tool].validate(args)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read", {}),
        ("read", {"path": "a.py", "limit": 501}),
        ("read", {"path": "a.py", "offset": 0}),
        ("write", {"path": "a.py"}),
        ("edit", {"path": "a.py", "old": "", "new": "y"}),
        ("edit", {"path": "a.py", "new": "y"}),
        ("bash", {"cmd": "sleep 400", "timeout_s": 301}),
        ("bash", {"command": "ls"}),
        ("grep", {"pattern": ""}),
    ],
)
def test_invalid_tool_calls_are_schema_errors(tool, args):
    assert not tool_validators()[tool].is_valid(args)


@pytest.mark.parametrize("name", ["record-passed.json", "record-escalate.json"])
def test_example_records_validate_within_budget(name):
    record = load(EXAMPLES / name)
    validator("job_record.json").validate(record)
    assert len(json.dumps(record)) < RECORD_BUDGET_CHARS


def test_the_hand_written_bad_record_is_rejected_for_its_failing_check():
    errors = list(
        validator("job_record.json").iter_errors(
            load(EXAMPLES / "record-invalid-passed-with-failing-check.json")
        )
    )
    assert errors
    assert any("exit_code" in e.absolute_path for e in errors)


DROP = object()


def mutate(record: dict, **changes) -> dict:
    out = copy.deepcopy(record)
    for key, value in changes.items():
        if value is DROP:
            out.pop(key, None)
        else:
            out[key] = value
    return out


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "checks_failed", "detail": "x"},  # passed never carries a reason
        {"branch": DROP},  # required
        {"files_changed": []},  # passed with nothing changed is no_changes
        {"checks": []},  # passed needs at least one check
        {"status": "escalate"},  # no reason
        {"status": "done"},
        {"record_version": 2},
        {"surprise": True},
        {"files_changed": [{"path": "/etc/passwd", "added": 1, "removed": 0}]},
        {"files_changed": [{"path": "router/../../x", "added": 1, "removed": 0}]},
        {"concerns": ["a", "b", "c", "d"]},
        {"summary": "word " * 250},
    ],
)
def test_broken_passed_records_are_rejected(changes):
    record = mutate(load(EXAMPLES / "record-passed.json"), **changes)
    assert not validator("job_record.json").is_valid(record)


def test_a_passed_record_with_a_null_branch_is_rejected():
    record = mutate(load(EXAMPLES / "record-passed.json"), branch=None)
    assert not validator("job_record.json").is_valid(record)


@pytest.mark.parametrize(
    ("status", "reason", "ok"),
    [
        ("escalate", "must_not_touch", True),
        ("escalate", "no_changes", True),
        ("escalate", "endpoint", False),
        ("failed", "endpoint", True),
        ("failed", "cancelled", True),
        ("failed", "checks_failed", False),
        ("failed", "made_up", False),
    ],
)
def test_reasons_belong_to_their_status(status, reason, ok):
    record = mutate(load(EXAMPLES / "record-escalate.json"), status=status, reason=reason)
    assert validator("job_record.json").is_valid(record) is ok


def test_a_failed_job_may_have_no_branch_and_no_checks():
    record = mutate(
        load(EXAMPLES / "record-escalate.json"),
        status="failed",
        reason="endpoint",
        detail="127.0.0.1:8080 refused the connection 3 times",
        attempts=0,
        turns=0,
        files_changed=[],
        diffstat="",
        checks=[],
        summary="",
        branch=None,
    )
    validator("job_record.json").validate(record)


def test_example_arguments_validate():
    validator("run_local_subagent.json").validate(load(EXAMPLES / "args.json"))


@pytest.mark.parametrize(
    "changes",
    [
        {"files": ["/abs/path.py"]},
        {"files": ["../outside.py"]},
        {"files": ["a/../../b.py"]},
        {"files": []},
        {"acceptance_criteria": {"commands": []}},
        {"acceptance_criteria": {"must_not_touch": ["tests/**"]}},
        {"max_retries": 4},
        {"max_turns": 0},
        {"isolation": "container"},
        {"repo": "relative/repo"},
        {"model": "claude-opus"},  # no per-job model choice, so no failover by argument
        {"task": "fix it"},
    ],
)
def test_bad_arguments_are_rejected(changes):
    args = mutate(load(EXAMPLES / "args.json"), **changes)
    assert not validator("run_local_subagent.json").is_valid(args)


def test_dotted_names_are_not_mistaken_for_parent_dirs():
    args = mutate(load(EXAMPLES / "args.json"), files=["src/a..b.py", ".github/x.yml"])
    validator("run_local_subagent.json").validate(args)


@pytest.mark.parametrize("text", [GUIDE_BONSAI, GUIDE_QWEN])
def test_the_guide_profiles_validate(text):
    validator("profile.json").validate(yaml.safe_load(text))


@pytest.mark.parametrize(
    "profile",
    [
        {"oneshot": {"temperature": 0.7}},  # agent is required
        {"agent": {"temperature": "hot"}},
        {"agent": {"reasoning_effort": "max"}},
        {"agent": {"max_tokens": 100}},
        {"agent": {}, "endpoint": "http://example.com"},
        {"agent": {}, "sources": ["http://insecure.example"]},
    ],
)
def test_bad_profiles_are_rejected(profile):
    assert not validator("profile.json").is_valid(profile)


@pytest.mark.parametrize(
    ("report", "ok"),
    [
        ({"outcome": "done", "summary": "Added slugify.", "concerns": []}, True),
        ({"outcome": "blocked", "summary": "Which module?", "concerns": ["scope"]}, True),
        ({"outcome": "passed", "summary": "x", "concerns": []}, False),
        ({"outcome": "done", "summary": "", "concerns": []}, False),
        ({"outcome": "done", "summary": "x"}, False),
    ],
)
def test_worker_reports(report, ok):
    assert validator("worker_report.json").is_valid(report) is ok


def prompt_sections() -> dict[str, str]:
    text = (ROOT / "prompts" / "worker.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (\S+)\n", text, flags=re.MULTILINE)
    return {name: body.strip() for name, body in zip(parts[1::2], parts[2::2], strict=True)}


PLACEHOLDERS = {
    "system": set(),
    "task": {"task", "files", "must_not_touch", "commands", "max_turns"},
    "retry": {"attempt", "max_attempts", "command", "exit_code", "tail", "max_turns"},
    "correction.invalid": {"tool", "error"},
    "correction.unknown_tool": {"tool"},
    "correction.narrated": set(),
    "correction.length": set(),
    "correction.outside_files": {"path", "files"},
    "report": set(),
}


def test_the_prompt_file_has_exactly_the_expected_sections():
    assert set(prompt_sections()) == set(PLACEHOLDERS)


@pytest.mark.parametrize("name", sorted(PLACEHOLDERS))
def test_each_section_uses_exactly_its_placeholders(name):
    body = prompt_sections()[name]
    assert set(Template(body).get_identifiers()) == PLACEHOLDERS[name]
    assert Template(body).substitute(dict.fromkeys(PLACEHOLDERS[name], "X"))


def test_the_system_prompt_stays_under_budget():
    assert len(prompt_sections()["system"]) < 6000
