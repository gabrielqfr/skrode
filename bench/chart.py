"""The README chart: Claude spend saved vs Sonnet alone, per row and batch, as light and dark SVGs.

Each bar is a row's median net Claude $ a run on one batch (`skrode.benchsummary`, one batch at
a time, as in `docs/benchmark.md` "By batch"), as a share of claude-sonnet's median cost on that
batch: the same figure as the README's "Saved vs Sonnet alone". Rerun after the results change.

Run: uv run python bench/chart.py [--results bench/results] [--out docs/img]
"""

import argparse
import sys
from pathlib import Path

from skrode import benchsummary

BATCHES = (
    ("b1", "Connected", "5 tasks"),
    ("b3", "Harder", "5 tasks"),
    ("b4", "Smaller", "18 tasks"),
)
ROWS = (
    ("claude-haiku-subagent", "Haiku 5.5 subagent"),
    ("router-flash", "skrode + DeepSeek V4.1 Flash"),
    ("router-bonsai", "skrode + Bonsai 2 27B (local GPU)"),
)
# Categorical slots 1-3 of the dataviz reference palette, validated for these surfaces.
THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "series": ("#2a78d6", "#eb6834", "#1baf7a"),
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "series": ("#3987e5", "#d95926", "#199e70"),
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

    group_h = len(ROWS) * BAR + (len(ROWS) - 1) * BAR_GAP
    plot_bottom = TOP + len(BATCHES) * group_h + (len(BATCHES) - 1) * GROUP_GAP
    height = plot_bottom + 62
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc" '
        f'font-family="{FONT}">',
        '<title id="title">Claude spend saved vs Sonnet alone, by batch</title>',
        '<desc id="desc">' + "; ".join(
            f"{label}: " + ", ".join(
                f"{row_label} {_percent(shares[batch][row])}" if row in shares[batch]
                else f"{row_label} not run"
                for row, row_label in ROWS
            )
            for batch, label, _ in BATCHES
        ) + ".</desc>",
        f'<rect width="{WIDTH}" height="{height}" fill="{theme["surface"]}"/>',
        _text(24, 32, "Claude spend saved vs Sonnet 5.5 alone", theme["ink"], 16, weight=600),
        _text(24, 52, "Sonnet orchestrates every row. Median a run, as a share of Sonnet's "
              "median cost on the same batch", theme["ink2"]),
    ]

    x = 24.0
    for (_, label), colour in zip(ROWS, theme["series"], strict=True):
        out.append(f'<rect x="{x:.1f}" y="70" width="12" height="12" rx="2" fill="{colour}"/>')
        out.append(_text(x + 18, 80.5, label, theme["ink2"]))
        x += 18 + 7.2 * len(label) + 22

    for tick in range(-limit, limit + 1, TICK_STEP):
        tx = zero + tick * scale
        stroke = theme["axis"] if tick == 0 else theme["grid"]
        out.append(f'<line x1="{tx:.1f}" y1="{TOP - 8}" x2="{tx:.1f}" y2="{plot_bottom + 8}" '
                   f'stroke="{stroke}" stroke-width="1"/>')
        out.append(_text(tx, plot_bottom + 24, _percent(tick / 100), theme["muted"], 12,
                         "middle"))
    out.append(_text(zero - 8, plot_bottom + 44, "← cost Claude more", theme["muted"], 12,
                     "end"))
    out.append(_text(zero + 8, plot_bottom + 44, "saved Claude spend →", theme["muted"], 12))

    y = float(TOP)
    for batch, label, tasks in BATCHES:
        mid = y + group_h / 2
        out.append(_text(24, mid - 2, label, theme["ink"], weight=600))
        out.append(_text(24, mid + 15, f"{batch}, {tasks}", theme["muted"], 12))
        for (row, _), colour in zip(ROWS, theme["series"], strict=True):
            baseline = y + BAR / 2 + 4.5
            share = shares[batch].get(row)
            if share is None:
                out.append(_text(zero + 8, baseline, "not run", theme["muted"], 12))
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=Path("bench/results"))
    parser.add_argument("--out", type=Path, default=Path("docs/img"))
    ns = parser.parse_args()
    shares = saved_shares(ns.results.resolve())
    ns.out.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        path = ns.out / f"bench-saved-{name}.svg"
        path.write_text(render(shares, theme), encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
