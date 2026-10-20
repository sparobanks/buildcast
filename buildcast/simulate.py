"""Simulated data for an M&E (mechanical and electrical) contractor working across NI, GB and ROI.

Nothing here is real company data. The simulator exists because a contractor's job history is confidential,
and the point of the project is the method: joining scattered systems and learning from past jobs.

It writes four exports that look like the stand-alone systems a contractor typically runs, each with its own
identifiers, formats and mistakes:

  job_tracker.csv     project register kept by the estimating team
  timesheets.csv      weekly hours per operative from the time-and-attendance system
  purchasing.json     purchase-order lines from the procurement system (free-text descriptions)
  business_central/   job and job-ledger records shaped like Business Central OData web services

It also writes `truth/` (the hidden answer key: true job of each record, true material of each PO line, true
quantities and hours). The pipeline never reads it; it is used only to measure how well the pipeline did.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SECTORS = {  # share of jobs, typical floor area (median m2, spread), floors
    "Commercial": (0.22, 6000, 0.7, (2, 10)),
    "Education": (0.18, 4500, 0.6, (1, 4)),
    "Healthcare": (0.16, 9000, 0.8, (1, 7)),
    "Residential": (0.16, 7000, 0.6, (3, 12)),
    "Life Sciences": (0.10, 5000, 0.7, (1, 5)),
    "Hospitality & Leisure": (0.12, 4000, 0.6, (1, 8)),
    "Heritage": (0.06, 1800, 0.6, (1, 4)),
}
# installed quantity per m2 of floor area by sector (cable m, containment m, m2 per light fitting,
# pipework m, ductwork m2, m2 per sprinkler head)
RATES = {
    "Commercial":            (16, 1.1, 9.0, 0.55, 0.45, 9.0),
    "Education":             (13, 0.9, 8.0, 0.60, 0.35, 10.0),
    "Healthcare":            (24, 1.6, 7.0, 1.10, 0.70, 9.0),
    "Residential":           (11, 0.6, 12.0, 0.80, 0.15, 12.0),
    "Life Sciences":         (26, 1.7, 7.5, 1.00, 0.95, 9.0),
    "Hospitality & Leisure": (15, 1.0, 8.5, 0.85, 0.50, 9.5),
    "Heritage":              (12, 0.8, 9.5, 0.50, 0.25, 11.0),
}
SPEC_MULT = {"Standard": 1.0, "High": 1.15, "Premium": 1.32}
WORK_MULT = {"New build": 1.0, "Refurbishment": 1.28, "Fit-out": 0.92}
REGION_MULT = {"NI": 1.0, "ROI": 1.06, "GB": 1.12}
EUR_PER_GBP = {2018: 1.13, 2019: 1.14, 2020: 1.13, 2021: 1.16, 2022: 1.17, 2023: 1.15, 2024: 1.18, 2025: 1.19,
               2026: 1.17}
MATERIALS = ["cable_m", "containment_m", "light_fittings", "pipework_m", "ductwork_m2", "sprinkler_heads"]
MATERIAL_PRICE_GBP = {"cable_m": 1.35, "containment_m": 14.0, "light_fittings": 95.0, "pipework_m": 32.0,
                      "ductwork_m2": 48.0, "sprinkler_heads": 21.0}
TRADES = {"Electrician": "electrical", "Electrical Improver": "electrical", "Pipefitter": "mechanical",
          "Plumber": "mechanical", "Ductwork Fitter": "mechanical", "Sprinkler Fitter": "mechanical"}

# purchase-order description templates per material: (template, unit text, units per ordered unit)
PO_TEMPLATES = {
    "cable_m": [("2.5mm T&E cable {n}m drum", "drum", "drum"), ("LSF 6491X 2.5mm cable", "m", 1),
                ("SWA 4mm 3 core armoured cable", "metres", 1), ("Cat6 LSZH data cable 305m box", "box", 305),
                ("6242Y twin & earth 1.5mm", "m", 1)],
    "containment_m": [("Cable tray 300mm perforated 3m length", "length", 3),
                      ("Basket tray 150x54 3m", "length", 3), ("Steel trunking 100x100", "m", 1),
                      ("PVC mini trunking 25x16 3m", "lengths", 3)],
    "light_fittings": [("LED panel 600x600 4000K", "each", 1), ("LED downlight IP65", "no.", 1),
                       ("Emergency LED bulkhead 3hr", "ea", 1), ("Linear LED batten 1500mm", "each", 1)],
    "pipework_m": [("Copper tube 22mm 3m", "length", 3), ("MLCP pipe 20mm coil 50m", "coil", 50),
                   ("Steel pipe 54mm black heavy", "m", 1), ("Copper 15mm table X", "metres", 1)],
    "ductwork_m2": [("Galvanised rect ductwork", "m2", 1), ("Spiral duct 250mm dia", "m2", 1),
                    ("Ductwork fabrication pack", "sq m", 1)],
    "sprinkler_heads": [("Sprinkler head pendent 68C", "each", 1), ("Concealed sprinkler head K80", "no.", 1),
                        ("Sidewall sprinkler 57C", "ea", 1)],
}
# Wording the pipeline's rules were NOT written for: other suppliers' phrasing, abbreviations and typos.
# Used on ~15% of lines and scored separately, as a fair test of the material matcher.
PO_TEMPLATES_UNSEEN = {
    "cable_m": [("LV armoured cbl 4c 16mm", "mtr", 1), ("Data cbl Cat 6A U/FTP 500m reel", "reel", 500),
                ("Fire resistant FP200 2c 1.5", "m", 1)],
    "containment_m": [("Unistrut channel 41x41 3mtr", "length", 3), ("Ladder rack 450 wide 3m lgth", "lgth", 3),
                      ("Trunkin galv 75x75", "m", 1)],
    "light_fittings": [("Emerg. luminaire maintained 3h", "pcs", 1), ("Recessed troffer 1200x300", "pcs", 1),
                       ("Wall light IP44 opal", "each", 1)],
    "pipework_m": [("Cu tube 28mm x 3m", "len", 3), ("PE-X barrier tubing 16mm 100m", "roll", 100),
                   ("Press-fit carbon steel 35mm 6m", "len", 6)],
    "ductwork_m2": [("Galv spiral 315 dia", "m2", 1), ("Plenum box fabrication", "sqm", 1)],
    "sprinkler_heads": [("Sprinker hd pendant chrome", "pcs", 1), ("Spklr upright 68 deg", "each", 1)],
}
SECTOR_SPELLINGS = {
    "Commercial": ["Commercial", "commercial", "Commercial Office", "Office"],
    "Education": ["Education", "education", "Schools", "Education "],
    "Healthcare": ["Healthcare", "Health", "Hospital", "healthcare"],
    "Residential": ["Residential", "Resi", "residential", "Housing"],
    "Life Sciences": ["Life Sciences", "Pharma", "Life sciences", "Lab"],
    "Hospitality & Leisure": ["Hospitality & Leisure", "Hospitality", "Leisure", "Hotel"],
    "Heritage": ["Heritage", "heritage", "Listed Building"],
}
SHORT_NAME = {"Commercial": "Office", "Education": "School", "Healthcare": "Hospital", "Residential": "Apartments",
              "Life Sciences": "Laboratory", "Hospitality & Leisure": "Hotel", "Heritage": "Heritage"}
CLIENT_WORDS = ["Health Trust", "Developments", "Properties", "Education Authority", "Hotels", "Pharma",
                "Estates", "Housing Association", "University", "Council", "Holdings", "Partners"]
PLACES = ["Belfast", "Dublin", "Derry", "Newry", "Cork", "Galway", "Glasgow", "Manchester", "Liverpool",
          "Armagh", "Omagh", "Limerick", "Leeds", "Birmingham", "Lisburn", "Dundalk", "Enniskillen", "Bristol"]


@dataclass
class Config:
    n_jobs: int = 1100
    first_year: int = 2018
    last_start: str = "2026-06-30"
    snapshot: str = "2026-09-30"  # data extracted on this date: later jobs are still running
    seed: int = 42


def _jobs(cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    n = cfg.n_jobs
    sectors = list(SECTORS)
    sector = rng.choice(sectors, n, p=[SECTORS[s][0] for s in sectors])
    start = pd.to_datetime(f"{cfg.first_year}-01-01") + pd.to_timedelta(
        np.sort(rng.uniform(0, (pd.Timestamp(cfg.last_start) - pd.Timestamp(f"{cfg.first_year}-01-01")).days, n)),
        unit="D")
    rows = []
    for i in range(n):
        s = sector[i]
        _, med, spread, (fl_lo, fl_hi) = SECTORS[s]
        gia = float(np.clip(rng.lognormal(np.log(med), spread), 300, 60000))
        floors = int(rng.integers(fl_lo, fl_hi + 1))
        work = rng.choice(list(WORK_MULT), p=[0.45, 0.35, 0.20] if s != "Heritage" else [0.1, 0.8, 0.1])
        spec = rng.choice(list(SPEC_MULT), p=[0.5, 0.35, 0.15] if s not in ("Life Sciences", "Healthcare")
                          else [0.25, 0.45, 0.30])
        region = rng.choice(["NI", "GB", "ROI"], p=[0.45, 0.30, 0.25])
        package = rng.choice(["Full M&E", "Electrical only", "Mechanical only"], p=[0.62, 0.22, 0.16])
        sprinklers = package != "Electrical only" and (s in ("Healthcare", "Residential", "Hospitality & Leisure")
                                                       or rng.random() < 0.35)
        year = start[i].year
        offsite = float(np.clip(rng.normal(0.04 + 0.035 * (year - cfg.first_year), 0.06), 0, 0.45)) \
            if work == "New build" else float(np.clip(rng.normal(0.02, 0.03), 0, 0.15))
        occupied = bool(work == "Refurbishment" and rng.random() < 0.55)
        rows.append(dict(job=i, sector=s, gia_m2=gia, floors=floors, work_type=work, spec=spec, region=region,
                         package=package, sprinklers=bool(sprinklers), offsite_pct=round(offsite, 3),
                         occupied=occupied, start=start[i]))
    return pd.DataFrame(rows)


def _outturn(df: pd.DataFrame, rng: np.random.Generator, cfg: Config) -> pd.DataFrame:
    """True installed quantities and labour hours, including factors nobody knows at tender time."""
    out = df.copy()
    n = len(df)
    site_team = rng.lognormal(0, 0.09, n)        # hidden: supervision and team quality
    variations = rng.gamma(2.0, 0.04, n)          # hidden: client changes after award (~8% on average)
    for k, m in enumerate(MATERIALS):
        base = np.array([RATES[s][k] for s in df.sector])
        if m in ("light_fittings", "sprinkler_heads"):
            q = df.gia_m2 / base
        else:
            q = df.gia_m2 * base
        q = q * df.spec.map(SPEC_MULT) ** (0.8 if m != "cable_m" else 1.0)
        q = q * (1 + 0.025 * (df.floors - 1).clip(0, 10)) if m in ("cable_m", "containment_m", "pipework_m") else q
        q = q * rng.lognormal(0, 0.10, n) * (1 + variations)
        elec = m in ("cable_m", "containment_m", "light_fittings")
        in_scope = df.package.ne("Mechanical only") if elec else df.package.ne("Electrical only")
        if m == "sprinkler_heads":
            in_scope = df.sprinklers
        out[m] = np.where(in_scope, np.round(q), 0.0)
    prod = (df.work_type.map(WORK_MULT) * df.region.map(REGION_MULT) * np.where(df.occupied, 1.15, 1.0)
            * np.where(df.sector == "Heritage", 1.35, 1.0) * (1 + 0.02 * (df.floors - 3).clip(0, 9))
            * 0.985 ** (df.start.dt.year - cfg.first_year) * site_team)
    out["electrical_hours"] = np.round((out.cable_m * 0.042 + out.containment_m * 0.30 + out.light_fittings * 1.5)
                                       * prod * (1 - 0.30 * df.offsite_pct) * rng.lognormal(0, 0.06, n))
    out["mechanical_hours"] = np.round((out.pipework_m * 0.85 + out.ductwork_m2 * 0.65 + out.sprinkler_heads * 0.55)
                                       * prod * (1 - 0.65 * df.offsite_pct) * rng.lognormal(0, 0.06, n))
    weeks = np.clip(np.round(12 + 10 * np.sqrt(df.gia_m2 / 1000) * rng.lognormal(0, 0.15, n)), 8, 156)
    out["programme_weeks"] = weeks.astype(int)
    out["finish"] = df.start + pd.to_timedelta(weeks * 7 * (1 + variations), unit="D")
    out["status"] = np.where(out.finish <= pd.Timestamp(cfg.snapshot), "Completed", "Open")
    out["variations"] = variations
    # Tender estimate: the estimating team's rule of thumb, hours per m2 by sector and spec, a little optimistic
    rule = {s: (RATES[s][0] * 0.042 + RATES[s][1] * 0.30 + 1.5 / RATES[s][2],
                RATES[s][3] * 0.85 + RATES[s][4] * 0.65 + 0.55 / RATES[s][5]) for s in RATES}
    e_rate = np.array([rule[s][0] for s in df.sector]) * df.spec.map({"Standard": 1, "High": 1.1, "Premium": 1.25})
    m_rate = np.array([rule[s][1] for s in df.sector]) * df.spec.map({"Standard": 1, "High": 1.1, "Premium": 1.25})
    est_mult = df.work_type.map({"New build": 1.0, "Refurbishment": 1.15, "Fit-out": 0.95}) * 1.12
    noise = rng.lognormal(0, 0.13, n)
    out["tender_electrical_hours"] = np.round(np.where(df.package.ne("Mechanical only"), df.gia_m2 * e_rate, 0)
                                              * est_mult * noise)
    out["tender_mechanical_hours"] = np.round(np.where(df.package.ne("Electrical only"), df.gia_m2 * m_rate, 0)
                                              * est_mult * noise * rng.lognormal(0, 0.05, n))
    return out


def _job_code(year: int, seq: int, style: str) -> str:
    return {"canonical": f"MEP-{year}-{seq:04d}", "short": f"{year % 100:02d}/{seq}",
            "squashed": f"MEP{year}{seq:04d}", "lower": f"mep-{year}-{seq}"}[style]


def _fmt_date(d: pd.Timestamp, rng) -> str:
    return d.strftime("%d/%m/%Y") if rng.random() < 0.6 else d.strftime("%Y-%m-%d")


def generate(out_dir: str | Path, cfg: Config = Config()) -> dict:
    rng = np.random.default_rng(cfg.seed)
    out_dir = Path(out_dir)
    (out_dir / "raw" / "business_central").mkdir(parents=True, exist_ok=True)
    (out_dir / "truth").mkdir(parents=True, exist_ok=True)
    jobs = _outturn(_jobs(cfg, rng), rng, cfg)
    seq_by_year: dict[int, int] = {}
    codes = []
    for d in jobs.start:
        seq_by_year[d.year] = seq_by_year.get(d.year, 0) + 1
        codes.append((d.year, seq_by_year[d.year]))
    jobs["job_ref"] = [_job_code(y, s, "canonical") for y, s in codes]
    jobs["bc_no"] = [f"J{20000 + i:06d}" for i in range(len(jobs))]
    snapshot = pd.Timestamp(cfg.snapshot)

    # ---- 1. job tracker (estimating team's spreadsheet export) -------------------------------------------------
    tr = []
    for (y, s), r in zip(codes, jobs.itertuples()):
        sqft = rng.random() < 0.15
        area = r.gia_m2 * (10.7639 if sqft else 1)
        currency = "EUR" if r.region == "ROI" else "GBP"
        fx = EUR_PER_GBP[r.start.year] if currency == "EUR" else 1.0
        tender_value = (r.tender_electrical_hours + r.tender_mechanical_hours) * 52 * 2.1 * fx
        scope = r.package + (" + sprinklers" if r.sprinklers else "")
        tr.append({"Job Ref": r.job_ref, "Project Name": f"{rng.choice(PLACES)} {SHORT_NAME[r.sector]} "
                   f"{rng.choice(['Scheme', 'Project', 'Phase 2', 'Extension', 'Refit', 'Campus', 'Centre'])}",
                   "Client": f"{rng.choice(PLACES)} {rng.choice(CLIENT_WORDS)}",
                   "Sector": rng.choice(SECTOR_SPELLINGS[r.sector]), "Region": r.region,
                   "Work Type": r.work_type, "Floor Area": round(area, 0), "Area Unit": "sq ft" if sqft else "m2",
                   "Floors": r.floors, "Spec": r.spec, "Scope": scope,
                   "Occupied Site": "Y" if r.occupied else "N", "Offsite %": round(100 * r.offsite_pct),
                   "Start": _fmt_date(r.start, rng), "Planned Weeks": r.programme_weeks,
                   "Tender Elec Hrs": int(r.tender_electrical_hours), "Tender Mech Hrs": int(r.tender_mechanical_hours),
                   "Tender Value": round(tender_value, 2), "Currency": currency})
    tracker = pd.DataFrame(tr)
    dup = tracker.sample(frac=0.02, random_state=1)  # copy-paste duplicates
    tracker = pd.concat([tracker, dup]).sample(frac=1, random_state=2).reset_index(drop=True)
    tracker.to_csv(out_dir / "raw" / "job_tracker.csv", index=False)

    # ---- 2. timesheets (weekly hours per operative) ----------------------------------------------------------
    ts_rows, ts_truth = [], []
    emp_pool = {t: [f"E{rng.integers(1000, 9999)}" for _ in range(60)] for t in TRADES}
    for (y, s), r in zip(codes, jobs.itertuples()):
        end = min(r.finish, snapshot)
        n_weeks = max(1, int((end - r.start).days // 7))
        frac_done = 1.0 if r.status == "Completed" else min(1.0, n_weeks / max(1, (r.finish - r.start).days / 7))
        for group, total in (("electrical", r.electrical_hours), ("mechanical", r.mechanical_hours)):
            hours_left = total * frac_done
            trades = [t for t, g in TRADES.items() if g == group]
            if group == "mechanical" and not r.sprinklers:
                trades = [t for t in trades if t != "Sprinkler Fitter"]
            if r.ductwork_m2 == 0:
                trades = [t for t in trades if t != "Ductwork Fitter"] or trades
            # hours spread over the programme with a hump in the middle
            w = np.sin(np.linspace(0.15, np.pi - 0.15, n_weeks)) + 0.2
            weekly = hours_left * w / w.sum()
            for k, h in enumerate(weekly):
                week = (r.start + pd.Timedelta(weeks=k)).normalize()
                while h > 1:
                    hh = float(min(h, rng.choice([39, 40, 42.5, 45, 47.5])))
                    h -= hh
                    style = rng.choice(["canonical", "short", "squashed", "lower"], p=[0.55, 0.2, 0.15, 0.1])
                    code = _job_code(y, s, style)
                    if rng.random() < 0.002:
                        code = code[:-1] + str((int(code[-1]) + 1) % 10)  # typo -> another or no job
                    t = rng.choice(trades)
                    ts_rows.append({"EmpNo": rng.choice(emp_pool[t]), "Trade": t,
                                    "WeekEnding": (week + pd.Timedelta(days=6)).strftime("%d/%m/%Y"),
                                    "JobCode": code, "Hours": round(hh, 1)})
                    ts_truth.append(r.job)
    ts = pd.DataFrame(ts_rows)
    ts["_truth_job"] = ts_truth
    extra = ts.sample(frac=0.01, random_state=3)  # double-submitted timesheets
    bad = ts.sample(frac=0.003, random_state=4).assign(Hours=lambda d: -d.Hours)  # reversal entered as negative
    ts = pd.concat([ts, extra, bad]).sample(frac=1, random_state=5).reset_index(drop=True)
    ts[["_truth_job"]].to_csv(out_dir / "truth" / "timesheet_jobs.csv", index=False)
    ts.drop(columns="_truth_job").to_csv(out_dir / "raw" / "timesheets.csv", index=False)

    # ---- 3. purchasing (PO lines with free-text descriptions) -------------------------------------------------
    po, po_truth, po_no = [], [], 400000
    for (y, s), r in zip(codes, jobs.itertuples()):
        done = 1.0 if r.status == "Completed" else 0.6
        currency = "EUR" if r.region == "ROI" else "GBP"
        fx = EUR_PER_GBP[r.start.year] if currency == "EUR" else 1.0
        for m in MATERIALS:
            qty_total = getattr(r, m) * done
            if qty_total <= 0:
                continue
            n_orders = int(np.clip(rng.poisson(1 + qty_total ** 0.25 / 3), 1, 12))
            parts = rng.dirichlet(np.ones(n_orders)) * qty_total
            for part in parts:
                unseen = rng.random() < 0.15
                bank = PO_TEMPLATES_UNSEEN if unseen else PO_TEMPLATES
                tmpl, uom, per = bank[m][rng.integers(len(bank[m]))]
                if per == "drum":
                    drum = int(rng.choice([50, 100]))
                    tmpl, per = tmpl.format(n=drum), drum
                n_units = max(1, int(round(part / per)))
                desc = tmpl
                if rng.random() < 0.2:
                    desc = desc.lower()
                if rng.random() < 0.1:
                    desc = desc.replace(" ", "  ", 1)
                po_no += 1
                order_date = r.start + pd.Timedelta(days=int(rng.uniform(0, max(1, (min(r.finish, snapshot) - r.start).days))))
                unit_price = MATERIAL_PRICE_GBP[m] * per * rng.lognormal(0, 0.08) * fx * (1.03 ** (order_date.year - 2018))
                po.append({"po": f"PO{po_no}", "job": _job_code(y, s, rng.choice(["canonical", "squashed"], p=[0.8, 0.2])),
                           "supplier": rng.choice(["CEF", "Edmundson", "Rexel", "Wolseley", "Plumb Center",
                                                   "Hevac", "Electric Center", "Dornan Supplies"]),
                           "description": desc, "qty": n_units, "uom": uom, "unit_price": round(unit_price, 2),
                           "currency": currency, "order_date": order_date.strftime("%Y-%m-%d")})
                po_truth.append({"job": r.job, "material": m, "qty_true": n_units * per, "unseen_wording": unseen})
    (out_dir / "raw" / "purchasing.json").write_text(json.dumps({"exported": cfg.snapshot, "lines": po}))
    pd.DataFrame(po_truth).to_csv(out_dir / "truth" / "po_lines.csv", index=False)

    # ---- 4. Business Central: jobs and job ledger entries (cost postings) ----------------------------------------
    bc_jobs, ledger, entry = [], [], 0
    for (y, s), r in zip(codes, jobs.itertuples()):
        ext = r.job_ref if rng.random() > 0.06 else ""  # a few jobs were set up without the tracker reference
        bc_jobs.append({"No": r.bc_no, "Description": tracker.loc[tracker["Job Ref"] == r.job_ref, "Project Name"].iloc[0],
                        "External_Document_No": ext, "Status": "Completed" if r.status == "Completed" else "Open",
                        "Starting_Date": r.start.strftime("%Y-%m-%d"),
                        "Currency_Code": "EUR" if r.region == "ROI" else "",
                        "Last_Modified_Date_Time": (min(r.finish, snapshot)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        # monthly labour cost postings (resource) at an internal cost rate
        end = min(r.finish, snapshot)
        months = pd.date_range(r.start, end, freq="MS") if end > r.start else [r.start]
        months = list(months) or [r.start]
        total_hours = (r.electrical_hours + r.mechanical_hours) * (1.0 if r.status == "Completed" else 0.6)
        for mth in months:
            entry += 1
            h = total_hours / len(months)
            ledger.append({"Entry_No": entry, "Job_No": r.bc_no, "Posting_Date": mth.strftime("%Y-%m-%d"),
                           "Type": "Resource", "No": "LABOUR", "Description": "Site labour",
                           "Quantity": round(h, 1), "Total_Cost_LCY": round(h * 38.5 * 1.025 ** (mth.year - 2018), 2)})
    (out_dir / "raw" / "business_central" / "jobs.json").write_text(json.dumps(bc_jobs))
    (out_dir / "raw" / "business_central" / "job_ledger_entries.json").write_text(json.dumps(ledger))

    truth = jobs.drop(columns=[]).copy()
    truth["start"] = truth.start.dt.strftime("%Y-%m-%d")
    truth["finish"] = truth.finish.dt.strftime("%Y-%m-%d")
    truth.to_csv(out_dir / "truth" / "jobs.csv", index=False)
    return {"jobs": len(jobs), "completed": int((jobs.status == "Completed").sum()), "tracker_rows": len(tracker),
            "timesheet_rows": len(ts), "po_lines": len(po), "bc_jobs": len(bc_jobs), "bc_ledger": len(ledger)}


if __name__ == "__main__":
    print(generate("data"))
