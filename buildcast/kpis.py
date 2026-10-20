"""KPI 'cue cards' and summary tables from the warehouse, shared by the dashboard, the figures and Power BI."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def job_summary(db: str | Path = ROOT / "data" / "warehouse.db") -> pd.DataFrame:
    con = sqlite3.connect(db)
    q = """
    WITH lab AS (SELECT l.job_ref, SUM(CASE WHEN t.trade_group='electrical' THEN l.hours END) elec,
                        SUM(CASE WHEN t.trade_group='mechanical' THEN l.hours END) mech
                 FROM fact_labour l JOIN dim_trade t USING(trade) GROUP BY 1),
         mat AS (SELECT job_ref, SUM(cost_gbp) material_cost FROM fact_material GROUP BY 1),
         cost AS (SELECT job_ref, SUM(cost_gbp) labour_cost FROM fact_cost GROUP BY 1)
    SELECT j.*, COALESCE(lab.elec,0) actual_electrical_hours, COALESCE(lab.mech,0) actual_mechanical_hours,
           COALESCE(mat.material_cost,0) material_cost_gbp, COALESCE(cost.labour_cost,0) labour_cost_gbp
    FROM dim_job j LEFT JOIN lab USING(job_ref) LEFT JOIN mat USING(job_ref) LEFT JOIN cost USING(job_ref)"""
    df = pd.read_sql(q, con)
    con.close()
    df["actual_hours"] = df.actual_electrical_hours + df.actual_mechanical_hours
    df["tender_hours"] = df.tender_electrical_hours + df.tender_mechanical_hours
    df["hours_per_m2"] = df.actual_hours / df.gia_m2
    df["labour_vs_tender"] = df.actual_hours / df.tender_hours - 1
    df["year"] = pd.to_datetime(df.start_date).dt.year
    return df


def cue_cards(df: pd.DataFrame) -> dict:
    done = df[df.status == "Completed"]
    return {
        "Jobs completed": f"{len(done):,}",
        "Jobs in progress": f"{(df.status == 'Open').sum():,}",
        "Labour hours (completed jobs)": f"{done.actual_hours.sum() / 1e6:.2f} M",
        "Labour hours per m² (median)": f"{done.hours_per_m2.median():.2f}",
        "Jobs over tender labour by more than 10%": f"{(done.labour_vs_tender > 0.10).mean():.0%}",
        "Tender labour estimate error (median)": f"{done.labour_vs_tender.abs().median():.0%}",
        "Material spend (GBP)": f"£{done.material_cost_gbp.sum() / 1e6:.1f} M",
    }


def by_sector(df: pd.DataFrame) -> pd.DataFrame:
    done = df[df.status == "Completed"]
    return done.groupby("sector").agg(jobs=("job_ref", "size"), hours_per_m2=("hours_per_m2", "median"),
                                      over_tender=("labour_vs_tender", lambda s: float((s > 0.1).mean())),
                                      material_cost_gbp=("material_cost_gbp", "sum")).sort_values(
        "hours_per_m2", ascending=False)


def tender_accuracy_by_year(df: pd.DataFrame) -> pd.DataFrame:
    done = df[df.status == "Completed"]
    return done.groupby("year").agg(median_actual_vs_tender=("labour_vs_tender", "median"),
                                    jobs=("job_ref", "size"))


def weekly_hours(db: str | Path = ROOT / "data" / "warehouse.db", before: str = "2026-09-01") -> pd.DataFrame:
    """Hours booked per month, up to the last full month before the data snapshot."""
    con = sqlite3.connect(db)
    df = pd.read_sql("SELECT substr(week_ending,1,7) month, t.trade_group, SUM(hours) hours FROM fact_labour l "
                     "JOIN dim_trade t USING(trade) WHERE week_ending < ? GROUP BY 1, 2", con, params=(before,))
    con.close()
    return df.pivot(index="month", columns="trade_group", values="hours").fillna(0)
