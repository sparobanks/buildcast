"""Integration pipeline: four stand-alone systems in, one clean SQL warehouse out.

    extract   job tracker (CSV), timesheets (CSV), purchasing (JSON), Business Central (OData API)
    clean     fix formats, units, currencies and duplicates; quarantine what can't be trusted
    match     one job key across systems (job codes typed four different ways; BC's own numbering)
    load      a star schema in SQLite, plus a data-quality log of every rule that fired

Every rule is counted, so the data-quality report says exactly what was changed and why.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent.parent
FX = json.loads((ROOT / "config" / "fx.json").read_text())  # EUR per GBP, annual average

SECTOR_MAP = {"commercial": "Commercial", "commercial office": "Commercial", "office": "Commercial",
              "education": "Education", "schools": "Education", "healthcare": "Healthcare", "health": "Healthcare",
              "hospital": "Healthcare", "residential": "Residential", "resi": "Residential",
              "housing": "Residential", "life sciences": "Life Sciences", "pharma": "Life Sciences",
              "lab": "Life Sciences", "hospitality & leisure": "Hospitality & Leisure",
              "hospitality": "Hospitality & Leisure", "leisure": "Hospitality & Leisure",
              "hotel": "Hospitality & Leisure", "heritage": "Heritage", "listed building": "Heritage"}
TRADE_GROUP = {"Electrician": "electrical", "Electrical Improver": "electrical", "Pipefitter": "mechanical",
               "Plumber": "mechanical", "Ductwork Fitter": "mechanical", "Sprinkler Fitter": "mechanical"}
MATERIAL_RULES = [  # order matters: "cable tray" is containment, not cable
    ("sprinkler_heads", ["sprinkler"]),
    ("ductwork_m2", ["duct"]),
    ("containment_m", ["tray", "trunking", "basket"]),
    ("light_fittings", ["led", "downlight", "luminaire", "bulkhead", "batten"]),
    ("cable_m", ["cable", "t&e", "twin & earth", "swa", "cat6", "6491x", "6242y"]),
    ("pipework_m", ["pipe", "tube", "copper", "mlcp"]),
]
CATALOGUE = {"cable_m": "electrical cable", "containment_m": "cable tray trunking containment",
             "light_fittings": "led light fitting luminaire", "pipework_m": "copper steel pipe tube",
             "ductwork_m2": "ductwork galvanised duct", "sprinkler_heads": "sprinkler head"}
MATERIAL_UNIT = {"cable_m": "m", "containment_m": "m", "light_fittings": "no.", "pipework_m": "m",
                 "ductwork_m2": "m2", "sprinkler_heads": "no."}


class DQ:
    """Data-quality log: (source, rule) -> rows affected."""

    def __init__(self):
        self.c: Counter = Counter()

    def __call__(self, source: str, rule: str, n: int = 1):
        if n:
            self.c[(source, rule)] += int(n)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([{"source": s, "rule": r, "rows": n} for (s, r), n in sorted(self.c.items())])


# ---- job codes -----------------------------------------------------------------------------------------------
_PATTERNS = [re.compile(r"^MEP-(\d{4})-(\d{1,4})$", re.I), re.compile(r"^MEP(\d{4})(\d{4})$", re.I),
             re.compile(r"^(\d{2})/(\d{1,4})$")]


def canon_job(code: str) -> str | None:
    """'MEP-2021-0143', 'mep-2021-143', 'MEP20210143' and '21/143' all become 'MEP-2021-0143'."""
    code = str(code).strip()
    for p in _PATTERNS:
        m = p.match(code)
        if m:
            y, s = m.groups()
            y = int(y) + (2000 if len(y) == 2 else 0)
            return f"MEP-{y}-{int(s):04d}"
    return None


# ---- materials -----------------------------------------------------------------------------------------------
def classify_material(desc: str) -> tuple[str, str]:
    """Return (material, method). Keyword rules first, then fuzzy match to the catalogue."""
    d = " ".join(str(desc).lower().split())
    for mat, words in MATERIAL_RULES:
        if any(w in d for w in words):
            return mat, "rule"
    best = process.extractOne(d, CATALOGUE, scorer=fuzz.token_set_ratio)
    return (best[2], "fuzzy") if best and best[1] >= 50 else ("unknown", "none")


_LEN = re.compile(r"(\d+(?:\.\d+)?)\s*m\b")


def units_per_order_unit(desc: str, uom: str) -> float:
    """Metres (or items) in one ordered unit: a '100m drum' is 100 m; a '3m length' is 3 m; 'each' is 1."""
    u = str(uom).lower().strip()
    if u in ("drum", "box", "coil", "length", "lengths"):
        found = _LEN.findall(" ".join(str(desc).lower().split()))
        return float(found[-1]) if found else 1.0
    return 1.0


# ---- the pipeline --------------------------------------------------------------------------------------------
def clean_tracker(raw: pd.DataFrame, dq: DQ) -> pd.DataFrame:
    df = raw.copy()
    n0 = len(df)
    df = df.drop_duplicates()
    dq("job_tracker", "exact duplicate rows removed", n0 - len(df))
    df["job_ref"] = df["Job Ref"].map(canon_job)
    dq("job_tracker", "unreadable job reference", df.job_ref.isna().sum())
    df = df.dropna(subset=["job_ref"]).drop_duplicates("job_ref")
    s = df.Sector.str.strip().str.lower()
    df["sector"] = s.map(SECTOR_MAP)
    dq("job_tracker", "sector spelling standardised", (df.Sector.str.strip() != df.sector).sum())
    dq("job_tracker", "unknown sector", df.sector.isna().sum())
    sqft = df["Area Unit"].str.lower().str.contains("ft")
    df["gia_m2"] = np.where(sqft, df["Floor Area"] / 10.7639, df["Floor Area"]).round(0)
    dq("job_tracker", "floor area converted from sq ft to m2", sqft.sum())
    df["start"] = pd.to_datetime(df.Start, format="mixed", dayfirst=True)
    dq("job_tracker", "date format standardised (dd/mm/yyyy -> ISO)", df.Start.str.contains("/").sum())
    fx = np.where(df.Currency.eq("EUR"), df.start.dt.year.astype(str).map(FX).astype(float), 1.0)
    df["tender_value_gbp"] = (df["Tender Value"] / fx).round(2)
    dq("job_tracker", "tender value converted EUR -> GBP", df.Currency.eq("EUR").sum())
    scope = df.Scope.str.lower()
    out = pd.DataFrame({
        "job_ref": df.job_ref, "project_name": df["Project Name"].str.strip(), "client": df.Client.str.strip(),
        "sector": df.sector, "region": df.Region, "work_type": df["Work Type"], "gia_m2": df.gia_m2,
        "floors": df.Floors.astype(int), "spec": df.Spec,
        "package": np.select([scope.str.startswith("electrical"), scope.str.startswith("mechanical")],
                             ["Electrical only", "Mechanical only"], "Full M&E"),
        "sprinklers": scope.str.contains("sprinkler").astype(int), "occupied": df["Occupied Site"].eq("Y").astype(int),
        "offsite_pct": df["Offsite %"] / 100.0, "start_date": df.start.dt.strftime("%Y-%m-%d"),
        "planned_weeks": df["Planned Weeks"], "tender_electrical_hours": df["Tender Elec Hrs"],
        "tender_mechanical_hours": df["Tender Mech Hrs"], "tender_value_gbp": df.tender_value_gbp})
    return out.sort_values("job_ref").reset_index(drop=True)


def clean_timesheets(raw: pd.DataFrame, jobs: set[str], dq: DQ) -> pd.DataFrame:
    df = raw.copy()
    n0 = len(df)
    df = df.drop_duplicates()
    dq("timesheets", "duplicate submissions removed", n0 - len(df))
    neg = df.Hours <= 0
    dq("timesheets", "zero or negative hours quarantined", neg.sum())
    df = df[~neg]
    df["job_ref"] = df.JobCode.map(canon_job)
    dq("timesheets", "job code reformatted to MEP-YYYY-NNNN", (df.JobCode != df.job_ref).sum())
    unknown = ~df.job_ref.isin(jobs)
    dq("timesheets", "job code not found in tracker (quarantined)", unknown.sum())
    df = df[~unknown]
    df["trade_group"] = df.Trade.map(TRADE_GROUP)
    df["week_ending"] = pd.to_datetime(df.WeekEnding, format="%d/%m/%Y").dt.strftime("%Y-%m-%d")
    return df[["job_ref", "EmpNo", "Trade", "trade_group", "week_ending", "Hours"]].rename(
        columns={"EmpNo": "employee", "Trade": "trade", "Hours": "hours"})


MAP_FILE = ROOT / "config" / "material_map.csv"
BASE_UOMS = {"m", "metres", "m2", "sq m", "each", "no.", "ea"}   # one ordered unit = one base unit
PACK_UOMS = {"drum", "box", "coil", "length", "lengths"}        # size read from the description


def norm_desc(d: str) -> str:
    return " ".join(str(d).lower().split())


def load_material_map(path: Path = MAP_FILE) -> dict:
    """Descriptions a person has already mapped: normalised description -> (material, units per ordered unit)."""
    if not Path(path).exists():
        return {}
    m = pd.read_csv(path)
    return {r.description: (r.material, float(r.units_per_order_unit)) for r in m.itertuples()}


def clean_purchasing(raw: dict, jobs: set[str], dq: DQ, review_dir: Path | None = None,
                     mapping: dict | None = None) -> pd.DataFrame:
    df = pd.DataFrame(raw["lines"])
    mapping = load_material_map() if mapping is None else mapping
    df["job_ref"] = df.job.map(canon_job)
    unknown = ~df.job_ref.isin(jobs)
    dq("purchasing", "job code not found in tracker (quarantined)", unknown.sum())
    dq("purchasing", "job code reformatted", (df.job != df.job_ref).sum())
    nd = df.description.map(norm_desc)
    cls = [(mapping[d][0], "map") if d in mapping else classify_material(d) for d in nd]
    uom = df.uom.str.lower().str.strip()
    odd_unit = ~nd.isin(mapping) & ~uom.isin(BASE_UOMS | PACK_UOMS)
    dq("purchasing", "unit of measure not recognised (quarantined for review)", int(odd_unit.sum()))
    cls = [("unknown", "none") if o else c for c, o in zip(cls, odd_unit)]
    df["material"] = [c[0] for c in cls]
    dq("purchasing", "material from reviewer's mapping table", sum(c[1] == "map" for c in cls))
    dq("purchasing", "material identified by keyword rule", sum(c[1] == "rule" for c in cls))
    dq("purchasing", "material identified by fuzzy match", sum(c[1] == "fuzzy" for c in cls))
    dq("purchasing", "material not identified (quarantined for review)", sum(c[1] == "none" for c in cls))
    per = [mapping[d][1] if d in mapping else units_per_order_unit(d, u) for d, u in zip(nd, df.uom)]
    if review_dir is not None:  # the review queue: each unknown description once, busiest first
        q = df[[c[1] == "none" for c in cls]].assign(description=nd[[c[1] == "none" for c in cls]])
        q = q.groupby("description").agg(lines=("po", "size"), example_uom=("uom", "first")).sort_values(
            "lines", ascending=False).reset_index()
        Path(review_dir).mkdir(parents=True, exist_ok=True)
        q.assign(material="", units_per_order_unit="").to_csv(Path(review_dir) / "unmapped_descriptions.csv",
                                                              index=False)
    df["quantity"] = df.qty * np.array(per)
    dq("purchasing", "pack/drum/length quantities converted to base units", int((np.array(per) != 1).sum()))
    yr = pd.to_datetime(df.order_date).dt.year.astype(str)
    fx = np.where(df.currency.eq("EUR"), yr.map(FX).astype(float), 1.0)
    df["cost_gbp"] = (df.qty * df.unit_price / fx).round(2)
    dq("purchasing", "cost converted EUR -> GBP", df.currency.eq("EUR").sum())
    keep = ~unknown & df.material.ne("unknown")
    out = df[keep][["po", "job_ref", "supplier", "material", "quantity", "cost_gbp", "order_date", "description"]]
    return out.reset_index(drop=True), df  # second value keeps every line for evaluation


def match_bc_jobs(bc_jobs: list[dict], tracker: pd.DataFrame, dq: DQ) -> pd.DataFrame:
    """Business Central has its own job numbers. Match on the tracker reference when it was typed in, otherwise
    on project name and start date."""
    bc = pd.DataFrame(bc_jobs)
    bc["job_ref"] = bc.External_Document_No.map(lambda x: canon_job(x) if x else None)
    by_ref = bc.job_ref.notna() & bc.job_ref.isin(set(tracker.job_ref))
    dq("business_central", "matched on tracker reference", by_ref.sum())
    names = dict(zip(tracker.job_ref, tracker.project_name + " " + tracker.start_date))
    for i in bc.index[~by_ref]:
        key = f"{bc.at[i, 'Description']} {bc.at[i, 'Starting_Date']}"
        hit = process.extractOne(key, names, scorer=fuzz.token_sort_ratio)
        if hit and hit[1] >= 95:
            bc.at[i, "job_ref"] = hit[2]
            dq("business_central", "matched on project name + start date (no reference in BC)")
        else:
            bc.at[i, "job_ref"] = None
            dq("business_central", "no confident match (left for manual review)")
    return bc


def run(data_dir: str | Path = ROOT / "data", db_path: str | Path | None = None, http=None,
        mapping: dict | None = None) -> dict:
    """Run the full pipeline. `http` is any requests-style client pointed at Business Central (a test client
    for the mock by default)."""
    from .bc_api import BCClient, make_app

    data_dir = Path(data_dir)
    raw = data_dir / "raw"
    db_path = Path(db_path or data_dir / "warehouse.db")
    dq = DQ()

    tracker = clean_tracker(pd.read_csv(raw / "job_tracker.csv"), dq)
    jobs = set(tracker.job_ref)
    ts = clean_timesheets(pd.read_csv(raw / "timesheets.csv"), jobs, dq)
    po, po_all = clean_purchasing(json.loads((raw / "purchasing.json").read_text()), jobs, dq,
                                  review_dir=data_dir / "review", mapping=mapping)

    if http is None:
        from fastapi.testclient import TestClient
        http = TestClient(make_app(raw / "business_central"))
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS sync_state (entity TEXT PRIMARY KEY, watermark TEXT)")
    state = dict(con.execute("SELECT entity, watermark FROM sync_state").fetchall())
    client = BCClient(http)
    bc_jobs = client.fetch("Jobs")  # small table: always full refresh
    since = state.get("Job_Ledger_Entries")
    new_ledger = client.fetch("Job_Ledger_Entries", since=since)
    dq("business_central", "ledger entries fetched (incremental)" if since else "ledger entries fetched (full)",
       len(new_ledger))
    bc = match_bc_jobs(bc_jobs, tracker, dq)
    bc_map = dict(zip(bc.No, bc.job_ref))
    ledger = pd.DataFrame(new_ledger)
    if len(ledger):
        ledger["job_ref"] = ledger.Job_No.map(bc_map)
        cur = bc.set_index("No").Currency_Code.reindex(ledger.Job_No).fillna("").values
        yr = pd.to_datetime(ledger.Posting_Date).dt.year.astype(str)
        fx = np.where(cur == "EUR", yr.map(FX).astype(float), 1.0)
        ledger["cost_gbp"] = (ledger.Total_Cost_LCY / fx).round(2)
        dq("business_central", "ledger cost converted EUR -> GBP", int((cur == "EUR").sum()))
        ledger = ledger.dropna(subset=["job_ref"])

    # ---- load: star schema ---------------------------------------------------------------------------------
    status = bc.dropna(subset=["job_ref"]).set_index("job_ref").Status
    tracker["status"] = tracker.job_ref.map(status).fillna("Unknown")
    tracker["bc_job_no"] = tracker.job_ref.map(dict(zip(bc.job_ref, bc.No)))
    tracker.to_sql("dim_job", con, if_exists="replace", index=False)
    pd.DataFrame([{"trade": t, "trade_group": g} for t, g in TRADE_GROUP.items()]).to_sql(
        "dim_trade", con, if_exists="replace", index=False)
    pd.DataFrame([{"material": m, "unit": u, "description": CATALOGUE[m]} for m, u in MATERIAL_UNIT.items()]).to_sql(
        "dim_material", con, if_exists="replace", index=False)
    weekly = ts.groupby(["job_ref", "trade", "week_ending"], as_index=False).hours.sum()
    weekly.to_sql("fact_labour", con, if_exists="replace", index=False)
    po.to_sql("fact_material", con, if_exists="replace", index=False)
    if len(ledger):
        cols = ["Entry_No", "job_ref", "Posting_Date", "Type", "Quantity", "cost_gbp"]
        ledger[cols].rename(columns=str.lower).to_sql(
            "fact_cost", con, if_exists="append" if since else "replace", index=False)
        con.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?)",
                    ("Job_Ledger_Entries", ledger.Posting_Date.max()))
    dq_frame = dq.frame()
    dq_frame.to_sql("dq_log", con, if_exists="replace", index=False)
    con.commit()
    con.close()
    return {"dq": dq_frame, "po_all": po_all, "bc": bc, "timesheets": ts}


def audit(result: dict, data_dir: str | Path = ROOT / "data", db_path: str | Path | None = None) -> dict:
    """Score the pipeline against the simulator's hidden answer key (never used by the pipeline itself)."""
    data_dir = Path(data_dir)
    truth = pd.read_csv(data_dir / "truth" / "jobs.csv")
    out = {}
    # Business Central job matching
    bc = result["bc"].merge(truth[["bc_no", "job_ref"]].rename(columns={"bc_no": "No", "job_ref": "true_ref"}), on="No")
    matched = bc.job_ref.notna()
    out["bc_job_matching"] = {"jobs": int(len(bc)), "matched": int(matched.sum()),
                              "matched_correctly": int((bc.job_ref == bc.true_ref).sum()),
                              "wrong_matches": int((matched & (bc.job_ref != bc.true_ref)).sum()),
                              "left_for_review": int((~matched).sum())}
    # material identification
    po_all, po_truth = result["po_all"], pd.read_csv(data_dir / "truth" / "po_lines.csv")
    ok_mat = po_all.material.values == po_truth.material.values
    ok_qty = np.isclose(po_all.quantity.values, po_truth.qty_true.values)
    unseen = po_truth.unseen_wording.values.astype(bool)
    held = po_all.material.values == "unknown"
    silent = ~held & ~(ok_mat & ok_qty)
    out["material_identification"] = {
        "po_lines": int(len(po_all)), "held_for_review": int(held.sum()), "silently_wrong": int(silent.sum()),
        "familiar_wording": {"lines": int((~unseen).sum()), "material_correct": round(float(ok_mat[~unseen].mean()), 4),
                             "quantity_correct": round(float(ok_qty[~unseen].mean()), 4)},
        "unseen_wording": {"lines": int(unseen.sum()), "material_correct": round(float(ok_mat[unseen].mean()), 4),
                           "quantity_correct": round(float(ok_qty[unseen].mean()), 4)}}
    # job-level totals for completed jobs: warehouse vs truth
    con = sqlite3.connect(db_path or data_dir / "warehouse.db")
    lab = pd.read_sql("SELECT l.job_ref, t.trade_group, SUM(l.hours) h FROM fact_labour l JOIN dim_trade t USING(trade) "
                      "GROUP BY 1, 2", con).pivot(index="job_ref", columns="trade_group", values="h").fillna(0)
    mat = pd.read_sql("SELECT job_ref, material, SUM(quantity) q FROM fact_material GROUP BY 1, 2", con).pivot(
        index="job_ref", columns="material", values="q").fillna(0)
    con.close()
    t = truth[truth.status == "Completed"].set_index("job_ref")
    errs = {}
    for col, src in (("electrical_hours", lab.get("electrical")), ("mechanical_hours", lab.get("mechanical")),
                     ("cable_m", mat.get("cable_m")), ("pipework_m", mat.get("pipework_m"))):
        x = src.reindex(t.index).fillna(0)
        m = t[col] > 0
        e = (x[m] - t[col][m]).abs() / t[col][m]
        errs[col] = {"median_abs_pct_error": round(float(e.median() * 100), 2),
                     "jobs_within_2pct": round(float((e <= 0.02).mean()), 3)}
    out["job_totals_vs_truth"] = errs
    return out
