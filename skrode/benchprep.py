"""Prepare eval tasks for a bench run: a clone per task, its base commit, its args and config.

Every bench row gets the same input (D24): the task text from benchtask and a base commit that
is the task's parent plus its hidden tests, taken from the task's merged commit. The tests are
fetched, never stored: each source repo is mirrored once from the task's `repo` URL, and each
task gets its own clone of that mirror, so a source is only read and each clone can carry the
task's own setup.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from skrode.benchtask import TaskError, hidden_paths, task_args
from skrode.tools import HIDDEN_ENV_PREFIXES

BRANCH_PREFIX = "bench/"
BASE_MESSAGE = "bench base"
BENCH_NAME = "bench"
BENCH_EMAIL = "bench@localhost"
DEFAULT_SETUP = ("uv sync --frozen -q", "pnpm --dir web install --frozen-lockfile --silent")
GIT_TIMEOUT_S = 300


class PrepError(RuntimeError):
    """A task that cannot be prepared."""


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(HIDDEN_ENV_PREFIXES) and not k.startswith("GIT_")
    }
    if env is not None:
        result_env.update(env)
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            check=False,
            env=result_env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        raise PrepError(f"git {args[0]}: {error}") from error
    if result.returncode:
        stderr_lines = [line for line in result.stderr.splitlines() if line]
        last = stderr_lines[-1] if stderr_lines else f"exit {result.returncode}"
        raise PrepError(f"git {args[0]}: {last}")
    return result.stdout


def source_for(task_dir: Path, sources: Path) -> tuple[str, Path]:
    repo_file = task_dir / "repo"
    if not repo_file.is_file():
        raise PrepError("no repo")
    url = repo_file.read_text(encoding="utf-8").strip()
    parts = [p for p in url.replace("\\", "/").split("/") if p]
    owner = parts[-2]
    name = parts[-1]
    if name.endswith(".git"):
        name = name[: -4]
    return url, sources / f"{owner}-{name}"


def ensure_source(url: str, source: Path) -> None:
    if source.exists():
        return
    source.parent.mkdir(parents=True, exist_ok=True)
    # a mirror keeps every branch: some merged commits live off the default branch
    _git(source.parent, "clone", "-q", "--mirror", url, str(source))


def setup_commands(task_dir: Path) -> tuple[str, ...]:
    script = task_dir / "setup.sh"
    if script.is_file():
        return (f"bash {shlex.quote(str(script.resolve()))}",)
    return DEFAULT_SETUP


def ensure_clone(source: Path, dest: Path) -> None:
    if dest.exists():
        if not (dest / ".git").exists():
            raise PrepError(f"{dest} exists and is not a git repo")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(dest.parent, "clone", "-q", "--no-checkout", str(source), str(dest))


def _require_commit(repo: Path, rev: str, what: str) -> None:
    try:
        _git(repo, "cat-file", "-e", f"{rev}^{{commit}}")
    except PrepError as exc:
        raise PrepError(f"{what} {rev[:12]} is not in {repo}") from exc


def _tree_entry(repo: Path, commit: str, rel: str) -> tuple[str, str]:
    listing = _git(repo, "ls-tree", "-z", commit, "--", rel)
    for entry in listing.split("\0"):
        info, _, path = entry.partition("\t")
        if path == rel:
            mode, kind, blob = info.split()
            if kind == "blob":
                return mode, blob
    raise PrepError(f"{rel} is not in commit {commit[:12]}")


def bench_base(repo: Path, parent: str, commit: str, hidden: list[str]) -> str:
    _require_commit(repo, parent, "parent")
    _require_commit(repo, commit, "commit")

    date = _git(repo, "show", "-s", "--format=%cI", parent).strip()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_index = Path(tmp) / "index"
        git_env = {
            "GIT_INDEX_FILE": str(tmp_index),
            "GIT_AUTHOR_NAME": BENCH_NAME,
            "GIT_COMMITTER_NAME": BENCH_NAME,
            "GIT_AUTHOR_EMAIL": BENCH_EMAIL,
            "GIT_COMMITTER_EMAIL": BENCH_EMAIL,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
        }
        _git(repo, "read-tree", parent, env=git_env)
        for rel in hidden:
            mode, blob = _tree_entry(repo, commit, rel)
            _git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{rel}", env=git_env)
        tree = _git(repo, "write-tree", env=git_env).strip()
        base = ("commit-tree", "--no-gpg-sign", tree, "-p", parent, "-m", BASE_MESSAGE)
        return _git(repo, *base, env=git_env).strip()


def write_hidden(repo: Path, base: str, hidden: list[str], dest: Path) -> None:
    """Write the hidden tests where check.sh copies them from ($HIDDEN_DIR)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if not hidden:
        return
    with tempfile.TemporaryDirectory() as tmp:
        # a private index: the clone's own index and work tree stay as they are
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(repo, f"--work-tree={dest}", "checkout", base, "--", *hidden, env=env)


def prepare(
    task_dir: Path, *, sources: Path, clones: Path, out: Path
) -> tuple[dict, tuple[str, ...]]:
    revs = {}
    for name in ("parent", "commit"):
        path = task_dir / name
        if not path.is_file():
            raise PrepError(f"no {name}")
        revs[name] = path.read_text(encoding="utf-8").strip()

    try:
        args = task_args(task_dir)
    except TaskError as exc:
        raise PrepError(str(exc)) from exc
    hidden = hidden_paths(task_dir)

    url, source = source_for(task_dir, sources)
    ensure_source(url, source)

    dest = clones / task_dir.name
    ensure_clone(source, dest)
    base = bench_base(dest, revs["parent"], revs["commit"], hidden)
    _git(dest, "update-ref", f"refs/heads/{BRANCH_PREFIX}{task_dir.name}", base)
    write_hidden(dest, base, hidden, out / task_dir.name / "hidden")

    args["repo"] = str(dest.resolve())
    args["base"] = base
    return args, setup_commands(task_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m skrode.benchprep")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--clones", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("ids", nargs="*")
    options = parser.parse_args(argv)

    if options.ids:
        folders = [options.tasks / id_ for id_ in options.ids if not id_.startswith("_")]
    else:
        folders = sorted(
            p for p in options.tasks.iterdir() if p.is_dir() and not p.name.startswith("_")
        )

    failed = False
    successful: list[tuple[str, Path, dict]] = []

    for folder in folders:
        name = folder.name
        kind = folder / "kind"
        if kind.is_file() and "planning" in kind.read_text(encoding="utf-8"):
            print(f"{name}: skipped (planning)")
            continue
        try:
            args, _ = prepare(
                folder,
                sources=options.sources,
                clones=options.clones,
                out=options.out,
            )
        except PrepError as error:
            print(f"{name}: {error}", file=sys.stderr)
            failed = True
            continue
        (options.out / name).mkdir(parents=True, exist_ok=True)
        (options.out / name / "args.json").write_text(
            json.dumps(args, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{name}: base {args['base'][:12]} in {args['repo']}")
        successful.append((name, folder, args))

    options.out.mkdir(parents=True, exist_ok=True)

    # a JSON string is a valid TOML basic string
    config_entries = []
    for _name, folder, args in successful:
        setup = setup_commands(folder)
        entry = (
            "[[repo]]\n"
            f"path = {json.dumps(str(args['repo']))}\n"
            f"setup = [{', '.join(json.dumps(c) for c in setup)}]\n"
        )
        config_entries.append(entry)
    config_text = "\n".join(config_entries)
    (options.out / "config.toml").write_text(config_text, encoding="utf-8")

    tsv_lines = ["id\ttask_dir\trepo\tbase\thidden"]
    for name, folder, args in successful:
        hidden_dir = (options.out / name / "hidden").resolve()
        tsv_lines.append(
            f"{name}\t{folder.resolve()}\t{args['repo']}\t{args['base']}\t{hidden_dir}"
        )
    (options.out / "tasks.tsv").write_text("\n".join(tsv_lines) + "\n", encoding="utf-8")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
