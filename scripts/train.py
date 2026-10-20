"""python scripts/train.py  ->  models/forecaster.joblib, reports/models.json, reports/test_predictions.csv"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from buildcast.model import train_and_evaluate  # noqa: E402

fc, rep = train_and_evaluate()
(ROOT / "models").mkdir(exist_ok=True)
fc.save(ROOT / "models" / "forecaster.joblib")
rep.pop("_predictions").to_csv(ROOT / "reports" / "test_predictions.csv", index=False)
(ROOT / "reports" / "models.json").write_text(json.dumps(rep, indent=2))
for t, r in rep["targets"].items():
    print(f"{t:18s} {r['model_chosen']:18s} model {r['model']['median_error_pct']:5.1f}%  cover "
          f"{r['model']['range_covers_actual']:.2f}  " + "  ".join(
              f"{k.split(' (')[0][:22]} {v['median_error_pct']}%" for k, v in r.items()
              if isinstance(v, dict) and "median_error_pct" in v and k != "model"))
print(rep["jobs"])
