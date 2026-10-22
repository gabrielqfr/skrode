"""The README charts, as light and dark SVGs.

`bench-saved-*`: Claude spend saved vs Sonnet alone, per row and batch. Each bar is a row's
median net Claude $ a run on one batch (`skrode.benchsummary`, one batch at a time, as in
`docs/benchmark.md` "By batch"), as a share of claude-sonnet's median cost on that batch: the
same figure as the README's "Saved vs Sonnet alone".

`bench-graded-*`: the graded bench on Sensei Top (`bench/results/sensei-top/`). Each bar is a
row's ten runs split by the grader's verdict, with the mean score and median run cost beside it:
the same figures as that folder's `summarize.py`. Rerun after the results change.

Run: uv run python bench/chart.py [--results bench/results] [--out docs/img]
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

from skrode import benchsummary

BATCHES = (
    ("b1", "Connected", "5 tasks"),
    ("b3", "Harder", "5 tasks"),
    ("b4", "Smaller", "18 tasks"),
)
ROWS = (
    ("claude-haiku-subagent", "Sonnet + Haiku 5.5 subagent"),
    ("router-flash", "skrode + DeepSeek V4.1 Flash"),
    ("router-bonsai", "skrode + Bonsai 2 27B (local GPU)"),
    ("claude-haiku", "Haiku 5.5 alone"),
    ("claude-opus-haiku-subagent", "Opus 5.5 + Haiku 5.5 subagent"),
)
# What an empty slot says, naming where the row did run; a bare "not run" did not say which row
# the gap belonged to. A row missing here leaves its empty slots blank.
ONLY_ON = {
    "router-bonsai": "Bonsai: Connected and Harder only",
    "claude-opus-haiku-subagent": "Opus + Haiku subagent: Harder only",
}
# Categorical slots 1-5 of the dataviz reference palette, validated for these surfaces. Rows
# added later take the next slot so the earlier rows keep their colours.
THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7",
        "series": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"),
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835",
        "series": ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"),
    },
}
WIDTH = 760
PLOT_LEFT, PLOT_RIGHT = 180, WIDTH - 24
BAR, BAR_GAP, GROUP_GAP = 18, 2, 22
TOP = 112
TICK_STEP = 20
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def saved_shares(results: Path) -> dict[str, dict[str, float]]:
    """Batch name -> row name -> median net Claude $ over claude-sonnet's median, rows that ran."""
    shares: dict[str, dict[str, float]] = {}
    for batch, _, _ in BATCHES:
        rows = benchsummary.summarize_rows(benchsummary.summarize([results / batch]))
        medians = {
            row["row"]: (row["median_claude_usd"], row["median_net_claude_usd"]) for row in rows
        }
        baseline = float(medians[benchsummary.BASELINE_ROW][0])
        shares[batch] = {
            name: float(net) / baseline
            for name, (_, net) in medians.items()
            if name != benchsummary.BASELINE_ROW and net != "-"
        }
    return shares


def _percent(share: float) -> str:
    value = round(share * 100)
    if value == 0:
        return "0%"
    return f"+{value}%" if value > 0 else f"\N{MINUS SIGN}{-value}%"


def _bar_path(x0: float, x1: float, y: float, h: float, r: float = 4) -> str:
    """A bar from the zero line x0 to x1, square at x0 and rounded at its data end."""
    r = min(r, abs(x1 - x0), h / 2)
    if x1 >= x0:
        return (f"M{x0:.1f},{y:.1f}H{x1 - r:.1f}Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f}"
                f"V{y + h - r:.1f}Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f}H{x0:.1f}Z")
    return (f"M{x0:.1f},{y:.1f}H{x1 + r:.1f}Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f}"
            f"V{y + h - r:.1f}Q{x1:.1f},{y + h:.1f} {x1 + r:.1f},{y + h:.1f}H{x0:.1f}Z")


def _text(x: float, y: float, body: str, fill: str, size: int = 13, anchor: str = "start",
          weight: int = 400) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{body}</text>')


def render(shares: dict[str, dict[str, float]], theme: dict) -> str:
    """One SVG chart in `theme`'s colours."""
    values = [share for batch in shares.values() for share in batch.values()]
    # Symmetric around zero so a saving and a loss of the same size look the same size.
    reach = max(abs(v) for v in values) * 100
    limit = (int(reach // TICK_STEP) + 1) * TICK_STEP
    zero = (PLOT_LEFT + PLOT_RIGHT) / 2
    scale = (PLOT_RIGHT - zero) / limit

    # Legend entries wrap to a new line at the right edge; the plot starts below the last one.
    legend, x, ly = [], 24.0, 70
    for (_, label), colour in zip(ROWS, theme["series"], strict=True):
        width = 18 + 7.2 * len(label) + 22
        if x + width - 22 > WIDTH - 24:
            x, ly = 24.0, ly + 22
        legend.append(f'<rect x="{x:.1f}" y="{ly}" width="12" height="12" rx="2" fill="{colour}"/>')
        legend.append(_text(x + 18, ly + 10.5, label, theme["ink2"]))
        x += width
    top = TOP + ly - 70

    group_h = len(ROWS) * BAR + (len(ROWS) - 1) * BAR_GAP
    plot_bottom = top + len(BATCHES) * group_h + (len(BATCHES) - 1) * GROUP_GAP
    height = plot_bottom + 62
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc" '
        f'font-family="{FONT}">',
        '<title id="title">Claude spend saved vs Sonnet alone, by batch</title>',
        '<desc id="desc">' + "; ".join(
            f"{label}: " + ", ".join(
                f"{row_label} {_percent(shares[batch][row])}" if row in shares[batch]
                else f"{row_label} not run on this batch"
                for row, row_label in ROWS
            )
            for batch, label, _ in BATCHES
        ) + ".</desc>",
        f'<rect width="{WIDTH}" height="{height}" fill="{theme["surface"]}"/>',
        _text(24, 32, "Claude spend saved vs Sonnet 5.5 alone", theme["ink"], 16, weight=600),
        _text(24, 52, "Median a run, as a share of Sonnet's median cost on the same batch. "
              "Sonnet orchestrates the skrode rows", theme["ink2"]),
        *legend,
    ]

    for tick in range(-limit, limit + 1, TICK_STEP):
        tx = zero + tick * scale
        stroke = theme["axis"] if tick == 0 else theme["grid"]
        out.append(f'<line x1="{tx:.1f}" y1="{top - 8}" x2="{tx:.1f}" y2="{plot_bottom + 8}" '
                   f'stroke="{stroke}" stroke-width="1"/>')
        out.append(_text(tx, plot_bottom + 24, _percent(tick / 100), theme["muted"], 12,
                         "middle"))
    out.append(_text(zero - 8, plot_bottom + 44, "← cost Claude more", theme["muted"], 12,
                     "end"))
    out.append(_text(zero + 8, plot_bottom + 44, "saved Claude spend →", theme["muted"], 12))

    y = float(top)
    for batch, label, tasks in BATCHES:
        mid = y + group_h / 2
        out.append(_text(24, mid - 2, label, theme["ink"], weight=600))
        out.append(_text(24, mid + 15, f"{batch}, {tasks}", theme["muted"], 12))
        for (row, _), colour in zip(ROWS, theme["series"], strict=True):
            baseline = y + BAR / 2 + 4.5
            share = shares[batch].get(row)
            if share is None:
                # A hollow swatch in the row's colour ties the note to its legend entry.
                if row in ONLY_ON:
                    out.append(f'<rect x="{zero + 8.5:.1f}" y="{y + 3.5:.1f}" width="11" '
                               f'height="11" rx="2" fill="none" stroke="{colour}"/>')
                    out.append(_text(zero + 26, baseline, ONLY_ON[row], theme["muted"], 12))
            else:
                x1 = zero + share * 100 * scale
                out.append(f'<path d="{_bar_path(zero, x1, y, BAR)}" fill="{colour}"/>')
                if share >= 0:
                    out.append(_text(x1 + 6, baseline, _percent(share), theme["ink"], 12))
                else:
                    out.append(_text(x1 - 6, baseline, _percent(share), theme["ink"], 12, "end"))
            y += BAR + BAR_GAP
        y += GROUP_GAP - BAR_GAP
    out.append("</svg>")
    return "\n".join(out) + "\n"


GRADED_ROWS = (
    ("haiku", "Haiku 5.5 alone"),
    ("sonnet", "Sonnet + Haiku 5.5 subagent"),
    ("opus", "Opus 5.5 alone"),
    ("ref", "Reference (merged commit)"),
)
VERDICTS = (
    ("merge", "merge as is"), ("minor", "minor fixes"), ("major", "major fixes"),
    ("reject", "reject"),
)
# Ordinal blue ramps from the dataviz reference palette, merge strongest, validated with
# `validate_palette.js --ordinal` on each surface; the ink pairs keep each count readable.
GRADED_THEMES = {
    "light": {"fills": ("#104281", "#256abf", "#5598e7", "#86b6ef"),
              "inks": ("#fcfcfb", "#fcfcfb", "#0b0b0b", "#0b0b0b")},
    "dark": {"fills": ("#b7d3f6", "#5598e7", "#256abf", "#184f95"),
             "inks": ("#1a1a19", "#1a1a19", "#f0efec", "#f0efec")},
}
# The rubric's weights, as in bench/results/sensei-top/summarize.py.
WEIGHTS = {"correctness": 3, "completeness": 2, "tests": 2, "scope": 1, "prose": 1, "style": 1,
           "report": 1}
GRADED_LEFT, GRADED_RIGHT = 230, 540
GRADED_BAR, GRADED_GAP = 24, 14


def _score(grade: dict, ref: bool) -> int:
    # A reference has no report, so its score leaves that dimension out.
    weights = {k: v for k, v in WEIGHTS.items() if not (ref and k == "report")}
    return round(100 * sum(w * grade["scores"][k] for k, w in weights.items())
                 / (4 * sum(weights.values())))


def _run_cost(run: Path) -> float | None:
    path = run / "run.jsonl"
    if not path.exists():
        return None
    results = [json.loads(line) for line in path.open() if '"type":"result"' in line]
    return results[-1].get("total_cost_usd") if results else None


def graded(results: Path) -> dict[str, dict]:
    """Row -> verdict counts, mean score and median run cost over its graded runs."""
    runs: dict[str, list[tuple[dict, int, float | None]]] = {}
    for run in sorted((results / "sensei-top" / "runs").iterdir()):
        row = run.name.rsplit("-", 1)[1]
        raw = json.loads((run / "grade1.json").read_text())
        grade = raw.get("structured_output") or json.loads(raw.get("result") or "null")
        runs.setdefault(row, []).append((grade, _score(grade, row == "ref"), _run_cost(run)))
    out = {}
    for row, xs in runs.items():
        costs = [cost for _, _, cost in xs if cost is not None]
        out[row] = {
            "verdicts": [sum(g["verdict"] == v for g, _, _ in xs) for v, _ in VERDICTS],
            "mean": statistics.mean(score for _, score, _ in xs),
            "cost": statistics.median(costs) if costs else None,
        }
    return out


def render_graded(rows: dict[str, dict], theme: dict, ramp: dict) -> str:
    """The graded bench chart: one stacked bar of verdicts per row, in `theme`'s colours."""
    n = max(sum(r["verdicts"]) for r in rows.values())
    unit = (GRADED_RIGHT - GRADED_LEFT) / n
    legend, x = [], 24.0
    for (_, label), fill in zip(VERDICTS, ramp["fills"], strict=True):
        legend.append(f'<rect x="{x:.1f}" y="70" width="12" height="12" rx="2" fill="{fill}"/>')
        legend.append(_text(x + 18, 80.5, label, theme["ink2"]))
        x += 18 + 7.2 * len(label) + 22
    top = 128
    height = top + len(GRADED_ROWS) * (GRADED_BAR + GRADED_GAP) - GRADED_GAP + 44
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc" '
        f'font-family="{FONT}">',
        '<title id="title">Sensei Top backlog items, blind-graded by Opus 5.5</title>',
        '<desc id="desc">' + "; ".join(
            f"{label}: " + ", ".join(
                f"{count} {v}" for count, (v, _) in zip(rows[row]["verdicts"], VERDICTS,
                                                        strict=True)
            ) + f", mean score {rows[row]['mean']:.0f}"
            + (f", median run ${rows[row]['cost']:.3f}" if rows[row]["cost"] else "")
            for row, label in GRADED_ROWS
        ) + ".</desc>",
        f'<rect width="{WIDTH}" height="{height}" fill="{theme["surface"]}"/>',
        _text(24, 32, "Ten real backlog items, blind-graded by Opus 5.5", theme["ink"], 16,
              weight=600),
        _text(24, 52, "The grader's verdict on each run. The reference is the change that was "
              "merged, graded the same way", theme["ink2"]),
        *legend,
        _text(640, top - 12, "Mean score", theme["muted"], 12, "end"),
        _text(WIDTH - 24, top - 12, "Median run $", theme["muted"], 12, "end"),
    ]
    y = float(top)
    for row, label in GRADED_ROWS:
        data = rows[row]
        baseline = y + GRADED_BAR / 2 + 4.5
        out.append(_text(24, baseline, label, theme["ink"]))
        x0 = float(GRADED_LEFT)
        last = max(i for i, c in enumerate(data["verdicts"]) if c)
        for i, (count, fill, ink) in enumerate(
                zip(data["verdicts"], ramp["fills"], ramp["inks"], strict=True)):
            if not count:
                continue
            # A 2px surface gap between segments; only the bar's data end is rounded.
            x1 = x0 + count * unit - (0 if i == last else 2)
            if i == last:
                out.append(f'<path d="{_bar_path(x0, x1, y, GRADED_BAR)}" fill="{fill}"/>')
            else:
                out.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{x1 - x0:.1f}" '
                           f'height="{GRADED_BAR}" fill="{fill}"/>')
            out.append(_text((x0 + x1) / 2, baseline, str(count), ink, 12, "middle", 600))
            x0 = x1 + (0 if i == last else 2)
        out.append(_text(640, baseline, f"{data['mean']:.0f}", theme["ink"], 13, "end", 600))
        cost = f"{data['cost']:.3f}" if data["cost"] else "\N{EN DASH}"
        out.append(_text(WIDTH - 24, baseline, cost, theme["ink2"], 13, "end"))
        y += GRADED_BAR + GRADED_GAP
    out.append(_text(GRADED_LEFT, height - 18, f"Runs out of {n}, one per item. Score: a weighted "
                     "0-4 rubric, out of 100", theme["muted"], 12))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=Path("bench/results"))
    parser.add_argument("--out", type=Path, default=Path("docs/img"))
    ns = parser.parse_args()
    results = ns.results.resolve()
    shares = saved_shares(results)
    rows = graded(results)
    ns.out.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        path = ns.out / f"bench-saved-{name}.svg"
        path.write_text(render(shares, theme), encoding="utf-8")
        print(path)
        path = ns.out / f"bench-graded-{name}.svg"
        path.write_text(render_graded(rows, theme, GRADED_THEMES[name]), encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
