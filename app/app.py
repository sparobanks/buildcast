"""BuildCast estimator app and dashboard.

    streamlit run app/app.py
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from buildcast import kpis  # noqa: E402
from buildcast.model import LABELS, TARGETS, Forecaster  # noqa: E402

st.set_page_config(page_title="BuildCast", page_icon="🏗️", layout="wide")
DB = ROOT / "data" / "warehouse.db"
RATES = json.loads((ROOT / "config" / "rates.json").read_text())
BLUE, ORANGE, GREY = "#2a78d6", "#eb6834", "#8a8983"


@st.cache_resource
def forecaster():
    return Forecaster.load(ROOT / "models" / "forecaster.joblib")


@st.cache_data
def summary():
    return kpis.job_summary(DB)


fc = forecaster()
jobs = summary()

st.markdown("## 🏗️ BuildCast")
st.caption("Predicts the labour and materials a new M&E job will need, from the company's own past jobs, and "
           "brings scattered systems together into one set of reliable figures. Simulated data, not real company data.")
tab1, tab2, tab3, tab4 = st.tabs(["Estimate a new job", "Dashboard", "Data quality", "How it works"])

with tab1:
    with st.form("job"):
        c = st.columns(4)
        sector = c[0].selectbox("Sector", sorted(jobs.sector.dropna().unique()), index=1)
        region = c[1].selectbox("Region", ["NI", "GB", "ROI"])
        work = c[2].selectbox("Type of work", ["New build", "Refurbishment", "Fit-out"])
        spec = c[3].selectbox("Specification", ["Standard", "High", "Premium"])
        c = st.columns(4)
        gia = c[0].number_input("Floor area (m²)", 300, 60000, 5000, 100)
        floors = c[1].number_input("Floors", 1, 20, 3)
        package = c[2].selectbox("Scope", ["Full M&E", "Electrical only", "Mechanical only"])
        weeks = c[3].number_input("Planned programme (weeks)", 8, 160, 36)
        c = st.columns(4)
        sprinklers = c[0].checkbox("Sprinklers in scope", value=False, disabled=package == "Electrical only")
        occupied = c[1].checkbox("Occupied site", value=False)
        offsite = c[2].slider("Offsite prefabrication (%)", 0, 45, 10)
        start = c[3].date_input("Start date", date(2026, 11, 2))
        go = st.form_submit_button("Estimate", type="primary")
    job = pd.DataFrame([{"sector": sector, "region": region, "work_type": work, "spec": spec, "gia_m2": gia,
                         "floors": floors, "package": package, "sprinklers": int(sprinklers and package != "Electrical only"),
                         "occupied": int(occupied), "offsite_pct": offsite / 100, "planned_weeks": weeks,
                         "start_date": start.isoformat()}])
    pred = fc.predict(job).iloc[0]
    rows = []
    for t, unit in TARGETS.items():
        if pred[t] <= 0:
            continue
        rows.append({"Item": LABELS[t], "Prediction": f"{pred[t]:,.0f} {unit}",
                     "Likely range (90%)": f"{pred[t + '_low']:,.0f} to {pred[t + '_high']:,.0f}",
                     "Per m²": f"{pred[t] / gia:.2f}"})
    hours = pred.electrical_hours + pred.mechanical_hours
    m = st.columns(3)
    m[0].metric("Total labour", f"{hours:,.0f} hours")
    m[1].metric("Labour cost (at £%.0f/hr)" % RATES["labour_rate_gbp"], f"£{hours * RATES['labour_rate_gbp']:,.0f}")
    m[2].metric("Labour per m²", f"{hours / gia:.2f} hours")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption("The range is set so that, on jobs the model had not seen, about 9 in 10 actual figures landed "
               "inside it.")
    st.markdown("**Similar past jobs** (why the model says what it says)")
    sim = fc.similar(job.iloc[0], k=5)
    sim_show = sim.assign(**{"Actual labour hours": sim.electrical_hours + sim.mechanical_hours})
    sim_show["Hours per m²"] = (sim_show["Actual labour hours"] / sim_show.gia_m2).map("{:.2f}".format)
    sim_show["Actual labour hours"] = sim_show["Actual labour hours"].map("{:,.0f}".format)
    sim_show["gia_m2"] = sim_show.gia_m2.map("{:,.0f}".format)
    st.dataframe(sim_show[["job_ref", "project_name", "region", "work_type", "spec", "gia_m2", "floors",
                           "Actual labour hours", "Hours per m²"]].rename(
        columns={"job_ref": "Job", "project_name": "Project", "region": "Region", "work_type": "Work",
                 "spec": "Spec", "gia_m2": "Floor area (m²)", "floors": "Floors"}),
                 hide_index=True, use_container_width=True)

with tab2:
    cards = kpis.cue_cards(jobs)
    cols = st.columns(4)
    for i, (k, v) in enumerate(cards.items()):
        cols[i % 4].metric(k, v)
    left, right = st.columns(2)
    with left:
        bs = kpis.by_sector(jobs).reset_index()
        st.markdown("**Labour hours per m² by sector** (median, completed jobs)")
        st.altair_chart(alt.Chart(bs).mark_bar(color=BLUE, cornerRadiusEnd=3).encode(
            x=alt.X("hours_per_m2:Q", title="Hours per m²"), y=alt.Y("sector:N", sort="-x", title=None),
            tooltip=["sector", alt.Tooltip("hours_per_m2:Q", format=".2f"), "jobs"]), use_container_width=True)
    with right:
        ta = kpis.tender_accuracy_by_year(jobs).reset_index()
        st.markdown("**Actual labour against tender estimate** (median, by start year; recent years include only "
                    "jobs already finished)")
        st.altair_chart(alt.Chart(ta).mark_line(point=True, color=ORANGE).encode(
            x=alt.X("year:O", title="Start year"),
            y=alt.Y("median_actual_vs_tender:Q", title="Actual vs tender", axis=alt.Axis(format="%")),
            tooltip=["year", alt.Tooltip("median_actual_vs_tender:Q", format=".0%"), "jobs"]), use_container_width=True)
    wk = kpis.weekly_hours(DB).reset_index().melt("month", var_name="trade", value_name="hours")
    st.markdown("**Labour hours booked per month**")
    st.altair_chart(alt.Chart(wk).mark_line().encode(
        x=alt.X("month:T", title=None), y=alt.Y("hours:Q", title="Hours"),
        color=alt.Color("trade:N", scale=alt.Scale(range=[BLUE, ORANGE]), title="Trade"),
        tooltip=["month", "trade", alt.Tooltip("hours:Q", format=",.0f")]), use_container_width=True)

with tab3:
    con = sqlite3.connect(DB)
    dq = pd.read_sql("SELECT source AS System, rule AS 'What was fixed or held back', rows AS Rows FROM dq_log", con)
    con.close()
    audit = json.loads((ROOT / "reports" / "pipeline_audit.json").read_text())
    a = audit["after_review"]
    c = st.columns(4)
    c[0].metric("Business Central jobs matched", f"{a['bc_job_matching']['matched']:,} / {a['bc_job_matching']['jobs']:,}")
    c[1].metric("Wrong matches", a["bc_job_matching"]["wrong_matches"])
    c[2].metric("Purchase-order lines silently wrong", a["material_identification"]["silently_wrong"])
    c[3].metric("New descriptions reviewed by a person", audit["review"]["distinct_descriptions"])
    st.dataframe(dq, hide_index=True, use_container_width=True)

with tab4:
    st.image(str(ROOT / "docs" / "img" / "data_flow.png"), use_container_width=True)
    st.markdown("See the README for how the data is joined, how the predictions are tested, and their limits.")
