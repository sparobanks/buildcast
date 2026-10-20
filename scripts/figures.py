"""python scripts/figures.py  ->  docs/img/*.png"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

BLUE, ORANGE, INK, INK2, GRID, SURFACE = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
OUT = ROOT / "docs" / "img"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                     "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlesize": 13,
                     "axes.titlelocation": "left"})
rep = json.loads((ROOT / "reports" / "models.json").read_text())


def labour_accuracy():
    names = [("model", "BuildCast model"), ("tender estimate (estimating team)", "Tender estimate"),
             ("average of 5 similar past jobs", "Average of 5 similar jobs"),
             ("rate per m2 by sector", "Rate per m² by sector")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharex=True)
    for ax, t, title in zip(axes, ("electrical_hours", "mechanical_hours"), ("Electrical labour", "Mechanical labour")):
        r = rep["targets"][t]
        vals = [r[k]["median_error_pct"] for k, _ in names]
        y = np.arange(len(names))[::-1]
        bars = ax.barh(y, vals, 0.6, color=[BLUE] + [INK2] * 3)
        for b, v in zip(bars, vals):
            ax.text(v + 0.4, b.get_y() + b.get_height() / 2, f"{v:.0f}%", va="center", color=INK, fontsize=10)
        ax.set_yticks(y, [n for _, n in names] if ax is axes[0] else [""] * len(names))
        ax.set_title(title)
        ax.xaxis.grid(True, color=GRID)
        ax.set_axisbelow(True)
        ax.set_xlim(0, max(vals) * 1.25)
    fig.supxlabel("Typical error on jobs started 2024 onwards (median, lower is better)", color=INK2, fontsize=10.5)
    fig.tight_layout()
    fig.savefig(OUT / "labour_accuracy.png", dpi=160)


def predicted_vs_actual():
    p = pd.read_csv(ROOT / "reports" / "test_predictions.csv")
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4))
    for ax, t, title in zip(axes, ("electrical_hours", "mechanical_hours"), ("Electrical labour", "Mechanical labour")):
        d = p[p.target == t]
        inside = (d.actual >= d.low) & (d.actual <= d.high)
        ax.vlines(d.actual, d.low, d.high, color=BLUE, alpha=0.18, lw=1.5)
        ax.scatter(d.actual[inside], d.predicted[inside], s=12, color=BLUE, label="Actual inside the range")
        ax.scatter(d.actual[~inside], d.predicted[~inside], s=16, color=ORANGE, label="Actual outside the range")
        lim = [d[["actual", "low"]].min().min() * 0.9, d[["actual", "high"]].max().max() * 1.1]
        ax.plot(lim, lim, color=INK2, lw=1, ls="--")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.set_xlabel("Actual hours")
        ax.set_title(f"{title}: {inside.mean():.0%} inside the range")
        ax.grid(True, color=GRID, which="major")
    axes[0].set_ylabel("Predicted hours (line = 90% range)")
    axes[0].legend(frameon=False, loc="upper left", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(OUT / "predicted_vs_actual.png", dpi=150)


if __name__ == "__main__":
    labour_accuracy()
    predicted_vs_actual()
    print("figures written")
