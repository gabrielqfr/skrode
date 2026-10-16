import functools
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import anyio
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

import router.cli as cli
from router.jobs import run_job
from router.llm import LLMClient
from router.profiles import load_profile, request_params

REPO = Path(__file__).resolve().parents[1]
TOOLS = [
    "run_local_subagent",
    "job_status",
    "wait_jobs",
    "cancel_job",
    "stats",
    "list_endpoints",
]


@pytest.fixture
def fakes(monkeypatch):
    """Replaces JobManager and serve, recording what main builds; no job ever runs."""
    made = {"managers": [], "served": [], "endpoints": []}

    class FakeManager:
        def __init__(self, run, *, profile_name, state_dir):
            self.run = run
            self.profile_name = profile_name
            self.state_dir = state_dir
            self.closed = False
            made["managers"].append(self)

        def close(self, timeout_s=5.0):
            self.closed = True

    def fake_serve(manager, list_endpoints):
        made["served"].append((manager, manager.closed))
        made["endpoints"].append(list_endpoints)

    monkeypatch.setattr(cli, "JobManager", FakeManager)
    monkeypatch.setattr(cli, "serve", fake_serve)
    return made


def test_main_serves_a_manager_bound_to_the_profile(fakes, tmp_path):
    state = tmp_path / "state"
    argv = [
        "--state-dir", str(state),
        "--model", "served-alias",
        "--base-url", "http://127.0.0.1:9999",
        "--no-sandbox",
    ]
    assert cli.main(argv) == 0
    [manager] = fakes["managers"]
    assert fakes["served"] == [(manager, False)]
    assert manager.closed is True
    assert manager.profile_name == "bonsai2-27b"
    assert manager.state_dir == state
    assert state.is_dir()
    run = manager.run
    assert isinstance(run, functools.partial)
    assert run.func is run_job
    assert run.args == ()
    keywords = dict(run.keywords)
    client = keywords.pop("client")
    assert isinstance(client, LLMClient)
    assert client.base_url == "http://127.0.0.1:9999"
    profile = load_profile("bonsai2-27b")
    assert keywords == {
        "profile_name": "bonsai2-27b",
        "model": "served-alias",
        "params": request_params(profile, "agent"),
        "narrated_markers": tuple(profile["narrated_markers"]),
        "state_dir": state,
        "sandbox": False,
    }


def test_main_defaults_to_the_sandbox_and_the_user_state_dir(fakes, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "default")
    assert cli.main([]) == 0
    [manager] = fakes["managers"]
    assert manager.state_dir == tmp_path / "default"
    assert manager.run.keywords["sandbox"] is True
    assert manager.run.keywords["model"] == "local"
    assert manager.run.keywords["client"].base_url == "http://127.0.0.1:8080"


def test_main_serves_the_endpoint_list_bound_to_its_client(fakes, tmp_path):
    argv = [
        "--state-dir", str(tmp_path),
        "--model", "served-alias",
        "--base-url", "http://127.0.0.1:9999",
    ]
    assert cli.main(argv) == 0
    [manager] = fakes["managers"]
    [list_endpoints] = fakes["endpoints"]
    assert isinstance(list_endpoints, functools.partial)
    assert list_endpoints.func is cli.endpoints
    assert list_endpoints.args == (manager.run.keywords["client"],)
    assert list_endpoints.keywords == {"model": "served-alias", "profile": "bonsai2-27b"}


class FakeClient:
    base_url = "http://127.0.0.1:9999"

    def __init__(self, health):
        self.answer = health
        self.calls = 0

    def health(self):
        self.calls += 1
        return self.answer


def test_endpoints_names_the_one_local_endpoint_and_checks_it_each_call():
    client = FakeClient("loading")
    assert cli.endpoints(client, model="served-alias", profile="some-profile") == [
        {
            "name": "local",
            "base_url": "http://127.0.0.1:9999",
            "model": "served-alias",
            "profile": "some-profile",
            "health": "loading",
        }
    ]
    cli.endpoints(client, model="served-alias", profile="some-profile")
    assert client.calls == 2


def test_endpoints_reports_a_server_that_is_not_running():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = LLMClient(f"http://127.0.0.1:{port}")
    [endpoint] = cli.endpoints(client, model="local", profile="bonsai2-27b")
    assert endpoint["health"] == "down"
    assert endpoint["base_url"] == f"http://127.0.0.1:{port}"


def test_the_manager_closes_when_serving_fails(fakes, monkeypatch, tmp_path):
    def broken_serve(manager, list_endpoints):
        raise RuntimeError("stdio broke")

    monkeypatch.setattr(cli, "serve", broken_serve)
    with pytest.raises(RuntimeError, match="stdio broke"):
        cli.main(["--state-dir", str(tmp_path)])
    [manager] = fakes["managers"]
    assert manager.closed is True


def test_an_unknown_profile_exits_2_before_serving(fakes, tmp_path, capsys):
    assert cli.main(["--profile", "no-such-profile", "--state-dir", str(tmp_path)]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "bad profile: profile 'no-such-profile' not found\n"
    assert fakes["managers"] == []


def test_a_base_url_off_localhost_exits_2(fakes, tmp_path, capsys):
    argv = ["--base-url", "http://example.com:8080", "--state-dir", str(tmp_path)]
    assert cli.main(argv) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        "bad base url: the model server must be on http://127.0.0.1, "
        "not http://example.com:8080\n"
    )
    assert fakes["managers"] == []


def test_a_state_dir_that_cannot_be_made_exits_2(fakes, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    assert cli.main(["--state-dir", str(blocker / "state")]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("bad state dir: ")
    assert fakes["managers"] == []


def test_a_state_dir_that_is_not_writable_exits_2(fakes, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("os.access", lambda path, mode: False)
    assert cli.main(["--state-dir", str(tmp_path)]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == f"bad state dir: {tmp_path} is not writable\n"
    assert fakes["managers"] == []


def test_the_cli_module_does_not_import_mcp():
    # flags and profile errors are reported without paying for the SDK's import
    code = "import sys, router.cli; print('mcp' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO
    ).stdout
    assert out.strip() == "False"


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_the_command_lists_the_tools_over_stdio(tmp_path, mode):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "router.cli", "--state-dir", str(tmp_path)],
        cwd=str(REPO),
    )

    async def main():
        async with Client(params, mode=mode) as client:
            return [tool.name for tool in (await client.list_tools()).tools]

    assert anyio.run(main) == TOOLS


def test_closing_stdin_ends_the_command_cleanly(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "router.cli", "--state-dir", str(tmp_path)],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO,
    )
    assert done.returncode == 0
    assert done.stdout == ""


def test_the_hybrid_router_command_runs_main():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"] == {"hybrid-router": "router.cli:main"}
