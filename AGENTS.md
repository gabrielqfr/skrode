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
| `router/` | The package. Empty until the worker loop lands: `llm.py`, `profiles.py`, `tools.py`, `worker.py`, then `worktree.py`, `acceptance.py`, `jobs.py`, then `server.py`, `cli.py` |
| `tests/` | pytest. Tests never need a model server or the network: fake the HTTP endpoint |
| `scripts/verify.sh` | The checks every change must pass, with bounded output |
| `scripts/run-local.sh` | Runs one spec through opencode and the local model in a worktree, and logs the round. The implementer until the router can build itself |
| `implementer/opencode/` | That implementer's opencode config and rules, loaded through `XDG_CONFIG_HOME` so no global opencode config applies |

Coming with the spec work: `schemas/` (tool arguments, job record, profile), `prompts/worker.md`
(the worker's system prompt), `profiles/` (one YAML per model family), `docs/escalation.md`.

## Non-negotiables

1. **Local only.** The worker talks to `127.0.0.1` and nothing else. No hosted model, no other
   network access, in the worker or in tests.
2. **No failover.** When the local model cannot finish, the job ends as `failed` or `escalate`
   with a short record. It never retries on a hosted model.
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

Conventional messages, `<type>(<scope>): <what>`, signed off for the DCO (`git commit -s`). Nothing
is pushed until the project has its name. Implementers do not commit.

## Privacy

No home paths, usernames, emails, transcripts or data from other repositories in tracked files.
Logs, round records and handoffs live under `.claude/`, which is ignored.
