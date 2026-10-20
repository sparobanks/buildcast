import numpy as np
import pandas as pd

from buildcast.model import Forecaster, in_scope
from buildcast.kpis import cue_cards, job_summary


def test_shipped_model_predicts_with_ranges_and_respects_scope():
    from pathlib import Path
    fc = Forecaster.load(Path(__file__).resolve().parent.parent / "models" / "forecaster.joblib")
    job = pd.DataFrame([{"sector": "Education", "region": "NI", "work_type": "New build", "spec": "Standard",
                         "gia_m2": 4000, "floors": 2, "package": "Electrical only", "sprinklers": 0, "occupied": 0,
                         "offsite_pct": 0.1, "planned_weeks": 30, "start_date": "2026-11-01"}])
    p = fc.predict(job).iloc[0]
    assert p.electrical_hours > 0 and p.mechanical_hours == 0 and p.sprinkler_heads == 0
    assert p.electrical_hours_low < p.electrical_hours < p.electrical_hours_high
    bigger = fc.predict(job.assign(gia_m2=8000)).iloc[0]
    assert bigger.electrical_hours > p.electrical_hours  # more floor area, more work


def test_scope_rules():
    df = pd.DataFrame({"package": ["Full M&E", "Electrical only", "Mechanical only"], "sprinklers": [1, 0, 0]})
    assert list(in_scope(df, "cable_m")) == [True, True, False]
    assert list(in_scope(df, "pipework_m")) == [True, False, True]
    assert list(in_scope(df, "sprinkler_heads")) == [True, False, False]


def test_cue_cards(small_company):
    d, _ = small_company
    cards = cue_cards(job_summary(d / "warehouse.db"))
    assert "Jobs completed" in cards and cards["Jobs completed"] != "0"
