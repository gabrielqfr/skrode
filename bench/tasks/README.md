# Bench tasks

28 coding tasks, each one a real merged change in a public Python repository, written up as the
spec Claude would hand to an implementer. A run starts from the change's parent commit with the
merged version of its tests already in place, and passes only when `check.sh` passes in the
working tree the row left behind. [`docs/benchmark.md`](../../docs/benchmark.md) has the rows,
the results and how to rerun them.

Only what we wrote is stored here. Upstream code and tests are fetched: `skrode.benchprep`
mirrors each task's `repo`, and builds the task's base commit as `parent` plus the paths in
`hidden.txt`, taken byte for byte from `commit`.

## A task folder

| File | Content |
| --- | --- |
| `spec.md` | The spec, written as if the change did not exist yet. Its `## Files to touch` and `## Commands that must pass` sections list paths and commands in backticks. |
| `repo` | Clone URL of the upstream repository. |
| `parent` | The commit the change was built on: the run's starting point. |
| `commit` | The merged commit. Only its versions of the `hidden.txt` paths are used. |
| `hidden.txt` | The test files, one path per line. They are committed into the base and protected (`must_not_touch`) for router rows; `check.sh` copies them back before grading, so no row is graded by tests it edited. |
| `check.sh` | The grade. Bash, run from the worktree root with `HIDDEN_DIR` set to the task's copy of the hidden tests. Exit 0 is a pass. |
| `setup.sh` | Optional. Builds the worktree's environment; replaces the default (`uv sync --frozen`, then `pnpm --dir web install --frozen-lockfile`). Dependencies are pinned to the merge date with `--exclude-newer`, so reruns resolve the same versions. |
| `source` | Optional. The merged pull request. |
| `commands.txt`, `files.txt` | Optional. Override the spec's commands (the router takes at most five) or its file list. |

## The tasks

Batch b1 holds five connected changes (4-8 files, a behaviour that crosses modules), b3 five
harder ones (3-10 times b1's source lines: a parser, async supervision, a migration), and b4
the 18 older, smaller tasks.

| Task | Upstream | Licence | Batch |
| --- | --- | --- | --- |
| `attrs-ne-validator` | python-attrs/attrs | MIT | b4 |
| `attrs-setattr-generators` | python-attrs/attrs | MIT | b1 |
| `click-edit-pathlib` | pallets/click | BSD-3-Clause | b4 |
| `click-show-envvar-sequence` | pallets/click (on `stable`) | BSD-3-Clause | b4 |
| `datasette-background-tasks` | simonw/datasette | Apache-2.0 | b3 |
| `datasette-lifespan-startup` | simonw/datasette | Apache-2.0 | b1 |
| `datasette-tasks-endpoint` | simonw/datasette | Apache-2.0 | b4 |
| `datasette-write-sql-views` | simonw/datasette | Apache-2.0 | b1 |
| `humanize-natural-list-iterables` | python-humanize/humanize | MIT | b4 |
| `llm-logs-server-tool-results` | simonw/llm | Apache-2.0 | b1 |
| `llm-response-json` | simonw/llm | Apache-2.0 | b3 |
| `mi-one-falsy-exception` | more-itertools/more-itertools | MIT | b4 |
| `sqlite-utils-check-constraints` | simonw/sqlite-utils | Apache-2.0 | b3 |
| `sqlite-utils-compound-fks` | simonw/sqlite-utils | Apache-2.0 | b3 |
| `sqlite-utils-duplicate-columns` | simonw/sqlite-utils | Apache-2.0 | b4 |
| `sqlite-utils-transform-strict` | simonw/sqlite-utils | Apache-2.0 | b4 |
| `tox-override-substitution` | tox-dev/tox | MIT | b1 |
| `tox-set-env-override-substitution` | tox-dev/tox | MIT | b3 |
| `cli-report-no-deck` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `dashboard-csp` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `decks-first-played` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `default-model-opus-5-5` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `live-mtgo-watching` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `llm-cost-line-logging` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `rcq-chips-other` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `shell-store-at-start` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `topdeck-not-constructed` | sensei-top/sensei-top | AGPL-3.0 | b4 |
| `watcher-stopped-notice` | sensei-top/sensei-top | AGPL-3.0 | b4 |

**The 10 sensei-top tasks come from snapshot tags.** That project's development history stays
private; its public repository holds its releases, plus one tag per task, `bench/<task>`. The
tag is the merged change's tree, committed on top of its parent's tree as a root commit, and
the task's `parent` and `commit` name those two snapshot commits. They hold the same trees as
the private commits the runs used, so each rebuilt base has the same tree as the published
run's base; only the commit hashes differ (the base names the snapshot as its parent), which is
why `bench/results/` records other base hashes for these tasks.

## Licence

The specs, checks and setup scripts here are Apache-2.0, like the rest of this repository
(the sensei-top material is released under Apache-2.0 by its author). The upstream code and
tests a run fetches keep their own licences, listed above.
