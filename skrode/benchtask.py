"""Turn an eval task folder into the arguments a bench run passes to run_local_subagent."""

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "run_local_subagent.json"
COMMAND_LIMIT = 5  # acceptance_criteria.commands maxItems in the schema
TESTS_NOTE = (
    "These tests are already written and are part of the acceptance check. "
    "Read them; do not edit them:"
)
_BACKTICK_SPAN = re.compile(r"`([^`]+)`")


class TaskError(ValueError):
    """A task folder that cannot be turned into valid arguments."""


def section(spec: str, heading: str) -> list[str]:
    target = f"## {heading}"
    out: list[str] = []
    in_section = False
    for line in spec.split("\n"):
        if not in_section:
            if line.rstrip() == target:
                in_section = True
            continue
        if line.startswith("## "):
            break
        out.append(line)
    return out


def backticked(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        for match in _BACKTICK_SPAN.finditer(line):
            value = match.group(1)
            if value.startswith("./"):
                value = value[2:]
            if value not in seen:
                seen.add(value)
                out.append(value)
    return out


def listed(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def hidden_paths(task_dir: Path) -> list[str]:
    hidden = task_dir / "hidden"
    if not hidden.is_dir():
        return []
    return sorted(
        p.relative_to(hidden).as_posix()
        for p in hidden.rglob("*")
        if p.is_file()
    )


def _first_backticked(line: str) -> str:
    match = _BACKTICK_SPAN.search(line)
    if match is None:
        return ""
    value = match.group(1)
    return value[2:] if value.startswith("./") else value


def rewrite_spec(spec: str, protected: list[str]) -> str:
    if not protected:
        return spec
    lines = spec.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.rstrip() == "## Files to touch":
            out.append(line)
            i += 1
            while i < n and not lines[i].startswith("## "):
                if lines[i].startswith("- ") and _first_backticked(lines[i]) in protected:
                    i += 1
                    continue
                out.append(lines[i])
                i += 1
        elif line.rstrip() == "## Tests to add":
            out.append(line)
            i += 1
            while i < n and not lines[i].startswith("## "):
                i += 1
            out.append(TESTS_NOTE)
            out.extend(f"- `{p}`" for p in protected)
            out.append("")
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def task_args(task_dir: Path) -> dict:
    spec_path = task_dir / "spec.md"
    if not spec_path.is_file():
        raise TaskError("no spec.md")
    spec = spec_path.read_text(encoding="utf-8")
    protected = hidden_paths(task_dir)
    named = (
        listed(task_dir / "files.txt")
        if (task_dir / "files.txt").is_file()
        else backticked(section(spec, "Files to touch"))
    )
    files = [p for p in named if p not in protected]
    commands = (
        listed(task_dir / "commands.txt")
        if (task_dir / "commands.txt").is_file()
        else backticked(section(spec, "Commands that must pass"))
    )
    if len(commands) > COMMAND_LIMIT:
        raise TaskError(
            f"{len(commands)} commands; the limit is {COMMAND_LIMIT}: add a commands.txt"
        )
    args = {
        "task": rewrite_spec(spec, protected),
        "files": files,
        "acceptance_criteria": {
            "commands": commands,
            "must_not_touch": protected,
        },
    }
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    errors = list(validator.iter_errors(args))
    if errors:
        first = min(errors, key=lambda e: str(e.path))
        raise TaskError(first.message)
    return args


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    failed = False
    for path in argv:
        folder = Path(path)
        name = folder.name
        if name.startswith("_"):
            continue
        kind = folder / "kind"
        if kind.is_file() and "planning" in kind.read_text(encoding="utf-8"):
            print(f"{name}: skipped (planning)")
            continue
        try:
            args = task_args(folder)
        except TaskError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            failed = True
            continue
        (folder / "args.json").write_text(
            json.dumps(args, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        criteria = args["acceptance_criteria"]
        print(
            f"{name}: ok ({len(args['files'])} files, "
            f"{len(criteria['commands'])} commands, {len(criteria['must_not_touch'])} protected)"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
