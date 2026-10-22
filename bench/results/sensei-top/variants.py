"""Build each item's brief for the three rows from its original brief.

haiku and opus get the "alone" brief, sonnet the orchestrated one. An item
first run alone keeps its own Rules section and gains the orchestration
section before it; an item first run orchestrated loses that section and
gets the same rules, worded for one agent. The Item, Files and Done-when
sections are identical across a single item's rows.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])           # .claude/handoffs/backlog-runs
bench = root / "bench"
section = (root / "orchestrator-section.md").read_text()
how, rules = section.split("## Rules", 1)
rules_alone = ("## Rules" + rules).replace(
    "## Rules (yours and the implementer's)", "## Rules"
).replace(
    "the decisions made without asking (yours or the implementer's),\n  how many correction rounds it took, and",
    "the decisions you made yourself, and",
)
how_after_rules = how.rstrip() + "\n\nThe Rules above bind you and the implementer; its brief carries them.\nYour report also says how many correction rounds it took.\n"

for line in (bench / "items.tsv").read_text().splitlines():
    if line.startswith("#") or not line.strip():
        continue
    ident, _base, _tip, row, *_ = line.split("\t")
    brief = (root / ident / "brief.md").read_text()
    if row == "sonnet":
        alone = (root / ident / "head.md").read_text().rstrip() + "\n\n" + rules_alone.strip() + "\n"
        orch = brief
    else:
        alone = brief
        orch = brief.rstrip() + "\n\n" + how_after_rules
    for r, text in (("haiku", alone), ("opus", alone), ("sonnet", orch)):
        (bench / "briefs" / f"{ident}-{r}.md").write_text(text)
print("ok")
