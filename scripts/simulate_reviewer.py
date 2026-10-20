"""Stand-in for a person working through the review queue.

The pipeline writes data/review/unmapped_descriptions.csv: every purchase-order description it could not
identify, listed once. In real use an estimator or buyer fills in the material and pack size for each. Here the
simulator's answer key plays that person, so the effect of a one-off review can be measured. The result is the
mapping table config/material_map.csv, which the pipeline applies on every later run.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from buildcast.pipeline import norm_desc  # noqa: E402

queue = pd.read_csv(ROOT / "data" / "review" / "unmapped_descriptions.csv")
lines = pd.DataFrame(json.loads((ROOT / "data" / "raw" / "purchasing.json").read_text())["lines"])
truth = pd.read_csv(ROOT / "data" / "truth" / "po_lines.csv")
lines["description"] = lines.description.map(norm_desc)
lines["material"], lines["per"] = truth.material, truth.qty_true / lines.qty
answers = lines.groupby("description").agg(material=("material", "first"), per=("per", "median"))
out = queue[["description"]].join(answers, on="description").rename(columns={"per": "units_per_order_unit"})
existing = pd.read_csv(ROOT / "config" / "material_map.csv") if (ROOT / "config" / "material_map.csv").exists() else None
out = pd.concat([existing, out]).drop_duplicates("description", keep="last") if existing is not None else out
out.to_csv(ROOT / "config" / "material_map.csv", index=False)
print(f"mapped {len(queue)} descriptions covering {int(queue.lines.sum())} purchase-order lines")
