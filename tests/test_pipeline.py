import json
import sqlite3

import pytest

from buildcast.pipeline import audit, canon_job, classify_material, units_per_order_unit


@pytest.mark.parametrize("raw", ["MEP-2021-0143", "mep-2021-143", "MEP20210143", "21/143", " MEP-2021-143 "])
def test_job_codes_all_resolve_to_one_key(raw):
    assert canon_job(raw) == "MEP-2021-0143"


def test_unreadable_job_code():
    assert canon_job("JOB 143") is None


@pytest.mark.parametrize("desc,mat", [("Cable tray 300mm perforated 3m length", "containment_m"),
                                      ("2.5mm T&E cable 100m drum", "cable_m"),
                                      ("Concealed sprinkler head K80", "sprinkler_heads"),
                                      ("Copper tube 22mm 3m", "pipework_m")])
def test_material_rules(desc, mat):
    assert classify_material(desc)[0] == mat


def test_pack_sizes():
    assert units_per_order_unit("2.5mm T&E cable 100m drum", "drum") == 100
    assert units_per_order_unit("Copper tube 22mm 3m", "length") == 3
    assert units_per_order_unit("Cat6 LSZH data cable 305m box", "box") == 305
    assert units_per_order_unit("LED panel 600x600", "each") == 1


def test_pipeline_loads_star_schema_and_logs_rules(small_company):
    d, res = small_company
    con = sqlite3.connect(d / "warehouse.db")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"dim_job", "dim_trade", "dim_material", "fact_labour", "fact_material", "fact_cost", "dq_log"} <= tables
    assert con.execute("SELECT COUNT(*) FROM dim_job").fetchone()[0] == 60
    con.close()
    rules = set(res["dq"].rule)
    assert "duplicate submissions removed" in rules and "floor area converted from sq ft to m2" in rules


def test_unknown_wording_is_held_not_guessed(small_company):
    d, res = small_company
    a = audit(res, d, d / "warehouse.db")["material_identification"]
    assert a["silently_wrong"] == 0
    assert a["held_for_review"] > 0
    assert (d / "review" / "unmapped_descriptions.csv").exists()


def test_business_central_matches_are_never_wrong(small_company):
    d, res = small_company
    m = audit(res, d, d / "warehouse.db")["bc_job_matching"]
    assert m["wrong_matches"] == 0 and m["matched"] >= 0.9 * m["jobs"]
