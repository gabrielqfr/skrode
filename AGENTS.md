# hybrid-router

(Working name until the name search is done.) An MCP stdio server that lets Claude Code hand a
well-specified coding task to a local model. Its tool runs a small agent loop (five tools: read,
write, edit, bash, grep) against llama-server on `127.0.0.1`, inside a git worktree on its own
branch. It then runs the task's acceptance commands, retries with the failure output, and returns
a short record (`passed`, `failed` or `escalate`, under about 1.5k tokens). Claude reads the branch
only when it decides to review it. The point is to spend less Claude quota on mechanical work
without lowering quality, and to show that with a public benchmark.

## Repo map

| Path | What it is |
| --- | --- |
| `router/` | The package. The job: `worker.py` (the agent loop over `llm.py` and the five `tools.py`, shaped by `profiles.py`), `worktree.py`, `acceptance.py`, `jobs.py` (`run_job`). Serving it: `server.py` (MCP tools), `manager.py` (jobs at once), `stats.py`, `config.py` (the user's per-repo setup), `cli.py`. The bench: `benchtask.py` (task folder to arguments), `benchprep.py` (a clone, base commit and config per task) |
| `tests/` | pytest. Tests never need a model server or the network: fake the HTTP endpoint |
| `scripts/verify.sh` | The checks every change must pass, with bounded output |
| `scripts/run-local.sh` | Runs one spec through opencode and the local model in a worktree, and logs the round. The implementer until the router can build itself |
| `implementer/opencode/` | That implementer's opencode config and rules, loaded through `XDG_CONFIG_HOME` so no global opencode config applies |
| `schemas/` | JSON Schemas: `run_local_subagent` arguments, the job record, the worker's five tools and its report, model profiles. `examples/` holds records the tests validate |
| `prompts/worker.md` | Every message sent to the local model, one `## <name>` section each. Load it as written; never reword it in code |
| `docs/escalation.md` | When a job ends `passed`, `escalate` or `failed`, and each reason's trigger |
| `profiles/` | One YAML per served model alias, checked against `schemas/profile.json` |

## Non-negotiables

1. **Local by default.** The worker talks to `127.0.0.1` unless the user configures another
   endpoint; until a spec opens that, the check in `router/llm.py` stays. Tests never use the
   network.
2. **No silent failover.** A job runs on the worker that the caller or the user's configuration
   names. When it cannot finish, it ends as `failed` or `escalate` with a short record; it moves
   to another model only when the user's configuration says so, and the record names every
   model that ran.
3. **Exit 0 is not verification.** A job passes only when its acceptance commands pass in its own
   worktree, run by the router, not as reported by the model.
4. **Hands off Anthropic.** The router never reads, stores or forwards Anthropic credentials, and
   never sits between Claude Code and Anthropic. Claude Code calls it as a tool, nothing more.
5. **Protected tests stay as written.** The worker never edits a path in `must_not_touch`. An
   attempt is an escalation, not a retry.

## Working rules

- Python 3.12 or later, standard library first. A new dependency is a decision recorded in the
  plan, never an implementer's choice. Do not edit `pyproject.toml` or `uv.lock` unless the spec
  lists them.
- Touch only the files the spec or brief names. If another file is needed, stop and say so.
- Tests pin behaviour. Derive expectations from the spec, never by running the code and copying
  its output into an assertion.
- Comments say why, not what. Keep functions small and typed.

## Before you finish

Run `scripts/verify.sh` (ruff, then the full pytest suite). Report its output; never change a
check to make it pass.

## Commits

Conventional messages, `<type>(<scope>): <what>`, signed off for the DCO (`git commit -s`). The GitHub
repo is private; nothing goes public until the project's name is final. Implementers do not commit.

## Privacy

No home paths, usernames, emails, transcripts or data from other repositories in tracked files.
Logs, round records and handoffs live under `.claude/`, which is ignored.
