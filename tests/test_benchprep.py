import json
import subprocess
from pathlib import Path

import pytest

from skrode.benchprep import DEFAULT_SETUP, PrepError, bench_base, main, source_for
from skrode.config import load_config

SPEC = """# Fix a

## Goal
Change a.

## Files to touch
- `a.py`
- `tests/test_a.py`

## Tests to add
- `tests/test_a.py`

## Commands that must pass
- `.venv/bin/python -m pytest -q tests/test_a.py`
"""


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def source(tmp_path, monkeypatch) -> tuple[Path, str, str]:
    """An upstream repo: the parent, then the merged commit with new tests on a side branch."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "upstream" / "o" / "n"
    root.mkdir(parents=True)
    sh(root, "init", "-q", "-b", "main")
    sh(root, "config", "user.name", "t")
    sh(root, "config", "user.email", "t@t.t")
    (root / "a.py").write_text("a\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("old\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    parent = sh(root, "rev-parse", "HEAD")
    sh(root, "checkout", "-q", "-b", "stable")
    (root / "a.py").write_text("merged\n")
    (root / "tests" / "test_a.py").write_text("new\n")
    (root / "tests" / "run.sh").write_text("#!/bin/sh\n")
    (root / "tests" / "run.sh").chmod(0o755)
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "merged")
    commit = sh(root, "rev-parse", "HEAD")
    sh(root, "checkout", "-q", "main")
    return root, parent, commit


HIDDEN = "tests/test_a.py\n"


def make_task(
    tasks: Path, name: str, root: Path, parent: str, commit: str, **extra: str
) -> Path:
    folder = tasks / name
    folder.mkdir(parents=True)
    (folder / "spec.md").write_text(SPEC, encoding="utf-8")
    (folder / "repo").write_text(f"{root}\n")
    (folder / "parent").write_text(parent + "\n")
    (folder / "commit").write_text(commit + "\n")
    (folder / "hidden.txt").write_text(extra.pop("hidden_txt", HIDDEN))
    for filename, text in extra.items():
        (folder / filename.replace("_", ".")).write_text(text, encoding="utf-8")
    return folder


def run_main(tmp_path: Path, *ids: str) -> int:
    return main(
        [
            "--tasks",
            str(tmp_path / "tasks"),
            "--sources",
            str(tmp_path / "sources"),
            "--clones",
            str(tmp_path / "clones"),
            "--out",
            str(tmp_path / "out"),
            *ids,
        ]
    )


def test_base_is_the_parent_plus_the_hidden_tests_from_the_commit(source):
    root, parent, commit = source
    base = bench_base(root, parent, commit, ["tests/run.sh", "tests/test_a.py"])
    assert sh(root, "rev-parse", f"{base}^") == parent
    assert sh(root, "show", f"{base}:tests/test_a.py") == "new"
    assert sh(root, "show", f"{base}:a.py") == "a"
    assert sh(root, "ls-tree", base, "tests/run.sh").startswith("100755 ")
    assert (
        sh(root, "show", "-s", "--format=%an <%ae>|%s", base)
        == "bench <bench@localhost>|bench base"
    )


def test_base_is_the_same_commit_every_time(source):
    root, parent, commit = source
    hidden = ["tests/test_a.py"]
    assert bench_base(root, parent, commit, hidden) == bench_base(root, parent, commit, hidden)


def test_a_missing_parent_or_commit_is_an_error(source):
    root, parent, commit = source
    with pytest.raises(PrepError, match="parent 000000000000 is not in"):
        bench_base(root, "0" * 40, commit, [])
    with pytest.raises(PrepError, match="commit 000000000000 is not in"):
        bench_base(root, parent, "0" * 40, [])


def test_a_hidden_path_missing_from_the_commit_is_an_error(source):
    root, parent, commit = source
    with pytest.raises(PrepError, match=r"tests/nope\.py is not in commit"):
        bench_base(root, parent, commit, ["tests/nope.py"])


def test_source_comes_from_the_repo_url(tmp_path):
    task = tmp_path / "t1"
    task.mkdir()
    with pytest.raises(PrepError, match="no repo"):
        source_for(task, tmp_path / "sources")
    (task / "repo").write_text("https://github.com/simonw/sqlite-utils.git\n")
    assert source_for(task, tmp_path / "sources") == (
        "https://github.com/simonw/sqlite-utils.git",
        tmp_path / "sources" / "simonw-sqlite-utils",
    )


def test_main_writes_args_config_manifest_and_hidden_tests(tmp_path, source):
    root, parent, commit = source
    tasks = tmp_path / "tasks"
    with_setup = make_task(tasks, "t1", root, parent, commit, setup_sh="echo hi\n")
    make_task(tasks, "t2", root, parent, commit)
    assert run_main(tmp_path) == 0

    assert (tmp_path / "sources" / "o-n" / "HEAD").is_file()
    clone1 = (tmp_path / "clones" / "t1").resolve()
    args = json.loads((tmp_path / "out" / "t1" / "args.json").read_text())
    assert args["repo"] == str(clone1)
    assert sh(clone1, "rev-parse", "bench/t1") == args["base"]
    assert sh(clone1, "rev-parse", f"{args['base']}^") == parent
    assert args["acceptance_criteria"]["must_not_touch"] == ["tests/test_a.py"]
    assert args["files"] == ["a.py"]

    hidden1 = (tmp_path / "out" / "t1" / "hidden").resolve()
    assert [p.relative_to(hidden1).as_posix() for p in hidden1.rglob("*") if p.is_file()] == [
        "tests/test_a.py"
    ]
    assert (hidden1 / "tests" / "test_a.py").read_text() == "new\n"
    assert not (clone1 / ".git" / "index").exists()

    config = load_config(tmp_path / "out" / "config.toml", required=True)
    setup1 = f"bash {(with_setup / 'setup.sh').resolve()}"
    assert config == {
        clone1: (setup1,),
        (tmp_path / "clones" / "t2").resolve(): DEFAULT_SETUP,
    }

    rows = (tmp_path / "out" / "tasks.tsv").read_text().splitlines()
    assert rows[0] == "id\ttask_dir\trepo\tbase\thidden"
    assert rows[1] == f"t1\t{with_setup.resolve()}\t{clone1}\t{args['base']}\t{hidden1}"
    assert [row.split("\t")[0] for row in rows[1:]] == ["t1", "t2"]


def test_the_upstream_repo_is_left_untouched(tmp_path, source):
    root, parent, commit = source
    make_task(tmp_path / "tasks", "t1", root, parent, commit)
    refs_before = sh(root, "for-each-ref")
    assert run_main(tmp_path) == 0
    base = json.loads((tmp_path / "out" / "t1" / "args.json").read_text())["base"]
    assert sh(root, "for-each-ref") == refs_before
    missing = subprocess.run(["git", "cat-file", "-e", base], cwd=root, capture_output=True)
    assert missing.returncode != 0


def test_a_rerun_reuses_the_clone_and_gives_the_same_base(tmp_path, source):
    root, parent, commit = source
    make_task(tmp_path / "tasks", "t1", root, parent, commit)
    assert run_main(tmp_path) == 0
    first = json.loads((tmp_path / "out" / "t1" / "args.json").read_text())["base"]
    assert run_main(tmp_path) == 0
    assert json.loads((tmp_path / "out" / "t1" / "args.json").read_text())["base"] == first


def test_a_failing_task_is_reported_and_the_rest_still_run(tmp_path, source, capsys):
    root, parent, commit = source
    tasks = tmp_path / "tasks"
    make_task(tasks, "bad", root, "0" * 40, commit)
    make_task(tasks, "good", root, parent, commit)
    (tasks / "_template").mkdir()
    planning = make_task(tasks, "plan-x", root, parent, commit)
    (planning / "kind").write_text("planning\n")
    assert run_main(tmp_path) == 1
    captured = capsys.readouterr()
    assert "bad: parent 000000000000 is not in" in captured.err
    assert "plan-x: skipped (planning)" in captured.out
    rows = (tmp_path / "out" / "tasks.tsv").read_text().splitlines()
    assert [row.split("\t")[0] for row in rows[1:]] == ["good"]
    assert not (tmp_path / "out" / "bad").exists()


def test_ids_limit_the_run(tmp_path, source):
    root, parent, commit = source
    make_task(tmp_path / "tasks", "t1", root, parent, commit)
    make_task(tmp_path / "tasks", "t2", root, parent, commit)
    assert run_main(tmp_path, "t2") == 0
    rows = (tmp_path / "out" / "tasks.tsv").read_text().splitlines()
    assert [row.split("\t")[0] for row in rows[1:]] == ["t2"]
