"""The skrode command: serve the job tools to one MCP client over stdio."""

import argparse
import functools
import os
import sys
from collections.abc import Callable
from pathlib import Path

from skrode.config import CONFIG_PATH, ConfigError, load_config
from skrode.jobs import STATE_DIR, run_job
from skrode.llm import DEFAULT_BASE_URL, LLMClient
from skrode.manager import JobManager
from skrode.profiles import ProfileError, load_profile, request_params

DEFAULT_PROFILE = "bonsai2-27b"
DEFAULT_MODEL = "local"  # the alias llama-server serves the profile's model under


def endpoints(client: LLMClient, *, model: str, profile: str, jobs: int) -> list[dict]:
    """The one endpoint this command serves jobs on, for the list_endpoints tool."""
    return [
        {
            "name": "local",
            "base_url": client.base_url,
            "model": model,
            "profile": profile,
            "jobs": jobs,
            "health": client.health(),
        }
    ]


def serve(manager: JobManager, list_endpoints: Callable[[], list[dict]]) -> None:
    """Serve the job tools over stdin and stdout until the client closes stdin."""
    # imported here, so a bad flag or profile is reported before the SDK's import cost
    import anyio
    import mcp.server.stdio

    from skrode.server import build_server

    server = build_server(manager, list_endpoints=list_endpoints)

    async def run() -> None:
        async with mcp.server.stdio.stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skrode",
        description="Serve run_local_subagent and the job tools to one MCP client over stdio.",
    )
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--no-sandbox", action="store_true")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--config", default=None)
    ns = parser.parse_args(argv)

    if ns.jobs < 1:
        print(f"bad jobs: --jobs must be at least 1, not {ns.jobs}", file=sys.stderr)
        return 2

    try:
        profile = load_profile(ns.profile)
        params = request_params(profile, "agent")
    except ProfileError as error:
        print(f"bad profile: {error}", file=sys.stderr)
        return 2

    try:
        client = LLMClient(ns.base_url)
    except ValueError as error:
        print(f"bad base url: {error}", file=sys.stderr)
        return 2

    try:
        if ns.config is None:
            setups = load_config(CONFIG_PATH)
        else:
            setups = load_config(Path(ns.config), required=True)
    except ConfigError as error:
        print(f"bad config: {error}", file=sys.stderr)
        return 2

    state_dir = Path(ns.state_dir) if ns.state_dir else STATE_DIR
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"bad state dir: {error}", file=sys.stderr)
        return 2
    if not os.access(state_dir, os.W_OK):
        print(f"bad state dir: {state_dir} is not writable", file=sys.stderr)
        return 2

    run = functools.partial(
        run_job,
        client=client,
        profile_name=ns.profile,
        model=ns.model,
        params=params,
        narrated_markers=tuple(profile.get("narrated_markers", [])),
        state_dir=state_dir,
        setups=setups,
        sandbox=not ns.no_sandbox,
    )

    manager = JobManager(
        run, profile_name=ns.profile, state_dir=state_dir, concurrency=ns.jobs
    )
    try:
        serve(
            manager,
            functools.partial(
                endpoints, client, model=ns.model, profile=ns.profile, jobs=ns.jobs
            ),
        )
    finally:
        manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
