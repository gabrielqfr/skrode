"""Git around one job: its base commit, its worktree and branch, what changed, and the commit that keeps it."""  # noqa: E501

import fnmatch
import os
import subprocess
from pathlib import Path

from skrode.tools import HIDDEN_ENV_PREFIXES, matches

BRANCH_PREFIX = "local-agent/"
GIT_TIMEOUT_S = 60
COMMIT_NAME = "skrode"
COMMIT_EMAIL = "skrode@localhost"
TEST_DIRS = ("tests", "test")
TEST_NAMES = ("test_*", "*_test.*", "*.test.*", "*.spec.*")
FILES_CHANGED_LIMIT = 30
DIFFSTAT_LIMIT = 200


class WorktreeError(RuntimeError):
    """A git command failed, or the repo or base is not usable."""


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # the worker can write files under .git, so no hook may run with the router's network and env
    try:
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            check=False,
            env={k: v for k, v in os.environ.items() if not k.startswith(HIDDEN_ENV_PREFIXES)},
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        raise WorktreeError(f"git {args[0]}: {error}") from error


def _failure(args: tuple[str, ...], result: subprocess.CompletedProcess[str]) -> WorktreeError:
    stderr_lines = [line for line in result.stderr.splitlines() if line]
    last = stderr_lines[-1] if stderr_lines else f"exit {result.returncode}"
    return WorktreeError(f"git {args[0]}: {last}")


def git(cwd: Path, *args: str) -> str:
    result = _run(cwd, *args)
    if result.returncode:
        raise _failure(args, result)
    return result.stdout


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    for part in parts[:-1]:
        if part in TEST_DIRS:
            return True
    return any(fnmatch.fnmatchcase(parts[-1], name) for name in TEST_NAMES)


def resolve_base(repo: Path, base: str) -> str:
    if not repo.is_dir():
        raise WorktreeError(f"not a directory: {repo}")
    if _run(repo, "rev-parse", "--show-toplevel").returncode:
        raise WorktreeError(f"not a git repo: {repo}")
    if base.startswith("-"):
        raise WorktreeError(f"bad base: {base}")
    result = _run(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if result.returncode:
        raise WorktreeError(f"base does not resolve: {base}")
    return result.stdout.strip()


def default_must_not_touch(repo: Path, base_commit: str, files: list[str]) -> list[str]:
    names = git(repo, "ls-tree", "-r", "-z", "--name-only", base_commit)
    return [
        name
        for name in (part for part in names.split("\0") if part)
        if is_test_path(name) and not matches(name, files)
    ]


def branch_name(job_id: str) -> str:
    return BRANCH_PREFIX + job_id


def create(repo: Path, base_commit: str, job_id: str, parent: Path) -> Path:
    path = parent / job_id
    if path.exists():
        raise WorktreeError(f"worktree path exists: {path}")
    parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch_name(job_id), str(path), base_commit)
    return path


def changed_paths(worktree: Path, base_commit: str) -> list[str]:
    diff = git(worktree, "diff", "--name-only", "-z", "--no-renames", base_commit)
    untracked = git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    paths = {part for part in diff.split("\0") if part}
    paths.update(part for part in untracked.split("\0") if part)
    return sorted(paths)


def commit_all(worktree: Path, base_commit: str, message: str) -> bool:
    git(worktree, "add", "-A")
    cached = _run(worktree, "diff", "--cached", "--quiet")
    if cached.returncode == 1:
        git(
            worktree,
            "-c", f"user.name={COMMIT_NAME}",
            "-c", f"user.email={COMMIT_EMAIL}",
            "-c", "commit.gpgsign=false",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            message,
        )
    elif cached.returncode != 0:
        raise _failure(("diff", "--cached"), cached)
    diff = _run(worktree, "diff", "--quiet", base_commit, "HEAD")
    if diff.returncode == 0:
        return False
    if diff.returncode == 1:
        return True
    raise _failure(("diff", "--quiet", base_commit, "HEAD"), diff)


def discard_changes(worktree: Path) -> None:
    # ignored files (a prepared .venv, caches) stay; only tracked and untracked changes go
    git(worktree, "reset", "-q", "--hard", "HEAD")
    git(worktree, "clean", "-fdq")


def uncommit(worktree: Path, base_commit: str) -> None:
    # a mixed reset keeps the files but drops the attempt's commit, so a retrying worker's
    # git status and git diff show all of its work
    git(worktree, "reset", "-q", base_commit)


def diff_stat(worktree: Path, base_commit: str) -> tuple[list[dict], str]:
    numstat = git(worktree, "diff", "--numstat", "-z", "--no-renames", base_commit, "HEAD")
    files_changed: list[dict] = []
    for entry in (part for part in numstat.split("\0") if part):
        added_s, removed_s, path = entry.split("\t", 2)
        files_changed.append(
            {
                "path": path,
                "added": 0 if added_s == "-" else int(added_s),
                "removed": 0 if removed_s == "-" else int(removed_s),
            }
        )
    diffstat = (
        git(worktree, "diff", "--shortstat", base_commit, "HEAD").strip()[:DIFFSTAT_LIMIT]
    )
    return files_changed[:FILES_CHANGED_LIMIT], diffstat


def remove(repo: Path, worktree: Path, job_id: str, *, keep_branch: bool) -> None:
    git(repo, "worktree", "remove", "--force", str(worktree))
    if not keep_branch:
        git(repo, "branch", "-D", branch_name(job_id))
