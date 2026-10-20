"""Predict labour hours and material quantities for a new job from the company's own past jobs.

Only what is known at tender time goes in: sector, region, type of work, floor area, floors, spec level,
scope, occupied site, offsite share, planned programme and start year. Targets come from the warehouse
(timesheets and purchase orders), not from the estimators' figures.

The test is honest about time: models learn from jobs that started before 2023, the error range is
calibrated on 2023 starts, and everything is scored on jobs that started in 2024 or later, as if predicting
jobs that had not happened yet.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

ROOT = Path(__file__).resolve().parent.parent
CAT = ["sector", "region", "work_type", "spec", "package"]
NUM = ["log_gia", "floors", "sprinklers", "occupied", "offsite_pct", "planned_weeks", "year"]
TARGETS = {"electrical_hours": "hours", "mechanical_hours": "hours", "cable_m": "m", "containment_m": "m",
           "light_fittings": "no.", "pipework_m": "m", "ductwork_m2": "m2", "sprinkler_heads": "no."}
LABELS = {"electrical_hours": "Electrical labour", "mechanical_hours": "Mechanical labour", "cable_m": "Cable",
          "containment_m": "Containment", "light_fittings": "Light fittings", "pipework_m": "Pipework",
          "ductwork_m2": "Ductwork", "sprinkler_heads": "Sprinkler heads"}
ELECTRICAL = {"electrical_hours", "cable_m", "containment_m", "light_fittings"}
TRAIN_END, CALIB_END = "2023-01-01", "2024-01-01"
COVERAGE = 0.9


def in_scope(df: pd.DataFrame, target: str) -> pd.Series:
    if target == "sprinkler_heads":
        return df.sprinklers.astype(bool)
    if target in ELECTRICAL:
        return df.package.ne("Mechanical only")
    return df.package.ne("Electrical only")


def features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["log_gia"] = np.log(x.gia_m2)
    x["year"] = pd.to_datetime(x.start_date).dt.year
    return x[CAT + NUM]


def load_jobs(db: str | Path = ROOT / "data" / "warehouse.db") -> pd.DataFrame:
    con = sqlite3.connect(db)
    jobs = pd.read_sql("SELECT * FROM dim_job", con)
    lab = pd.read_sql("SELECT l.job_ref, t.trade_group || '_hours' k, SUM(l.hours) v FROM fact_labour l "
                      "JOIN dim_trade t USING(trade) GROUP BY 1, 2", con)
    mat = pd.read_sql("SELECT job_ref, material k, SUM(quantity) v FROM fact_material GROUP BY 1, 2", con)
    con.close()
    wide = pd.concat([lab, mat]).pivot(index="job_ref", columns="k", values="v")
    return jobs.join(wide, on="job_ref").fillna({t: 0 for t in TARGETS})


def _models():
    tree = make_pipeline(
        ColumnTransformer([("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), CAT)],
                          remainder="passthrough"),
        HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=10,
                                      l2_regularization=1.0, categorical_features=list(range(len(CAT))),
                                      random_state=0))
    linear = make_pipeline(
        ColumnTransformer([("c", OneHotEncoder(handle_unknown="ignore"), CAT), ("n", StandardScaler(), NUM)]),
        RidgeCV(alphas=np.logspace(-3, 2, 12)))
    return {"gradient boosting": tree, "log-linear": linear}


def mdape(pred, true):
    return float(np.median(np.abs(pred - true) / true))


@dataclass
class Forecaster:
    """One model per target (log scale) plus a calibrated error range."""
    models: dict = field(default_factory=dict)       # target -> fitted pipeline
    kind: dict = field(default_factory=dict)         # target -> which model family won on calibration
    halfwidth: dict = field(default_factory=dict)    # target -> log-scale half-width for the 90% range
    history: pd.DataFrame | None = None              # past jobs, for "similar jobs" explanations

    def predict(self, jobs: pd.DataFrame) -> pd.DataFrame:
        x = features(jobs)
        out = {}
        for t, m in self.models.items():
            p = np.exp(m.predict(x))
            p = np.where(in_scope(jobs, t), p, 0.0)
            w = np.exp(self.halfwidth[t])
            out[t], out[t + "_low"], out[t + "_high"] = p, p / w, p * w
        return pd.DataFrame(out, index=jobs.index)

    def similar(self, job: pd.Series, k: int = 5) -> pd.DataFrame:
        h = self.history
        same = h[(h.sector == job.sector) & (h.package == job.package)]
        if len(same) < k:
            same = h[h.sector == job.sector]
        d = (np.log(same.gia_m2 / job.gia_m2) ** 2 + ((same.floors - job.floors) / 4) ** 2
             + (same.work_type != job.work_type) * 0.5 + (same.spec != job.spec) * 0.3
             + (same.region != job.region) * 0.2)
        return same.assign(distance=d).nsmallest(k, "distance")

    def save(self, path: str | Path):
        joblib.dump(self, path, compress=3)

    @staticmethod
    def load(path: str | Path) -> "Forecaster":
        return joblib.load(path)


def baselines(train: pd.DataFrame, test: pd.DataFrame, target: str) -> dict:
    """What an estimating team might do without a model."""
    tr = train[in_scope(train, target) & (train[target] > 0)]
    rate = (tr[target] / tr.gia_m2).groupby(tr.sector).median()
    out = {"rate per m2 by sector": test.sector.map(rate).values * test.gia_m2.values}
    fc = Forecaster(history=tr)
    out["average of 5 similar past jobs"] = np.array(
        [np.median((s := fc.similar(r))[target] / s.gia_m2) * r.gia_m2 for _, r in test.iterrows()])
    if target in ("electrical_hours", "mechanical_hours"):
        out["tender estimate (estimating team)"] = test[f"tender_{target}"].values
    return out


def train_and_evaluate(db: str | Path = ROOT / "data" / "warehouse.db") -> tuple[Forecaster, dict]:
    jobs = load_jobs(db)
    done = jobs[jobs.status == "Completed"].copy()
    start = pd.to_datetime(done.start_date)
    tr, ca, te = done[start < TRAIN_END], done[(start >= TRAIN_END) & (start < CALIB_END)], done[start >= CALIB_END]
    report = {"jobs": {"train (started before 2023)": len(tr), "calibration (2023)": len(ca),
                       "test (2024 onwards)": len(te)}, "targets": {}}
    final = Forecaster(history=done)
    preds = []
    for t in TARGETS:
        m_tr, m_ca, m_te = (d[in_scope(d, t) & (d[t] > 0)] for d in (tr, ca, te))
        cands = {}
        for name, mdl in _models().items():
            mdl.fit(features(m_tr), np.log(m_tr[t]))
            cands[name] = (mdl, mdape(np.exp(mdl.predict(features(m_ca))), m_ca[t].values))
        best = min(cands, key=lambda k: cands[k][1])  # chosen on calibration jobs, never on test
        mdl = cands[best][0]
        resid = np.abs(np.log(m_ca[t].values) - mdl.predict(features(m_ca)))
        n = len(resid)
        q = float(np.quantile(resid, min(1.0, np.ceil((n + 1) * COVERAGE) / n)))  # split-conformal
        p = np.exp(mdl.predict(features(m_te)))
        y = m_te[t].values
        lo, hi = p / np.exp(q), p * np.exp(q)
        res = {"unit": TARGETS[t], "test_jobs": len(m_te), "model_chosen": best,
               "calibration_mdape": {k: round(v[1] * 100, 1) for k, v in cands.items()},
               "model": {"median_error_pct": round(mdape(p, y) * 100, 1),
                         "within_10pct": round(float(np.mean(np.abs(p - y) / y <= 0.10)), 3),
                         "range_covers_actual": round(float(np.mean((y >= lo) & (y <= hi))), 3),
                         "range_width_pct": round(float((np.exp(q) - np.exp(-q)) * 100), 1)}}
        bl = baselines(m_tr, m_te, t)
        for name, b in bl.items():
            res[name] = {"median_error_pct": round(mdape(b, y) * 100, 1),
                         "within_10pct": round(float(np.mean(np.abs(b - y) / y <= 0.10)), 3)}
        preds.append(pd.DataFrame({"job_ref": m_te.job_ref.values, "target": t, "actual": y, "predicted": p,
                                   "low": lo, "high": hi, **{k: v for k, v in bl.items()}}))
        report["targets"][t] = res
        # the shipped model learns from everything before the test period's end; range stays calibration-based
        full = pd.concat([m_tr, m_ca, m_te])
        fm = _models()[best].fit(features(full), np.log(full[t]))
        final.models[t], final.kind[t], final.halfwidth[t] = fm, best, q
    report["_predictions"] = pd.concat(preds)
    return final, report
