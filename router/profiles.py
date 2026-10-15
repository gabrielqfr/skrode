"""Model profiles: per-request settings for each served model.

Checked against schemas/profile.json.
"""

import copy
import json
import re
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, exceptions

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "profile.json"
MODES = ("agent", "oneshot")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ProfileError(ValueError):
    """A profile that is missing, malformed or lacks the mode asked for."""


def load_profile(name: str, directory: Path = PROFILES_DIR) -> dict:
    if not _NAME_RE.match(name):
        raise ProfileError(f"invalid profile name: {name!r}")
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise ProfileError(f"profile {name!r} not found")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"profile {name!r} is not valid YAML") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"profile {name!r} must be a mapping")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    error = exceptions.best_match(validator.iter_errors(raw))
    if error is not None:
        raise ProfileError(f"profile {name!r} is invalid: {error.message}")
    return raw


def request_params(profile: dict, mode: str = "agent") -> dict:
    if mode not in MODES:
        raise ProfileError(f"unknown mode: {mode!r}")
    if mode not in profile:
        raise ProfileError(f"mode {mode!r} not in profile")
    body = copy.deepcopy(profile[mode])
    extra = body.pop("extra", {})
    out = copy.deepcopy(body)
    out.update(copy.deepcopy(extra))
    return out
