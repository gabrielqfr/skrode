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
def source(tmp_path, monkeypatch) -> tuple[Path, str]:
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "sources" / "o-n"
    root.mkdir(parents=True)
    sh(root, "init", "-q")
    sh(root, "config", "user.name", "t")
    sh(root, "config", "user.email", "t@t.t")
    (root / "a.py").write_text("a\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("old\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    parent = sh(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("later\n")
    sh(root, "commit", "-q", "-am", "later")
    return root, parent


def make_task(tasks: Path, name: str, parent: str, **extra: str) -> Path:
    folder = tasks / name
    (folder / "hidden" / "tests").mkdir(parents=True)
    (folder / "hidden" / "tests" / "test_a.py").write_text("new\n")
    (folder / "spec.md").write_text(SPEC, encoding="utf-8")
    (folder / "parent").write_text(parent + "\n")
    for filename, text in extra.items():
        (folder / filename.replace("_", ".")).write_text(text, encoding="utf-8")
    return folder


def run_main(tmp_path: Path, default_source: Path, *ids: str) -> int:
    return main(
        [
            "--tasks",
            str(tmp_path / "tasks"),
            "--sources",
            str(tmp_path / "sources"),
            "--default-source",
            str(default_source),
            "--clones",
            str(tmp_path / "clones"),
            "--out",
            str(tmp_path / "out"),
            *ids,
        ]
    )


def test_base_is_the_parent_plus_the_hidden_tests(tmp_path, source):
    root, parent = source
    task = make_task(tmp_path / "tasks", "t1", parent)
    base = bench_base(root, parent, task / "hidden")
    assert sh(root, "rev-parse", f"{base}^") == parent
    assert sh(root, "show", f"{base}:tests/test_a.py") == "new"
    assert sh(root, "show", f"{base}:a.py") == "a"
    assert (
        sh(root, "show", "-s", "--format=%an <%ae>|%s", base)
        == "bench <bench@localhost>|bench base"
    )


def test_base_is_the_same_commit_every_time(tmp_path, source):
    root, parent = source
    task = make_task(tmp_path / "tasks", "t1", parent)
    assert bench_base(root, parent, task / "hidden") == bench_base(root, parent, task / "hidden")


def test_a_missing_parent_is_an_error(tmp_path, source):
    root, _ = source
    task = make_task(tmp_path / "tasks", "t1", "0" * 40)
    with pytest.raises(PrepError, match="is not in"):
        bench_base(root, "0" * 40, task / "hidden")


def test_source_comes_from_the_repo_url(tmp_path):
    task = tmp_path / "t1"
    task.mkdir()
    default = tmp_path / "default"
    assert source_for(task, tmp_path / "sources", default) == default
    (task / "repo").write_text("https://github.com/simonw/sqlite-utils.git\n")
    assert source_for(task, tmp_path / "sources", default) == (
        tmp_path / "sources" / "simonw-sqlite-utils"
    )


def test_main_writes_args_config_and_manifest(tmp_path, source):
    root, parent = source
    tasks = tmp_path / "tasks"
    with_setup = make_task(tasks, "t1", parent, setup_sh="echo hi\n")
    make_task(tasks, "t2", parent)
    assert run_main(tmp_path, root) == 0

    clone1 = (tmp_path / "clones" / "t1").resolve()
    args = json.loads((tmp_path / "out" / "t1" / "args.json").read_text())
    assert args["repo"] == str(clone1)
    assert sh(clone1, "rev-parse", "bench/t1") == args["base"]
    assert sh(clone1, "rev-parse", f"{args['base']}^") == parent
    assert args["acceptance_criteria"]["must_not_touch"] == ["tests/test_a.py"]
    assert args["files"] == ["a.py"]

    config = load_config(tmp_path / "out" / "config.toml", required=True)
    setup1 = f"bash {(with_setup / 'setup.sh').resolve()}"
    assert config == {
        clone1: (setup1,),
        (tmp_path / "clones" / "t2").resolve(): DEFAULT_SETUP,
    }

    rows = (tmp_path / "out" / "tasks.tsv").read_text().splitlines()
    assert rows[0] == "id\ttask_dir\trepo\tbase"
    assert rows[1] == f"t1\t{with_setup.resolve()}\t{clone1}\t{args['base']}"
    assert [row.split("\t")[0] for row in rows[1:]] == ["t1", "t2"]


def test_the_source_repo_is_left_untouched(tmp_path, source):
    root, parent = source
    make_task(tmp_path / "tasks", "t1", parent)
    refs_before = sh(root, "for-each-ref")
    assert run_main(tmp_path, root) == 0
    base = json.loads((tmp_path / "out" / "t1" / "args.json").read_text())["base"]
    assert sh(root, "for-each-ref") == refs_before
    missing = subprocess.run(["git", "cat-file", "-e", base], cwd=root, capture_output=True)
    assert missing.returncode != 0


def test_a_rerun_reuses_the_clone_and_gives_the_same_base(tmp_path, source):
    root, parent = source
    make_task(tmp_path / "tasks", "t1", parent)
    assert run_main(tmp_path, root) == 0
    first = json.loads((tmp_path / "out" / "t1" / "args.json").read_text())["base"]
    assert run_main(tmp_path, root) == 0
    assert json.loads((tmp_path / "out" / "t1" / "args.json").read_text())["base"] == first


def test_a_failing_task_is_reported_and_the_rest_still_run(tmp_path, source, capsys):
    root, parent = source
    tasks = tmp_path / "tasks"
    make_task(tasks, "bad", "0" * 40)
    make_task(tasks, "good", parent)
    (tasks / "_template").mkdir()
    planning = make_task(tasks, "plan-x", parent)
    (planning / "kind").write_text("planning\n")
    assert run_main(tmp_path, root) == 1
    captured = capsys.readouterr()
    assert "bad: parent 000000000000 is not in" in captured.err
    assert "plan-x: skipped (planning)" in captured.out
    rows = (tmp_path / "out" / "tasks.tsv").read_text().splitlines()
    assert [row.split("\t")[0] for row in rows[1:]] == ["good"]
    assert not (tmp_path / "out" / "bad").exists()


def test_ids_limit_the_run(tmp_path, source):
    root, parent = source
    make_task(tmp_path / "tasks", "t1", parent)
    make_task(tmp_path / "tasks", "t2", parent)
    assert run_main(tmp_path, root, "t2") == 0
    rows = (tmp_path / "out" / "tasks.tsv").read_text().splitlines()
    assert [row.split("\t")[0] for row in rows[1:]] == ["t2"]
