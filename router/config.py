"""The user's configuration: per-repo setup commands for jobs started over MCP.

Setup runs unsandboxed, with network, before the worker, so only the user's own file names it,
never a tool argument or a file inside the repo.
"""

import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "hybrid-router" / "config.toml"


class ConfigError(ValueError):
    """The configuration file cannot be read or has the wrong shape."""


def _check_entry(
    path: Path, entry: dict, i: int, seen: set[Path]
) -> tuple[Path, tuple[str, ...]]:
    where = f"{path}: repo {i}"
    for key in sorted(entry):
        if key not in ("path", "setup"):
            raise ConfigError(f"{where}: unknown key {key!r}")
    if "path" not in entry or not isinstance(entry["path"], str) or entry["path"] == "":
        raise ConfigError(f"{where}: path must be a non-empty string")
    resolved = Path(entry["path"]).expanduser()
    if not resolved.is_absolute():
        raise ConfigError(f"{where}: path must be absolute or start with ~")
    setup = entry.get("setup")
    if not isinstance(setup, list) or not all(isinstance(item, str) and item for item in setup):
        raise ConfigError(f"{where}: setup must be a list of non-empty strings")
    key = resolved.resolve()
    if key in seen:
        raise ConfigError(f"{where}: {key} is listed twice")
    seen.add(key)
    return key, tuple(setup)


def load_config(path: Path, *, required: bool = False) -> dict[Path, tuple[str, ...]]:
    """Map each configured repo's resolved path to its setup commands."""
    if not path.exists():
        if required:
            raise ConfigError(f"{path} does not exist")
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error

    for key in sorted(data):
        if key != "repo":
            raise ConfigError(f"{path}: unknown key {key!r}")

    repo = data.get("repo", [])
    if not isinstance(repo, list) or not all(isinstance(item, dict) for item in repo):
        raise ConfigError(f"{path}: repo must be an array of tables")

    seen: set[Path] = set()
    result: dict[Path, tuple[str, ...]] = {}
    for i, entry in enumerate(repo, start=1):
        key, setup = _check_entry(path, entry, i, seen)
        result[key] = setup
    return result
