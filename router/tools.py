"""The worker's five tools, run inside one worktree, with every call checked before it runs."""

import fnmatch
import json
import os
import signal
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from jsonschema import Draft202012Validator, exceptions

TOOLS_PATH = Path(__file__).resolve().parents[1] / "schemas" / "worker_tools.json"
TOOL_SPECS: list[dict] = json.loads(TOOLS_PATH.read_text(encoding="utf-8"))["tools"]
STDOUT_LIMIT = 12_000
STDERR_LIMIT = 4_000
READ_LINE_LIMIT = 2_000
GREP_LINE_LIMIT = 300
GREP_MATCH_LIMIT = 100
GREP_TIMEOUT_S = 30

# a new network namespace holds only a down loopback, so commands get no network
NO_NETWORK = ("unshare", "--user", "--map-root-user", "--net", "--")

# the local model never sees Anthropic credentials (AGENTS.md, non-negotiable 4)
HIDDEN_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_")

_VALIDATORS: dict[str, Draft202012Validator] = {
    tool["function"]["name"]: Draft202012Validator(tool["function"]["parameters"])
    for tool in TOOL_SPECS
}


class Outcome(StrEnum):
    OK = "ok"                          # the tool ran; text is its reply
    ERROR = "error"                    # the tool could not do it; text says why (sent as the reply)
    INVALID = "invalid"                # args did not parse or failed the schema
    UNKNOWN_TOOL = "unknown_tool"      # text is the tool name as called
    OUTSIDE_FILES = "outside_files"    # write/edit to a path not in files; text is the path
    MUST_NOT_TOUCH = "must_not_touch"  # write/edit to a protected path; text is the path


@dataclass(frozen=True)
class ToolResult:
    outcome: Outcome
    text: str


class PathError(ValueError):
    """A tool path that resolves outside the worktree."""


def matches(path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("./"):
            pattern = pattern[2:]
        if pattern == path:
            return True
        if fnmatch.fnmatchcase(path, pattern):
            return True
        if pattern.endswith("/") and path.startswith(pattern):
            return True
    return False


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"[cut: last {limit} of {len(text)} characters]\n" + text[-limit:]


@dataclass
class Workspace:
    root: Path
    files: list[str]
    must_not_touch: list[str] = field(default_factory=list)
    isolate_network: bool = True

    def relpath(self, path: str) -> str:
        resolved_root = self.root.resolve()
        resolved = (self.root / path).resolve()
        try:
            return resolved.relative_to(resolved_root).as_posix()
        except ValueError:
            raise PathError(f"{path} is outside the worktree") from None

    def run(self, name: str, arguments: str | dict) -> ToolResult:
        if name not in _VALIDATORS:
            return ToolResult(Outcome.UNKNOWN_TOOL, name)
        args: dict
        if isinstance(arguments, str):
            if not arguments.strip():
                args = {}
            else:
                try:
                    args = json.loads(arguments)
                except json.JSONDecodeError:
                    return ToolResult(Outcome.INVALID, "arguments are not valid JSON")
                if not isinstance(args, dict):
                    return ToolResult(Outcome.INVALID, "arguments must be a JSON object")
        else:
            args = arguments
        validator = _VALIDATORS[name]
        best = exceptions.best_match(validator.iter_errors(args))
        if best is not None:
            return ToolResult(Outcome.INVALID, best.message[:200])
        if name == "read":
            args.setdefault("offset", 1)
            args.setdefault("limit", 200)
        elif name == "bash":
            args.setdefault("timeout_s", 120)
        method = {
            "read": self._read,
            "write": self._write,
            "edit": self._edit,
            "bash": self._bash,
            "grep": self._grep,
        }[name]
        try:
            return method(args)
        except PathError as exc:
            return ToolResult(Outcome.ERROR, str(exc))
        except OSError as exc:
            return ToolResult(Outcome.ERROR, f"{type(exc).__name__}: {exc}")

    def _read(self, args: dict) -> ToolResult:
        rel = self.relpath(args["path"])
        full = self.root / rel
        if not full.is_file():
            return ToolResult(Outcome.ERROR, f"not a file: {rel}")
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        offset = args.get("offset", 1)
        limit = args.get("limit", 200)
        if total == 0:
            return ToolResult(Outcome.OK, "[lines 0-0 of 0]")
        if offset > total:
            return ToolResult(
                Outcome.ERROR,
                f"offset {offset} is past the end of {rel} ({total} lines)",
            )
        end = min(total, offset + limit - 1)
        out = [f"[lines {offset}-{end} of {total}]"]
        for n in range(offset, end + 1):
            line = lines[n - 1]
            if len(line) > READ_LINE_LIMIT:
                line = line[:READ_LINE_LIMIT] + " [cut]"
            out.append(f"{n}\t{line}")
        return ToolResult(Outcome.OK, "\n".join(out))

    def _write(self, args: dict) -> ToolResult:
        rel = self.relpath(args["path"])
        if matches(rel, self.must_not_touch):
            return ToolResult(Outcome.MUST_NOT_TOUCH, rel)
        if not matches(rel, self.files):
            return ToolResult(Outcome.OUTSIDE_FILES, rel)
        content = args["content"]
        full = self.root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return ToolResult(Outcome.OK, f"wrote {rel} ({len(content.splitlines())} lines)")

    def _edit(self, args: dict) -> ToolResult:
        rel = self.relpath(args["path"])
        if matches(rel, self.must_not_touch):
            return ToolResult(Outcome.MUST_NOT_TOUCH, rel)
        if not matches(rel, self.files):
            return ToolResult(Outcome.OUTSIDE_FILES, rel)
        full = self.root / rel
        if not full.is_file():
            return ToolResult(Outcome.ERROR, f"not a file: {rel}")
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(Outcome.ERROR, f"{rel} is not UTF-8 text")
        old = args["old"]
        count = text.count(old)
        if count == 0:
            return ToolResult(Outcome.ERROR, f"old text not found in {rel}")
        if count > 1:
            return ToolResult(
                Outcome.ERROR,
                f"old text matches {count} places in {rel}; include more surrounding lines",
            )
        full.write_text(text.replace(old, args["new"], 1), encoding="utf-8")
        return ToolResult(Outcome.OK, f"edited {rel}")

    def _bash(self, args: dict) -> ToolResult:
        cmd = args["cmd"]
        timeout_s = args.get("timeout_s", 120)
        argv = [*NO_NETWORK, "bash", "-c", cmd] if self.isolate_network else ["bash", "-c", cmd]
        env = {k: v for k, v in os.environ.items() if not k.startswith(HIDDEN_ENV_PREFIXES)}
        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=env,
            )
        except FileNotFoundError:
            return ToolResult(Outcome.ERROR, "unshare is not available")
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        out = _tail(stdout.decode("utf-8", errors="replace"), STDOUT_LIMIT).rstrip("\n")
        err = _tail(stderr.decode("utf-8", errors="replace"), STDERR_LIMIT).rstrip("\n")
        parts = [f"timed out after {timeout_s} s" if timed_out else f"exit {proc.returncode}"]
        if out:
            parts += ["--- stdout ---", out]
        if err:
            parts += ["--- stderr ---", err]
        return ToolResult(Outcome.OK, "\n".join(parts))

    def _grep(self, args: dict) -> ToolResult:
        path = args.get("path")
        glob = args.get("glob")
        pathspecs: list[str] = []
        if path is not None:
            rel = self.relpath(path)
            if glob is not None:
                if rel == ".":
                    pathspecs = [glob]
                elif (self.root / rel).is_dir():
                    pathspecs = [f"{rel}/{glob}"]
                else:
                    pathspecs = [rel]
            elif rel != ".":
                pathspecs = [rel]
        elif glob is not None:
            pathspecs = [glob]
        result = subprocess.run(
            [
                "git",
                "grep",
                "-nIE",
                "--untracked",
                "--no-color",
                "-e",
                args["pattern"],
                "--",
                *pathspecs,
            ],
            cwd=self.root,
            capture_output=True,
        )
        if result.returncode == 1:
            return ToolResult(Outcome.OK, "no matches")
        if result.returncode != 0:
            stderr_lines = (result.stderr or b"").decode("utf-8", errors="replace").splitlines()
            last = next((line for line in reversed(stderr_lines) if line.strip()), "")
            return ToolResult(Outcome.ERROR, f"grep failed: {last}")
        lines = (result.stdout or b"").decode("utf-8", errors="replace").splitlines()
        out: list[str] = []
        for line in lines:
            if len(line) > GREP_LINE_LIMIT:
                line = line[:GREP_LINE_LIMIT] + " [cut]"
            out.append(line)
        if len(out) > GREP_MATCH_LIMIT:
            extra = len(out) - GREP_MATCH_LIMIT
            out = [*out[:GREP_MATCH_LIMIT], f"[{extra} more matching lines]"]
        return ToolResult(Outcome.OK, "\n".join(out))
