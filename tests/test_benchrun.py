import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from skrode.benchrun import (
    FLASH_URL,
    HAIKU,
    OPUS,
    RESULTS_HEADER,
    SONNET,
    main,
    read_manifest,
    row_command,
    run_once,
)

CHECK = """set -e
test -f "$TASK_DIR/check.sh"
test -f .venv/marker
grep -qx 'a = 2' a.py
"""


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def fake(bench: dict, name: str, body: str) -> None:
    path = bench["bin"] / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def write_config(bench: dict, setup: str) -> None:
    text = f"[[repo]]\npath = {json.dumps(str(bench['repo']))}\nsetup = [{json.dumps(setup)}]\n"
    (bench["prep"] / "config.toml").write_text(text, encoding="utf-8")


@pytest.fixture
def bench(tmp_path, monkeypatch) -> dict:
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "clone"
    repo.mkdir()
    sh(repo, "init", "-q")
    sh(repo, "config", "user.name", "t")
    sh(repo, "config", "user.email", "t@t.t")
    (repo / "a.py").write_text("a = 1\n")
    (repo / ".gitignore").write_text(".venv/\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "base")
    base = sh(repo, "rev-parse", "HEAD")
    task_dir = tmp_path / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "check.sh").write_text(CHECK, encoding="utf-8")
    prep = tmp_path / "prep"
    (prep / "t1").mkdir(parents=True)
    args = {"task": "# Fix a\n", "files": ["a.py"], "repo": str(repo), "base": base,
            "acceptance_criteria": {"commands": ["true"]}}
    (prep / "t1" / "args.json").write_text(json.dumps(args), encoding="utf-8")
    (prep / "tasks.tsv").write_text(
        f"id\ttask_dir\trepo\tbase\nt1\t{task_dir}\t{repo}\t{base}\n", encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = {"repo": repo, "base": base, "task_dir": task_dir, "prep": prep, "bin": bin_dir,
              "out": tmp_path / "out", "wt": tmp_path / "wt", "router": tmp_path / "router",
              "seen": tmp_path / "seen", "calls": tmp_path / "calls"}
    write_config(result, "mkdir -p .venv && touch .venv/marker")
    return result


def task(bench: dict) -> dict[str, str]:
    return read_manifest(bench["prep"])[0]


def run(bench: dict, row: str = "opencode-bonsai", k: int = 1, **options) -> dict[str, str]:
    options.setdefault("timeout_min", 1)
    return run_once(row, task(bench), k, out=bench["out"], worktrees=bench["wt"],
                    prep=bench["prep"], router_repo=bench["router"], budget_usd=2.5, **options)


def command(bench: dict, row: str, run_name: str = "r1") -> tuple[list[str], dict[str, str]]:
    run_dir = bench["out"] / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return row_command(row, task=task(bench), run_dir=run_dir, prep=bench["prep"],
                       router_repo=bench["router"], budget_usd=2.5)


def after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_manifest_lines_become_tasks(bench):
    assert task(bench) == {"id": "t1", "task_dir": str(bench["task_dir"]),
                           "repo": str(bench["repo"]), "base": bench["base"]}


def test_opencode_row_uses_the_bench_config_dir(bench):
    argv, env = command(bench, "opencode-bonsai")
    prompt = "Implement the spec in SPEC.md. Run the checks it lists and stop when they pass."
    assert argv == ["opencode", "run", prompt]
    assert env == {"XDG_CONFIG_HOME": str(bench["router"] / "bench"),
                   "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1"}


def test_claude_row_runs_sonnet_with_only_its_own_tools(bench):
    argv, env = command(bench, "claude-sonnet")
    assert argv[:2] == ["claude", "-p"]
    assert after(argv, "--model") == SONNET
    assert after(argv, "--output-format") == "stream-json"
    assert after(argv, "--max-budget-usd") == "2.5"
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    tools = argv[argv.index("--allowedTools") + 1:]
    assert tools == ["Bash", "Read", "Edit", "Write", "Grep", "Glob"]
    assert env == {}


def test_subagent_row_defines_a_haiku_implementer(bench):
    argv, _ = command(bench, "claude-haiku-subagent")
    agents = json.loads(after(argv, "--agents"))
    assert agents["implementer"]["model"] == "haiku"
    assert "Agent" in argv[argv.index("--allowedTools") + 1:]
    assert after(argv, "--model") == SONNET


def test_haiku_row_runs_haiku_alone_on_the_plain_prompt(bench):
    sonnet, _ = command(bench, "claude-sonnet")
    argv, env = command(bench, "claude-haiku")
    assert after(argv, "--model") == HAIKU
    assert "--agents" not in argv
    assert argv[:3] == sonnet[:3]
    assert argv[argv.index("--allowedTools") + 1:] == sonnet[sonnet.index("--allowedTools") + 1:]
    assert env == {}


def test_opus_subagent_row_is_the_subagent_row_under_opus(bench):
    haiku_sub, _ = command(bench, "claude-haiku-subagent")
    argv, env = command(bench, "claude-opus-haiku-subagent")
    assert after(argv, "--model") == OPUS
    assert json.loads(after(argv, "--agents"))["implementer"]["model"] == "haiku"
    assert argv[:3] == haiku_sub[:3]
    tools = argv[argv.index("--allowedTools") + 1:]
    assert tools == haiku_sub[haiku_sub.index("--allowedTools") + 1:]
    assert env == {}


def test_router_row_writes_an_mcp_config(bench):
    argv, _ = command(bench, "router-bonsai")
    path = Path(after(argv, "--mcp-config"))
    server = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["skrode"]
    assert server["command"] == "uv"
    assert after(server["args"], "--directory") == str(bench["router"])
    assert after(server["args"], "--config") == str(bench["prep"] / "config.toml")
    assert after(server["args"], "--state-dir") == str(path.parent / "state")
    assert "--base-url" not in server["args"]
    assert str(bench["repo"]) in argv[2] and bench["base"] in argv[2]
    tools = argv[argv.index("--allowedTools") + 1:]
    assert "mcp__skrode__run_local_subagent" in tools
    assert "mcp__skrode__wait_jobs" in tools


def test_flash_row_tags_its_base_url_with_task_and_run(bench):
    argv, _ = command(bench, "router-flash", "r2")
    config = Path(after(argv, "--mcp-config")).read_text(encoding="utf-8")
    server = json.loads(config)["mcpServers"]["skrode"]
    assert after(server["args"], "--model") == "deepseek-flash"
    assert after(server["args"], "--base-url") == f"{FLASH_URL}/t1.r2"


def test_unknown_row_is_an_error(bench):
    with pytest.raises(ValueError):
        command(bench, "gpt")


def test_a_passing_run_is_graded_by_the_tasks_check(bench):
    fake(bench, "opencode", f"cat SPEC.md > {bench['seen']}\necho 'a = 2' > a.py\n")
    result = run(bench)
    assert result["check"] == "pass" and result["exit"] == "0"
    assert result["extra"] == "-" and result["missing"] == "-"
    assert result["dir"] == "opencode-bonsai/t1/r1"
    assert float(result["start"]) <= float(result["end"])
    assert bench["seen"].read_text() == "# Fix a\n"
    diff = (bench["out"] / "opencode-bonsai" / "t1" / "r1" / "agent.diff").read_text()
    assert "+a = 2" in diff and "SPEC.md" not in diff
    assert not (bench["wt"] / "opencode-bonsai-t1-r1").exists()


def test_a_run_that_changes_nothing_fails(bench):
    fake(bench, "opencode", "exit 3\n")
    result = run(bench)
    assert result["check"] == "fail" and result["exit"] == "3"
    assert result["missing"] == "a.py"


def test_files_outside_the_task_are_listed_as_extra(bench):
    fake(bench, "opencode", "echo 'a = 2' > a.py\necho x > b.txt\n")
    result = run(bench)
    assert result["check"] == "pass" and result["extra"] == "b.txt"


def test_a_timeout_kills_the_row_and_still_grades(bench):
    fake(bench, "opencode", "sleep 30\n")
    began = time.monotonic()
    result = run(bench, timeout_min=0.02)
    assert time.monotonic() - began < 20
    assert result["exit"] == "124" and result["check"] == "fail"


def test_a_failed_setup_skips_the_row(bench):
    write_config(bench, "exit 3")
    fake(bench, "opencode", f"touch {bench['seen']}\n")
    result = run(bench)
    assert result["check"] == "setup-failed" and result["exit"] == "-"
    assert not bench["seen"].exists()
    assert not (bench["wt"] / "opencode-bonsai-t1-r1").exists()


def test_keep_leaves_the_worktree(bench):
    fake(bench, "opencode", "echo 'a = 2' > a.py\n")
    run(bench, keep=True)
    assert (bench["wt"] / "opencode-bonsai-t1-r1" / "a.py").read_text() == "a = 2\n"


def test_a_leftover_worktree_is_replaced(bench):
    fake(bench, "opencode", "echo 'a = 2' > a.py\n")
    sh(bench["repo"], "worktree", "add", "--detach", str(bench["wt"] / "opencode-bonsai-t1-r1"))
    assert run(bench)["check"] == "pass"


def test_main_writes_results_and_skips_runs_already_there(bench):
    fake(bench, "opencode", f"echo run >> {bench['calls']}\necho 'a = 2' > a.py\n")
    common = ["--prep", str(bench["prep"]), "--row", "opencode-bonsai", "--out", str(bench["out"]),
              "--worktrees", str(bench["wt"])]
    assert main([*common, "--runs", "2"]) == 0
    assert main([*common, "--runs", "2"]) == 0
    lines = (bench["out"] / "results.tsv").read_text().splitlines()
    assert lines[0].split("\t") == list(RESULTS_HEADER)
    assert [line.split("\t")[:4] for line in lines[1:]] == [
        ["opencode-bonsai", "t1", "1", "pass"], ["opencode-bonsai", "t1", "2", "pass"]]
    assert bench["calls"].read_text().count("run") == 2
    assert main([*common, "--runs", "3", "t1"]) == 0
    assert bench["calls"].read_text().count("run") == 3


def test_the_row_sees_its_worktree_as_pwd(bench):
    # opencode takes its project from $PWD, not the process cwd; bash would reset PWD itself
    path = bench["bin"] / "opencode"
    path.write_text(
        "#!/usr/bin/env python3\nimport os\n"
        f"open({str(bench['seen'])!r}, 'w').write(os.environ.get('PWD', ''))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    run(bench)
    assert bench["seen"].read_text() == str(bench["wt"] / "opencode-bonsai-t1-r1")


def test_main_makes_relative_paths_absolute(bench, monkeypatch):
    copy = f'[ "$1" = --mcp-config ] && cp "$2" {bench["seen"]}'
    fake(bench, "claude", f"while [ $# -gt 0 ]; do {copy}; shift; done\n")
    monkeypatch.chdir(bench["out"].parent)
    assert main(["--prep", "prep", "--row", "router-bonsai", "--out", "out",
                 "--worktrees", "wt", "--runs", "1"]) == 0
    server = json.loads(bench["seen"].read_text())["mcpServers"]["skrode"]
    assert after(server["args"], "--config") == str(bench["prep"] / "config.toml")
    assert after(server["args"], "--state-dir") == str(bench["out"] / "router-bonsai" / "t1" / "r1"
                                                       / "state")
