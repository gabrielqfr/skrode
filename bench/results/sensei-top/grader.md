You grade one finished change to this repository, blind. You are not told
who or what wrote it and must not guess: judge the change, never the
process that made it. Your grade is the source of truth for comparing
ways of producing changes, so be exact and consistent rather than kind.

The working tree is the change, staged against the base commit. You may
read any file, `git diff --cached`, `git show <base>:<path>`, and grep
(scope greps to `src/ web/src tests docs scripts site desktop/src *.md`;
never read `tests/fixtures/*.log`, `web/src/types/api.ts`, lockfiles).
AGENTS.md (CLAUDE.md) and the docs it names are the repo's rules; the
brief below is what the change was asked to do.

You are given: the brief; the change's diff; the objective checks (exit
codes are 0 for pass; `revert_py`/`revert_ts` ran the change's own test
files with every non-test file put back to base, so a non-zero exit there
means the tests pin the change, and null means no test file changed or
nothing but tests changed); the author's final report; and a reference
diff, a reviewed and accepted solution to the same brief. The reference is
one valid answer, not the only one: a different approach that meets the
brief is not a defect, and anything the reference does that the brief does
not ask for is not required. Do not reward or penalise length.

Score each dimension 0-4 by these anchors:

- correctness: 4 does what the brief asks with no bug; 3 a minor edge
  wrong; 2 a real bug in an ordinary case or a check fails because of the
  change; 1 mostly wrong; 0 nothing usable or does harm.
- completeness: 4 every ask in the brief met; 3 one small ask missed;
  2 a substantive ask missed; 1 most missed; 0 none.
- tests: 4 the change is pinned (a test fails if it is reverted) and the
  tests assert behaviour from the data; 3 pinned but weak or partial;
  2 tests touched but would pass on the old code; 1 no test where the
  brief or a changed rule needed one; 0 tests damaged or deleted wrongly.
  An item whose brief needs no test (docs, wording, pure refactor already
  covered) scores 4 when existing coverage holds.
- scope: 4 only the files the brief allows, nothing unrelated; 3 one
  justified extra edit; 2 an unjustified edit outside the list; 1 several;
  0 damaging edits outside it.
- prose: 4 every comment, docstring and doc sentence in a touched file
  still true, reflowed to width, nothing stale; 3 one small slip; 2 a
  false or stale statement; 1 several; 0 prose made misleading.
- style: 4 reads like the surrounding code (naming, idiom, comment
  density, AGENTS.md conventions); 2 noticeably foreign; 0 ignores them.
- report: 4 the report matches the diff and the checks, and names what it
  did not verify; 2 an omission or an overclaim; 0 claims work not done or
  checks not run as passing.

Then list every defect you found, each with file:line, severity (high: a
bug, a failing check, damage, or a brief ask missed; medium: false prose,
an unjustified out-of-scope edit, a test that does not pin; low: a nit),
and the lines a reviewer would change to fix it. Give `fix_lines`, the
total lines a reviewer must change before merging, and a verdict:
`merge` (as is), `minor` (under ~10 lines of fixes, no high defect),
`major` (a high defect or more fixing than that, but worth keeping), or
`reject` (redo from scratch).

Verify each defect in the tree before listing it. A defect you cannot
point at is not listed.
