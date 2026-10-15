"""Tests for router.tools: the five worker tools and their guards."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from router.tools import (
    NO_NETWORK,
    TOOL_SPECS,
    Outcome,
    Workspace,
    matches,
)

ROOT = Path(__file__).resolve().parents[1]
_TOOLS_SCHEMA = json.loads((ROOT / "schemas" / "worker_tools.json").read_text(encoding="utf-8"))

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t.t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t.t",
    "GIT_TERMINAL_PROMPT": "0",
}


def test_tool_specs_are_the_contract():
    assert _TOOLS_SCHEMA["tools"] == TOOL_SPECS


@pytest.mark.parametrize(
    ("path", "patterns", "expected"),
    [
        ("a.py", ["a.py"], True),
        ("src/x/a.py", ["src/*.py"], True),
        ("src/a.py", ["src/"], True),
        ("src/a.py", ["./src/a.py"], True),
        ("b.py", ["a.py", "src/"], False),
    ],
)
def test_matches(path, patterns, expected):
    assert matches(path, patterns) is expected


def test_unknown_tool(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("delete", "{}")
    assert r.outcome == Outcome.UNKNOWN_TOOL
    assert r.text == "delete"


def test_bad_json_is_invalid(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("read", "{not json")
    assert r.outcome == Outcome.INVALID
    assert r.text == "arguments are not valid JSON"


def test_non_object_is_invalid(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("read", "[1]")
    assert r.outcome == Outcome.INVALID
    assert r.text == "arguments must be a JSON object"


@pytest.mark.parametrize(
    "args",
    [
        {"path": "a.py", "limit": 0},
        {"path": "a.py", "colour": 1},
        {},
    ],
)
def test_schema_errors_are_invalid(tmp_path, args):
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("read", args)
    assert r.outcome == Outcome.INVALID
    assert r.text


def test_read_pages(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    r = ws.run("read", {"path": "f.txt", "offset": 2, "limit": 2})
    assert r.outcome == Outcome.OK
    assert r.text == "[lines 2-3 of 5]\n2\tb\n3\tc"
    r2 = ws.run("read", {"path": "f.txt"})
    assert r2.outcome == Outcome.OK
    assert r2.text == "[lines 1-5 of 5]\n1\ta\n2\tb\n3\tc\n4\td\n5\te"


def test_read_empty_file(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    (tmp_path / "f.txt").write_text("", encoding="utf-8")
    r = ws.run("read", {"path": "f.txt"})
    assert r.outcome == Outcome.OK
    assert r.text == "[lines 0-0 of 0]"


def test_read_offset_past_end(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    r = ws.run("read", {"path": "f.txt", "offset": 9})
    assert r.outcome == Outcome.ERROR
    assert r.text == "offset 9 is past the end of f.txt (5 lines)"


def test_read_missing_file(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("read", {"path": "nope.py"})
    assert r.outcome == Outcome.ERROR
    assert r.text == "not a file: nope.py"


def test_read_cuts_long_lines(tmp_path):
    ws = Workspace(root=tmp_path, files=[])
    (tmp_path / "f.txt").write_text("x" * 2500, encoding="utf-8")
    r = ws.run("read", {"path": "f.txt"})
    assert r.outcome == Outcome.OK
    assert r.text.splitlines()[1] == "1\t" + "x" * 2000 + " [cut]"


@pytest.mark.parametrize("path", ["../x.txt", "/etc/hostname"])
def test_paths_outside_are_refused(tmp_path, path):
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("read", {"path": path})
    assert r.outcome == Outcome.ERROR
    assert r.text == f"{path} is outside the worktree"


def test_paths_outside_are_refused_symlink(tmp_path):
    (tmp_path / "outside.txt").write_text("x\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace(root=root, files=[])
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    r = ws.run("read", {"path": "link.txt"})
    assert r.outcome == Outcome.ERROR
    assert r.text == "link.txt is outside the worktree"


def test_write_creates_parents(tmp_path):
    ws = Workspace(root=tmp_path, files=["pkg/*"])
    r = ws.run("write", {"path": "pkg/sub/new.py", "content": "a\nb\n"})
    assert r.outcome == Outcome.OK
    assert r.text == "wrote pkg/sub/new.py (2 lines)"
    assert (tmp_path / "pkg/sub/new.py").read_text(encoding="utf-8") == "a\nb\n"


def test_write_normalises_the_path(tmp_path):
    ws = Workspace(root=tmp_path, files=["src/a.py"])
    r = ws.run("write", {"path": "./src/../src/a.py", "content": "x\n"})
    assert r.outcome == Outcome.OK
    assert r.text == "wrote src/a.py (1 lines)"
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "x\n"


def test_write_outside_files(tmp_path):
    ws = Workspace(root=tmp_path, files=["a.py"])
    r = ws.run("write", {"path": "other.py", "content": "x"})
    assert r.outcome == Outcome.OUTSIDE_FILES
    assert r.text == "other.py"
    assert not (tmp_path / "other.py").is_file()


def test_write_protected_wins(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text("orig\n", encoding="utf-8")
    ws = Workspace(root=tmp_path, files=["*"], must_not_touch=["tests/"])
    r = ws.run("write", {"path": "tests/test_a.py", "content": "hack"})
    assert r.outcome == Outcome.MUST_NOT_TOUCH
    assert r.text == "tests/test_a.py"
    r2 = ws.run("edit", {"path": "tests/test_a.py", "old": "orig", "new": "hack"})
    assert r2.outcome == Outcome.MUST_NOT_TOUCH
    assert r2.text == "tests/test_a.py"
    assert (tmp_path / "tests/test_a.py").read_text(encoding="utf-8") == "orig\n"


def test_edit_replaces_one_match(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    ws = Workspace(root=tmp_path, files=["a.py"])
    r = ws.run("edit", {"path": "a.py", "old": "return 1", "new": "return 2"})
    assert r.outcome == Outcome.OK
    assert r.text == "edited a.py"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_edit_no_match(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    ws = Workspace(root=tmp_path, files=["a.py"])
    r = ws.run("edit", {"path": "a.py", "old": "zzz", "new": "y"})
    assert r.outcome == Outcome.ERROR
    assert r.text == "old text not found in a.py"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "def f():\n    return 1\n"


def test_edit_two_matches(tmp_path):
    (tmp_path / "a.py").write_text("a\nb\na\n", encoding="utf-8")
    ws = Workspace(root=tmp_path, files=["a.py"])
    r = ws.run("edit", {"path": "a.py", "old": "a\n", "new": "y"})
    assert r.outcome == Outcome.ERROR
    assert r.text == "old text matches 2 places in a.py; include more surrounding lines"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a\nb\na\n"


def test_bash_reply_format(tmp_path):
    ws = Workspace(root=tmp_path, files=[], isolate_network=False)
    r = ws.run("bash", {"cmd": "echo hi; echo err >&2; exit 3"})
    assert r.outcome == Outcome.OK
    assert r.text == "exit 3\n--- stdout ---\nhi\n--- stderr ---\nerr"
    r2 = ws.run("bash", {"cmd": "true"})
    assert r2.outcome == Outcome.OK
    assert r2.text == "exit 0"


def test_bash_runs_in_the_root(tmp_path):
    ws = Workspace(root=tmp_path, files=[], isolate_network=False)
    r = ws.run("bash", {"cmd": "pwd"})
    assert r.outcome == Outcome.OK
    assert r.text.splitlines()[-1] == str(tmp_path.resolve())


def test_bash_timeout_kills_the_group(tmp_path):
    ws = Workspace(root=tmp_path, files=[], isolate_network=False)
    start = time.monotonic()
    r = ws.run("bash", {"cmd": "sleep 30 & sleep 30", "timeout_s": 1})
    elapsed = time.monotonic() - start
    assert r.outcome == Outcome.OK
    assert r.text.startswith("timed out after 1 s")
    assert elapsed < 10


def test_bash_cuts_long_output(tmp_path):
    ws = Workspace(root=tmp_path, files=[], isolate_network=False)
    r = ws.run("bash", {"cmd": "head -c 20000 /dev/zero | tr '\\0' x"})
    assert r.outcome == Outcome.OK
    assert "[cut: last 12000 of 20000 characters]" in r.text


def test_bash_hides_anthropic_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    ws = Workspace(root=tmp_path, files=[], isolate_network=False)
    r = ws.run("bash", {"cmd": "env"})
    assert r.outcome == Outcome.OK
    assert "ANTHROPIC_API_KEY" not in r.text
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in r.text
    assert "PATH=" in r.text


def _unshare_ok() -> bool:
    try:
        result = subprocess.run((*NO_NETWORK, "true"), capture_output=True, timeout=10)
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


@pytest.mark.skipif(not _unshare_ok(), reason="unshare not available")
def test_bash_has_no_network(tmp_path):
    ws = Workspace(root=tmp_path, files=[], isolate_network=True)
    r = ws.run("bash", {"cmd": "tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' '"})
    assert r.outcome == Outcome.OK
    assert r.text == "exit 0\n--- stdout ---\nlo"


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, env=GIT_ENV, check=True)


def _commit(root: Path, *paths) -> None:
    subprocess.run(["git", "add", *paths], cwd=root, env=GIT_ENV, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "t"], cwd=root, env=GIT_ENV, check=True)


def test_grep_tracked_and_new_not_ignored(tmp_path):
    (tmp_path / "a.py").write_text("def foo\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git_init(tmp_path)
    _commit(tmp_path, "a.py")
    (tmp_path / "new.py").write_text("def foo\n", encoding="utf-8")
    (tmp_path / "x.log").write_text("def foo\n", encoding="utf-8")
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("grep", {"pattern": "foo"})
    assert r.outcome == Outcome.OK
    lines = r.text.splitlines()
    assert any(line.startswith("a.py:1:") for line in lines)
    assert any(line.startswith("new.py:1:") for line in lines)
    assert not any(line.startswith("x.log") for line in lines)


def test_grep_path_and_glob(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo\n", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("def foo\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("def foo\n", encoding="utf-8")
    _git_init(tmp_path)
    _commit(tmp_path, "src", "c.py")
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("grep", {"pattern": "def foo"})
    lines = r.text.splitlines()
    assert any(line.startswith("src/a.py:1:") for line in lines)
    assert any(line.startswith("src/b.txt:1:") for line in lines)
    assert any(line.startswith("c.py:1:") for line in lines)
    r2 = ws.run("grep", {"pattern": "def foo", "path": "src", "glob": "*.py"})
    lines2 = r2.text.splitlines()
    assert any(line.startswith("src/a.py:1:") for line in lines2)
    assert not any(line.startswith("src/b.txt") for line in lines2)
    assert not any(line.startswith("c.py") for line in lines2)


def test_grep_no_match(tmp_path):
    (tmp_path / "a.py").write_text("def bar\n", encoding="utf-8")
    _git_init(tmp_path)
    _commit(tmp_path, "a.py")
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("grep", {"pattern": "nomatchxyz"})
    assert r.outcome == Outcome.OK
    assert r.text == "no matches"


def test_grep_bad_regex(tmp_path):
    (tmp_path / "a.py").write_text("def foo\n", encoding="utf-8")
    _git_init(tmp_path)
    _commit(tmp_path, "a.py")
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("grep", {"pattern": "("})
    assert r.outcome == Outcome.ERROR
    assert r.text.startswith("grep failed:")


def test_grep_caps_matches(tmp_path):
    lines = [f"def foo {i}" for i in range(150)]
    (tmp_path / "a.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git_init(tmp_path)
    _commit(tmp_path, "a.py")
    ws = Workspace(root=tmp_path, files=[])
    r = ws.run("grep", {"pattern": "def foo"})
    lines_out = r.text.splitlines()
    assert len(lines_out) == 101
    assert lines_out[-1] == "[50 more matching lines]"
