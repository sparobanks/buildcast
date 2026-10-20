"""Run the integration pipeline twice, as it would happen in practice, and score both runs.

  1. first run: unfamiliar purchase-order wording is held in a review queue
  2. a person maps each queued description once (simulated with the answer key; see simulate_reviewer.py)
  3. second run: the mapping table resolves those lines

Writes reports/pipeline_audit.json and data/warehouse.db.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from buildcast.pipeline import audit, run  # noqa: E402

(ROOT / "config" / "material_map.csv").unlink(missing_ok=True)
(ROOT / "data" / "warehouse.db").unlink(missing_ok=True)
first = audit(run())
queue = __import__("pandas").read_csv(ROOT / "data" / "review" / "unmapped_descriptions.csv")
subprocess.run([sys.executable, str(ROOT / "scripts" / "simulate_reviewer.py")], check=True)
(ROOT / "data" / "warehouse.db").unlink(missing_ok=True)
res = run()
second = audit(res)
report = {"before_review": first, "review": {"distinct_descriptions": int(len(queue)), "lines_covered": int(queue.lines.sum())},
          "after_review": second, "data_quality_log": res["dq"].to_dict("records")}
(ROOT / "reports").mkdir(exist_ok=True)
(ROOT / "reports" / "pipeline_audit.json").write_text(json.dumps(report, indent=2))
print(json.dumps({k: v for k, v in report.items() if k != "data_quality_log"}, indent=1))
