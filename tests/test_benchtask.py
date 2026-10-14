"""Tests for router.benchtask: eval task folders to run_local_subagent args."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from router.benchtask import TESTS_NOTE, TaskError, hidden_paths, main, task_args

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "run_local_subagent.json").read_text(encoding="utf-8"))
SCHEMA_VALIDATOR = Draft202012Validator(SCHEMA)

SPEC = (
    "## Goal\n"
    "Make `add_one` add one to an integer.\n"
    "\n"
    "## Files to touch\n"
    "- `src/app.py`\n"
    "- `./tests/test_app.py`\n"
    "\n"
    "## Tests to add\n"
    "- `tests/test_app.py` checks that add_one returns the input plus one.\n"
    "\n"
    "## Commands that must pass\n"
    "- `uv run pytest -q tests/test_app.py`\n"
    "- `uv run ruff check .`\n"
    "\n"
    "## Out of scope\n"
    "No other files, no new dependencies.\n"
)

SPEC_6_COMMANDS = (
    "## Goal\n"
    "Do it.\n"
    "\n"
    "## Files to touch\n"
    "- `src/app.py`\n"
    "\n"
    "## Commands that must pass\n"
    "- `uv run pytest -q tests/test_app.py`\n"
    "- `uv run ruff check .`\n"
    "- `uv run ruff format src/`\n"
    "- `uv run python -m py_compile src/app.py`\n"
    "- `uv run mypy src/`\n"
    "- `uv run ruff check tests/`\n"
)

SPEC_ONLY_HIDDEN = (
    "## Goal\n"
    "Do it.\n"
    "\n"
    "## Files to touch\n"
    "- `./tests/test_app.py`\n"
    "\n"
    "## Commands that must pass\n"
    "- `uv run pytest -q tests/test_app.py`\n"
)


def make_task(root: Path, name: str, spec: str, hidden: dict[str, str] | None = None, **extra):
    folder = root / name
    folder.mkdir()
    (folder / "spec.md").write_text(spec, encoding="utf-8")
    if hidden:
        hidden_dir = folder / "hidden"
        for rel, text in hidden.items():
            path = hidden_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    for key, value in extra.items():
        filename = {"files_txt": "files.txt", "commands_txt": "commands.txt", "kind": "kind"}[key]
        (folder / filename).write_text(value, encoding="utf-8")
    return folder


def test_files_and_commands_come_from_the_spec(tmp_path):
    task = make_task(tmp_path, "t", SPEC, hidden={"tests/test_app.py": "x"})
    args = task_args(task)
    assert args["files"] == ["src/app.py"]
    assert args["acceptance_criteria"]["commands"] == [
        "uv run pytest -q tests/test_app.py",
        "uv run ruff check .",
    ]
    assert args["acceptance_criteria"]["must_not_touch"] == ["tests/test_app.py"]


def test_hidden_paths_are_nested_relative_and_sorted(tmp_path):
    task = make_task(
        tmp_path,
        "t",
        SPEC,
        hidden={"web/src/a.test.ts": "x", "tests/test_b.py": "y"},
    )
    assert hidden_paths(task) == ["tests/test_b.py", "web/src/a.test.ts"]


def test_files_txt_and_commands_txt_override_the_spec(tmp_path):
    commands_txt = (
        "# only these checks\n\nuv run pytest -q tests/test_app.py\n\nuv run ruff check .\n"
    )
    task = make_task(
        tmp_path,
        "t",
        SPEC,
        hidden={"tests/test_app.py": "x"},
        files_txt="# only the listed files\n\nsrc/app.py\n\nother.py\n",
        commands_txt=commands_txt,
    )
    args = task_args(task)
    assert args["files"] == ["src/app.py", "other.py"]
    assert args["acceptance_criteria"]["commands"] == [
        "uv run pytest -q tests/test_app.py",
        "uv run ruff check .",
    ]


def test_task_text_protects_the_hidden_tests(tmp_path):
    task = make_task(tmp_path, "t", SPEC, hidden={"tests/test_app.py": "x"})
    task_text = task_args(task)["task"]
    assert "- `./tests/test_app.py`" not in task_text
    assert "- `src/app.py`" in task_text
    assert "## Goal" in task_text
    assert "## Out of scope" in task_text
    assert "checks that add_one returns the input plus one" not in task_text
    assert TESTS_NOTE + "\n- `tests/test_app.py`" in task_text


def test_without_hidden_the_spec_is_sent_as_written(tmp_path):
    task = make_task(tmp_path, "t", SPEC)
    args = task_args(task)
    assert args["task"] == SPEC
    assert args["acceptance_criteria"]["must_not_touch"] == []


def test_more_than_five_commands_is_an_error(tmp_path):
    task = make_task(tmp_path, "t", SPEC_6_COMMANDS)
    with pytest.raises(TaskError) as exc:
        task_args(task)
    assert "the limit is 5" in str(exc.value)


def test_args_validate_against_the_schema(tmp_path):
    task = make_task(tmp_path, "t", SPEC, hidden={"tests/test_app.py": "x"})
    args = task_args(task)
    SCHEMA_VALIDATOR.validate(args)


def test_no_files_left_is_an_error(tmp_path):
    task = make_task(tmp_path, "t", SPEC_ONLY_HIDDEN, hidden={"tests/test_app.py": "x"})
    with pytest.raises(TaskError):
        task_args(task)


def test_main_writes_args_and_skips_planning_and_template(tmp_path, capsys):
    good = make_task(tmp_path, "good", SPEC, hidden={"tests/test_app.py": "x"})
    template = make_task(tmp_path, "_template", SPEC)
    plan = make_task(tmp_path, "plan-x", SPEC, kind="planning")
    bad = make_task(tmp_path, "bad", SPEC_6_COMMANDS)
    rc = main([good, template, plan, bad])
    assert rc == 1
    assert json.loads((good / "args.json").read_text(encoding="utf-8")) == task_args(good)
    assert not (template / "args.json").is_file()
    assert not (plan / "args.json").is_file()
    assert not (bad / "args.json").is_file()
    out = capsys.readouterr()
    assert "good: ok (1 files, 2 commands, 1 protected)" in out.out
    assert "plan-x: skipped (planning)" in out.out
    assert "bad: 6 commands" in out.err
