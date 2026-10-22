"""Tables from the bench's grades: per row, per item, and the grader's
agreement with itself. Score is the weighted rubric out of 100."""
import json
import statistics as st
import sys
from pathlib import Path

B = Path(__file__).resolve().parent
W = {"correctness": 3, "completeness": 2, "tests": 2, "scope": 1, "prose": 1, "style": 1, "report": 1}
ROWS = ("haiku", "sonnet", "opus", "ref")


def grade(d: Path, n: int, ref: bool = False):
    p = d / f"grade{n}.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    g = raw.get("structured_output") or json.loads(raw.get("result") or "null")
    if not g:
        return None
    w = {k: v for k, v in W.items() if not (ref and k == "report")}  # a reference has no report
    g["score"] = round(100 * sum(w[k] * g["scores"][k] for k in w) / (4 * sum(w.values())))
    g["grade_cost"] = raw.get("total_cost_usd", 0)
    return g


def run_cost(d: Path):
    p = d / "run.jsonl"
    if not p.exists():
        return None, None
    rs = [json.loads(line) for line in p.open() if '"type":"result"' in line]
    return (rs[-1].get("total_cost_usd"), rs[-1].get("num_turns")) if rs else (None, None)


def checks_ok(d: Path):
    c = json.loads((d / "checks.json").read_text())
    ok = c["ruff_exit"] == 0 and c["pytest_exit"] == 0
    for part in ("web", "desktop"):
        if c.get(part):
            ok = ok and all(v == 0 for v in c[part].values())
    pinned = None
    for k in ("revert_py", "revert_ts"):
        if c.get(k):
            pinned = (pinned or False) or c[k]["exit"] != 0
    return ok, pinned


rows = {}
for d in sorted((B / "runs").iterdir()):
    ident, row = d.name.rsplit("-", 1)
    g = grade(d, 1, ref=row == "ref")
    if not g:
        continue
    cost, turns = run_cost(d)
    ok, pinned = checks_ok(d)
    rows.setdefault(row, []).append(dict(item=ident, g=g, cost=cost, turns=turns, ok=ok, pinned=pinned, g2=grade(d, 2)))

print("| Row | n | Score mean (min-max) | Verdicts merge/minor/major/reject | High defects | Fix lines median | Checks green | Run cost median | Grade cost total |")
print("|---|---|---|---|---|---|---|---|---|")
for r in ROWS:
    xs = rows.get(r, [])
    if not xs:
        continue
    sc = [x["g"]["score"] for x in xs]
    v = [x["g"]["verdict"] for x in xs]
    hi = sum(1 for x in xs for d in x["g"]["defects"] if d["severity"] == "high")
    costs = [x["cost"] for x in xs if x["cost"] is not None]
    print(f"| {r} | {len(xs)} | {st.mean(sc):.0f} ({min(sc)}-{max(sc)}) | "
          + "/".join(str(v.count(k)) for k in ("merge", "minor", "major", "reject"))
          + f" | {hi} | {st.median(x['g']['fix_lines'] for x in xs):.0f} | {sum(x['ok'] for x in xs)}/{len(xs)}"
          + f" | {'$%.3f' % st.median(costs) if costs else '-'} | ${sum(x['g']['grade_cost'] for x in xs):.2f} |")

print("\n| Item | " + " | ".join(ROWS) + " |\n|---|" + "---|" * len(ROWS))
by_item = {}
for r, xs in rows.items():
    for x in xs:
        by_item.setdefault(x["item"], {})[r] = x
for item in sorted(by_item):
    cells = []
    for r in ROWS:
        x = by_item[item].get(r)
        cells.append("-" if not x else f"{x['g']['score']} {x['g']['verdict']}" + (f" ${x['cost']:.3f}" if x["cost"] else ""))
    print(f"| {item} | " + " | ".join(cells) + " |")

pairs = [(x["g"], x["g2"]) for xs in rows.values() for x in xs if x["g2"]]
if pairs:
    print(f"\nGrader agreement on {len(pairs)} regraded runs: same verdict "
          f"{sum(a['verdict'] == b['verdict'] for a, b in pairs)}/{len(pairs)}, "
          f"mean |score diff| {st.mean(abs(a['score'] - b['score']) for a, b in pairs):.1f}, "
          f"diffs {[a['score'] - b['score'] for a, b in pairs]}")
if "-v" in sys.argv:
    for r, xs in rows.items():
        for x in xs:
            print(f"\n## {x['item']}-{r} {x['g']['score']} {x['g']['verdict']} pinned={x['pinned']} ok={x['ok']}\n{x['g']['summary']}")
            for d in x["g"]["defects"]:
                print(f"- [{d['severity']}] {d['where']}: {d['what']}")
