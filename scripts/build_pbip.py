"""Build a Power BI Project (PBIP) for the BuildCast warehouse: semantic model + a one-page KPI report.

    python scripts/build_pbip.py        ->  powerbi/BuildCast.pbip (+ .SemanticModel and .Report folders)

PBIP is Power BI's text-based project format: Power BI Desktop opens BuildCast.pbip like a .pbix. The model
loads the CSVs in powerbi/data through a DataFolder parameter (set it to that folder's full path), defines the
star-schema relationships and the KPI measures, and the report lays out cue cards, two charts and slicers.
"""
import json
import uuid
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PBI = ROOT / "powerbi"
DATA = PBI / "data"
NAME = "BuildCast"

MEASURES = [  # (name, DAX, format) -- also written to powerbi/measures.dax
    ("Labour Hours", "SUM ( fact_labour[hours] )", "#,0"),
    ("Floor Area m2", "SUM ( dim_job[gia_m2] )", "#,0"),
    ("Labour Hours per m2", "CALCULATE ( DIVIDE ( [Labour Hours], [Floor Area m2] ), dim_job[status] = \"Completed\" )",
     "0.00"),
    ("Tender Labour Hours", "SUM ( dim_job[tender_electrical_hours] ) + SUM ( dim_job[tender_mechanical_hours] )", "#,0"),
    ("Actual vs Tender %",
     "VAR actual = CALCULATE ( [Labour Hours], dim_job[status] = \"Completed\" )\n"
     "VAR tender = CALCULATE ( [Tender Labour Hours], dim_job[status] = \"Completed\" )\n"
     "RETURN DIVIDE ( actual - tender, tender )", "0.0%"),
    ("Jobs Completed", "CALCULATE ( DISTINCTCOUNT ( dim_job[job_ref] ), dim_job[status] = \"Completed\" )", "#,0"),
    ("Jobs Over Tender 10%",
     "VAR jobs = ADDCOLUMNS ( CALCULATETABLE ( VALUES ( dim_job[job_ref] ), dim_job[status] = \"Completed\" ), "
     "\"ratio\", [Actual vs Tender %] )\nRETURN DIVIDE ( COUNTROWS ( FILTER ( jobs, [ratio] > 0.10 ) ), COUNTROWS ( jobs ) )",
     "0%"),
    ("Material Spend GBP", "SUM ( fact_material[cost_gbp] )", "\u00a3#,0"),
    ("Labour Cost GBP", "SUM ( fact_cost[cost_gbp] )", "\u00a3#,0"),
]
RELATIONSHIPS = [("fact_labour", "job_ref", "dim_job", "job_ref"), ("fact_material", "job_ref", "dim_job", "job_ref"),
                 ("fact_cost", "job_ref", "dim_job", "job_ref"), ("fact_labour", "trade", "dim_trade", "trade"),
                 ("fact_material", "material", "dim_material", "material"),
                 ("fact_labour", "week_ending", "dim_date", "date"), ("fact_material", "order_date", "dim_date", "date"),
                 ("fact_cost", "posting_date", "dim_date", "date")]
DATE_COLS = {"date", "week_ending", "order_date", "posting_date", "start_date"}


def column_types(df: pd.DataFrame):
    out = []
    for c, dt in df.dtypes.items():
        if c in DATE_COLS:
            out.append((c, "dateTime", "type date"))
        elif pd.api.types.is_integer_dtype(dt):
            out.append((c, "int64", "Int64.Type"))
        elif pd.api.types.is_float_dtype(dt):
            out.append((c, "double", "type number"))
        else:
            out.append((c, "string", "type text"))
    return out


def table(name: str) -> dict:
    df = pd.read_csv(DATA / f"{name}.csv", nrows=2000)
    cols = column_types(df)
    numeric_sum = {"hours", "quantity", "cost_gbp", "gia_m2", "tender_electrical_hours", "tender_mechanical_hours",
                   "tender_value_gbp"}
    columns = []
    for c, dtype, _ in cols:
        col = {"name": c, "dataType": dtype, "sourceColumn": c,
               "summarizeBy": "sum" if c in numeric_sum else "none"}
        if dtype == "dateTime":
            col["formatString"] = "Short Date"
        if name == "dim_date" and c == "date":
            col["isKey"] = True
        columns.append(col)
    types = ", ".join(f'{{"{c}", {m}}}' for c, _, m in cols)
    m = ["let",
         f'    Source = Csv.Document(File.Contents(DataFolder & "{name}.csv"), [Delimiter=",", Encoding=65001, '
         'QuoteStyle=QuoteStyle.Csv]),',
         "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),",
         f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-GB")',
         "in",
         "    Typed"]
    t = {"name": name, "columns": columns,
         "partitions": [{"name": name, "mode": "import", "source": {"type": "m", "expression": m}}]}
    if name == "dim_date":
        t["dataCategory"] = "Time"
    if name == "dim_job":
        t["measures"] = [{"name": n, "expression": e, "formatString": f} for n, e, f in MEASURES]
    return t


def semantic_model(folder: Path):
    names = ["dim_job", "dim_trade", "dim_material", "dim_date", "fact_labour", "fact_material", "fact_cost"]
    model = {
        "name": NAME, "compatibilityLevel": 1550,
        "model": {
            "culture": "en-GB", "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "tables": [table(n) for n in names],
            "relationships": [{"name": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{a}.{b}->{c}.{d}")), "fromTable": a,
                               "fromColumn": b, "toTable": c, "toColumn": d} for a, b, c, d in RELATIONSHIPS],
            "expressions": [{"name": "DataFolder", "kind": "m",
                             "expression": '"C:\\BuildCast\\powerbi\\data\\" meta [IsParameterQuery=true, '
                                           'Type="Text", IsParameterQueryRequired=true]'}],
            "annotations": [{"name": "PBI_QueryOrder", "value": json.dumps(["DataFolder"] + names)}],
        }}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bim").write_text(json.dumps(model, indent=2))
    (folder / "definition.pbism").write_text(json.dumps({"version": "1.0", "settings": {}}, indent=2))


# ---- report ------------------------------------------------------------------------------------------------
def _ref(entity: str, prop: str, measure: bool) -> tuple[dict, str]:
    kind = "Measure" if measure else "Column"
    name = f"{entity}.{prop}"
    return {kind: {"Expression": {"SourceRef": {"Source": entity[0] + entity[-1]}}, "Property": prop}, "Name": name}, name


def visual(vid: str, vtype: str, x, y, w, h, roles: dict, title: str | None = None, z: int = 0) -> dict:
    """roles: role -> list of (entity, property, is_measure)."""
    entities = sorted({e for items in roles.values() for e, _, _ in items})
    select, projections = [], {}
    for role, items in roles.items():
        projections[role] = []
        for e, p, m in items:
            sel, name = _ref(e, p, m)
            select.append(sel)
            projections[role].append({"queryRef": name})
    single = {"visualType": vtype, "projections": projections,
              "prototypeQuery": {"Version": 2, "From": [{"Name": e[0] + e[-1], "Entity": e, "Type": 0} for e in entities],
                                 "Select": select},
              "drillFilterOtherVisuals": True}
    if title:
        single["vcObjects"] = {"title": [{"properties": {
            "show": {"expr": {"Literal": {"Value": "true"}}},
            "text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]}
    cfg = {"name": vid, "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
           "singleVisual": single}
    return {"x": x, "y": y, "z": z, "width": w, "height": h, "config": json.dumps(cfg), "filters": "[]"}


def textbox(vid: str, text: str, x, y, w, h, size: str = "20pt") -> dict:
    cfg = {"name": vid, "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}],
           "singleVisual": {"visualType": "textbox", "drillFilterOtherVisuals": True, "objects": {"general": [
               {"properties": {"paragraphs": [{"textRuns": [{"value": text, "textStyle": {
                   "fontWeight": "bold", "fontSize": size}}]}]}}]}}}
    return {"x": x, "y": y, "z": 0, "width": w, "height": h, "config": json.dumps(cfg), "filters": "[]"}


def report(folder: Path):
    J = "dim_job"
    vcs = [
        textbox("title", "BuildCast: M&E job performance (simulated data)", 20, 10, 900, 50),
        visual("card_jobs", "card", 20, 70, 290, 110, {"Values": [(J, "Jobs Completed", True)]}),
        visual("card_rate", "card", 330, 70, 290, 110, {"Values": [(J, "Labour Hours per m2", True)]}),
        visual("card_over", "card", 640, 70, 290, 110, {"Values": [(J, "Jobs Over Tender 10%", True)]}),
        visual("card_spend", "card", 950, 70, 310, 110, {"Values": [(J, "Material Spend GBP", True)]}),
        visual("bar_sector", "clusteredBarChart", 20, 200, 600, 300,
               {"Category": [(J, "sector", False)], "Y": [(J, "Labour Hours per m2", True)]},
               "Labour hours per m² by sector"),
        visual("line_year", "lineChart", 640, 200, 620, 300,
               {"Category": [(J, "start_year", False)], "Y": [(J, "Actual vs Tender %", True)]},
               "Actual labour against tender, by start year"),
        visual("col_material", "clusteredColumnChart", 20, 520, 820, 190,
               {"Category": [("dim_material", "material", False)], "Y": [(J, "Material Spend GBP", True)]},
               "Material spend"),
        visual("slicer_region", "slicer", 860, 520, 190, 190, {"Values": [(J, "region", False)]}),
        visual("slicer_work", "slicer", 1070, 520, 190, 190, {"Values": [(J, "work_type", False)]}),
    ]
    rep = {"config": json.dumps({"version": "5.37", "activeSectionIndex": 0}), "layoutOptimization": 0,
           "resourcePackages": [], "filters": "[]",
           "sections": [{"name": "ReportSectionOverview", "displayName": "Overview", "displayOption": 1,
                         "width": 1280, "height": 720, "filters": "[]", "config": "{}", "visualContainers": vcs}]}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(rep, indent=2))
    (folder / "definition.pbir").write_text(json.dumps(
        {"version": "1.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}, "byConnection": None}},
        indent=2))


def main():
    semantic_model(PBI / f"{NAME}.SemanticModel")
    report(PBI / f"{NAME}.Report")
    (PBI / f"{NAME}.pbip").write_text(json.dumps(
        {"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
         "settings": {"enableAutoRecovery": True}}, indent=2))
    (PBI / ".gitignore").write_text("**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    dax = "// BuildCast KPI measures (also defined in the Power BI project's semantic model).\n\n"
    dax += "\n\n".join(f"{n} =\n{e}" for n, e, _ in MEASURES) + "\n"
    (PBI / "measures.dax").write_text(dax)
    print("written", sorted(str(p.relative_to(PBI)) for p in PBI.rglob("*") if p.is_file() and "data" not in p.parts))


if __name__ == "__main__":
    main()
