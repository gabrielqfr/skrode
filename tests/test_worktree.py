"""Tests for skrode.worktree: the git helpers one job uses."""

import os
import subprocess
from pathlib import Path

import pytest

import skrode.worktree as worktree
from skrode.worktree import (
    WorktreeError,
    _failure,
    changed_paths,
    commit_all,
    create,
    default_must_not_touch,
    diff_stat,
    git,
    is_test_path,
    remove,
    resolve_base,
)


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch) -> tuple[Path, str]:
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    root.mkdir()
    sh(root, "init", "-q")
    sh(root, "config", "user.name", "t")
    sh(root, "config", "user.email", "t@t.t")
    (root / "a.py").write_text("a\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("t\n")
    (root / ".gitignore").write_text("*.log\n")
    (root / "b.bin").write_bytes(b"\x00\x01")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    return root, sh(root, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/x.py", True),
        ("pkg/test/x.py", True),
        ("test_a.py", True),
        ("pkg/test_a.py", True),
        ("a_test.go", True),
        ("a.test.ts", True),
        ("a.spec.js", True),
        ("testing/x.py", False),
        ("contest.py", False),
        ("src/attest.py", False),
        ("tests", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) == expected


def test_resolve_base_returns_full_hash(repo: tuple[Path, str]) -> None:
    root, base = repo
    resolved = resolve_base(root, "HEAD")
    assert resolved == base
    assert len(resolved) == 40


def test_resolve_base_rejects(tmp_path, repo: tuple[Path, str]) -> None:
    root, _ = repo
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError, match=r"^not a directory"):
        resolve_base(root / "missing", "HEAD")
    with pytest.raises(WorktreeError, match=r"^not a git repo"):
        resolve_base(plain, "HEAD")
    with pytest.raises(WorktreeError, match=r"^bad base"):
        resolve_base(root, "-x")
    with pytest.raises(WorktreeError, match=r"^base does not resolve"):
        resolve_base(root, "nope")


def test_default_must_not_touch(repo: tuple[Path, str]) -> None:
    root, base = repo
    assert default_must_not_touch(root, base, ["a.py"]) == ["tests/test_a.py"]
    assert default_must_not_touch(root, base, ["tests/*"]) == []
    (root / "tests" / "test_new.py").write_text("n\n")
    assert default_must_not_touch(root, base, ["a.py"]) == ["tests/test_a.py"]
    (root / "tests" / "test_late.py").write_text("l\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "late")
    assert default_must_not_touch(root, base, ["a.py"]) == ["tests/test_a.py"]


def test_create_branch_at_base(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    expected = tmp_path / "wt" / "j-abc123"
    assert create(root, base, "j-abc123", tmp_path / "wt") == expected
    assert sh(expected, "rev-parse", "HEAD") == base
    assert sh(expected, "rev-parse", "--abbrev-ref", "HEAD") == "local-agent/j-abc123"
    with pytest.raises(WorktreeError):
        create(root, base, "j-abc123", tmp_path / "wt")


def test_changed_paths(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-chg1", tmp_path / "wt")
    (wt / "a.py").write_bytes((wt / "a.py").read_bytes() + b"b\n")
    (wt / "tests" / "test_a.py").unlink()
    (wt / "new" / "d").mkdir(parents=True)
    (wt / "new" / "d" / "x.py").write_text("x\n")
    (wt / "x.log").write_text("x\n")
    assert changed_paths(wt, base) == ["a.py", "new/d/x.py", "tests/test_a.py"]


def test_changed_paths_sees_worker_commit(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-chg2", tmp_path / "wt")
    (wt / "a.py").write_bytes((wt / "a.py").read_bytes() + b"b\n")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=w",
            "-c",
            "user.email=w@x",
            "commit",
            "-qam",
            "w",
        ],
        cwd=wt,
        check=True,
        capture_output=True,
    )
    assert changed_paths(wt, base) == ["a.py"]


def test_commit_all(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-cm1", tmp_path / "wt")
    assert commit_all(wt, base, "noop") is False
    assert sh(wt, "rev-parse", "HEAD") == base
    (wt / "new.py").write_text("n\n")
    assert commit_all(wt, base, "added new.py") is True
    assert sh(wt, "log", "-1", "--format=%an <%ae>%n%s") == (
        "skrode <skrode@localhost>\nadded new.py"
    )
    assert sh(wt, "status", "--porcelain") == ""
    assert commit_all(wt, base, "again") is True
    assert sh(wt, "log", "-1", "--format=%s") == "added new.py"


def test_hooks_do_not_run(tmp_path, repo: tuple[Path, str], monkeypatch) -> None:
    root, base = repo
    monkeypatch.setenv("MARKER_DIR", str(tmp_path))
    for name in ("post-checkout", "post-commit"):
        hook = root / ".git" / "hooks" / name
        hook.write_text('#!/bin/sh\ntouch "$MARKER_DIR/$(basename "$0")"\n')
        hook.chmod(0o755)
    wt = create(root, base, "j-hooks1", tmp_path / "wt")
    (wt / "hook.py").write_text("h\n")
    commit_all(wt, base, "hooks off")
    assert not (tmp_path / "post-checkout").exists()
    assert not (tmp_path / "post-commit").exists()


def test_git_hides_anthropic_env(repo: tuple[Path, str], monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    real = subprocess.run
    hidden: list[dict] = []

    def wrapper(*args, **kwargs):
        env = kwargs.get("env")
        if isinstance(env, dict):
            hidden.append(dict(env))
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", wrapper)
    root, _ = repo
    git(root, "status")
    recorded = hidden[-1]
    assert "ANTHROPIC_API_KEY" not in recorded
    assert "PATH" in recorded


def test_commit_all_raises_on_bad_diff_exit(repo: tuple[Path, str], monkeypatch) -> None:
    real = worktree._run

    def wrapper(cwd: Path, *args: str):
        if args[:2] == ("diff", "--cached"):
            return subprocess.CompletedProcess(
                args=[],
                returncode=128,
                stdout="",
                stderr="fatal: boom\n",
            )
        return real(cwd, *args)

    monkeypatch.setattr(worktree, "_run", wrapper)
    root, base = repo
    with pytest.raises(WorktreeError, match=r"^git diff: fatal: boom$"):
        commit_all(root, base, "x")


def test_diff_stat(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-ds1", tmp_path / "wt")
    (wt / "a.py").write_bytes((wt / "a.py").read_bytes() + b"b\n")
    (wt / "b.bin").write_bytes(b"\x00\x02")
    (wt / "tests" / "test_a.py").unlink()
    commit_all(wt, base, "stats")
    files_changed, diffstat = diff_stat(wt, base)
    assert files_changed == [
        {"path": "a.py", "added": 1, "removed": 0},
        {"path": "b.bin", "added": 0, "removed": 0},
        {"path": "tests/test_a.py", "added": 0, "removed": 1},
    ]
    assert diffstat == "3 files changed, 1 insertion(+), 1 deletion(-)"


def test_diff_stat_empty(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-ds2", tmp_path / "wt")
    assert commit_all(wt, base, "noop") is False
    assert diff_stat(wt, base) == ([], "")


def test_diff_stat_tab_in_path(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-tab1", tmp_path / "wt")
    (wt / "a\tb.py").write_text("z\n")
    commit_all(wt, base, "tab in path")
    files_changed, _ = diff_stat(wt, base)
    assert {"path": "a\tb.py", "added": 1, "removed": 0} in files_changed


def test_changed_paths_non_utf8_name(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt = create(root, base, "j-bad1", tmp_path / "wt")
    with open(os.fsencode(wt) + b"/caf\xe9.py", "wb") as handle:
        handle.write(b"x\n")
    assert changed_paths(wt, base) == ["caf\udce9.py"]


def test_failure_returns_error() -> None:
    error = _failure(
        ("x",),
        subprocess.CompletedProcess([], 3, "", "e\n"),
    )
    assert isinstance(error, WorktreeError)
    assert str(error) == "git x: e"
    empty = subprocess.CompletedProcess([], 3, "", "")
    assert str(_failure(("x",), empty)) == "git x: exit 3"


def test_remove(tmp_path, repo: tuple[Path, str]) -> None:
    root, base = repo
    wt_keep = create(root, base, "j-abc123", tmp_path / "wt")
    remove(root, wt_keep, "j-abc123", keep_branch=True)
    assert not wt_keep.exists()
    assert len(sh(root, "rev-parse", "--verify", "local-agent/j-abc123")) == 40
    wt_drop = create(root, base, "j-abc456", tmp_path / "wt2")
    remove(root, wt_drop, "j-abc456", keep_branch=False)
    assert sh(root, "branch", "--list", "local-agent/j-abc456") == ""


def test_discard_changes_keeps_ignored_files(repo):
    root, _ = repo
    (root / "a.py").write_text("changed\n")
    (root / "new.txt").write_text("n\n")
    (root / "sub").mkdir()
    (root / "sub" / "f.txt").write_text("f\n")
    (root / "keep.log").write_text("k\n")
    worktree.discard_changes(root)
    assert (root / "a.py").read_text() == "a\n"
    assert not (root / "new.txt").exists()
    assert not (root / "sub").exists()
    assert (root / "keep.log").read_text() == "k\n"
