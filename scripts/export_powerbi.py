"""Export the warehouse as a Power BI-ready star schema (CSV) into powerbi/."""
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "powerbi" / "data"
out.mkdir(parents=True, exist_ok=True)
con = sqlite3.connect(ROOT / "data" / "warehouse.db")
for t in ("dim_job", "dim_trade", "dim_material", "fact_labour", "fact_material", "fact_cost"):
    pd.read_sql(f"SELECT * FROM {t}", con).to_csv(out / f"{t}.csv", index=False)
dates = pd.DataFrame({"date": pd.date_range("2018-01-01", "2026-12-31")})
dates["year"], dates["month"] = dates.date.dt.year, dates.date.dt.strftime("%Y-%m")
dates["quarter"] = "Q" + dates.date.dt.quarter.astype(str) + " " + dates.year.astype(str)
dates.assign(date=dates.date.dt.strftime("%Y-%m-%d")).to_csv(out / "dim_date.csv", index=False)
con.close()
print("wrote", sorted(p.name for p in out.glob("*.csv")))
