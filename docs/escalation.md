# Escalation rules

How a job ends, and what the record tells the agent that called `run_local_subagent`. The record's
shape is `schemas/job_record.json`. The messages the worker sees are in `prompts/worker.md`.

## Three statuses

| Status | Meaning | What the caller does |
| --- | --- | --- |
| `passed` | Every check command exited 0 in the job's worktree, run by the router, and no guard below was tripped | Review `git diff <base_commit>..<branch>` before merging, and always when `concerns` is not empty |
| `escalate` | The worker ran but the task needs a stronger agent | Fix the branch, re-brief with a sharper task, or discard it. The reason says which is likely |
| `failed` | The job never reached a verdict on the task | Fix the request or the endpoint, then resubmit. The task itself has not been judged |

The worker's output is untrusted. Its claim of failure is believed: `blocked` in its report
escalates even when the checks pass. Its claim of success is not: `done` counts for nothing until
the checks pass. Partial work is kept: whenever the worktree has changes, the record names the
branch, whatever the status, except `internal`, where the router deletes the branch with the
worktree.

There is no failover. A job that cannot finish on the local model ends here; it is never retried on
a hosted model.

## Escalation reasons

Checked in this order; the first that applies is the record's `reason`, and `detail` names the
path, tool or command.

| Reason | Trigger | Checks run? | Likely next step |
| --- | --- | --- | --- |
| `must_not_touch` | A `write` or `edit` aimed at a protected path, a protected path changed in the worktree at the end of an attempt (by `bash`, say), or one changed by the checks while they ran. The attempt stops at once, with no retry | No, unless the checks made the change | Discard. If the protected file really must change, it is Claude's task |
| `outside_files` | At the end of an attempt, a changed or untracked path (ignored files excepted) matches nothing in `files` | Yes | Re-brief with the extra path in `files`, or keep the in-scope hunks |
| `schema_errors` | More than three bad calls in the job: arguments that fail the tool's schema or do not parse, an unknown tool name, a tool call narrated in prose, or a reply cut off by `finish_reason: length` | Yes | Re-brief with a smaller task; repeated cases point at the profile |
| `rewrite_loop` | A third `write` to the same path within one attempt | Yes | Re-brief naming the lines to change, so `edit` fits |
| `worker_blocked` | The worker's report says `blocked` | Yes | Answer what its summary says is missing, then re-brief |
| `no_changes` | The worker stopped and the worktree has no changes | Yes | Passing checks may mean the task was already done, or that they do not test it; failing ones mean the worker gave up |
| `turn_limit` | An attempt used `max_turns` model calls and the checks then failed; there is no retry. If they pass, the job passes, with a concern noting the limit | Yes | Split the task |
| `checks_failed` | The checks still fail after `1 + max_retries` attempts | Yes | Read the tail and the branch; fix it or re-brief |

Each bad call is answered with one terse correction (`correction.*` in `prompts/worker.md`); the
reply counts as a turn. A `write` or `edit` outside `files` is refused
with `correction.outside_files` and the worker carries on; the end-of-attempt check still catches
changes made any other way.

Retries happen only when the checks fail and no guard was tripped. Each retry is a new turn in the
same conversation, carrying the last 200 lines of the first failing check (`retry` in
`prompts/worker.md`), with a fresh `max_turns`.

The checks run on a snapshot of the attempt's work. Whatever they change in the worktree is then
discarded (ignored files such as a prepared `.venv` stay), so the branch holds the worker's work,
not the checks' side effects. When a check changed a tracked file, the record says so in a
concern: the branch has that file as it was before the checks ran.

The router removes the worktree when the job ends. If that fails (a read-only directory the worker
made, say), the verdict stands and a concern says the worktree is still on disk.

## Failure reasons

| Reason | Trigger |
| --- | --- |
| `bad_request` | Arguments fail `schemas/run_local_subagent.json`, `repo` is not a git repo, `base` does not resolve, a path is in both `files` and `must_not_touch`, or a setup command (the router's own, run before the worker) fails or leaves a changed or untracked file |
| `endpoint` | The model server on `127.0.0.1` is down or keeps returning errors |
| `timeout` | The job ran past its wall-clock limit |
| `cancelled` | `cancel_job` was called |
| `internal` | A router bug. `detail` holds the exception's last line |

## Defaults

`must_not_touch`, when omitted, is every test file tracked at `base` that `files` does not name: paths
under a `tests/` or `test/` directory, or named `test_*`, `*_test.*`, `*.test.*` or `*.spec.*`. A
new test file the task asks for goes in `files` and is not protected.

## Record budget

A record stays under 6000 characters of JSON, about 1.5k tokens. Only the first failing check
carries a `tail`, at most 2000 characters from its end. The full output and the transcript stay in
`logs`. `summary` is at most 150 words and `concerns` at most three lines. The caller reads the
branch only when it decides to review.
