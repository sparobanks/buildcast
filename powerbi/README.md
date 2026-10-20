# Building the BuildCast dashboard in Power BI

The warehouse is exported as a star schema in `powerbi/data/` (regenerate with `python scripts/export_powerbi.py`).
These steps take about 20 minutes in Power BI Desktop (free, Windows).

## 1. Load the tables
**Home → Get data → Text/CSV**, and load every file in `powerbi/data/`. Set `dim_date[date]`,
`fact_labour[week_ending]`, `fact_material[order_date]` and `fact_cost[posting_date]` to the **Date** type.

## 2. Relationships (Model view)
| From (many) | To (one) |
|---|---|
| fact_labour[job_ref] | dim_job[job_ref] |
| fact_material[job_ref] | dim_job[job_ref] |
| fact_cost[job_ref] | dim_job[job_ref] |
| fact_labour[trade] | dim_trade[trade] |
| fact_material[material] | dim_material[material] |
| fact_labour[week_ending] | dim_date[date] |
| fact_material[order_date] | dim_date[date] |

Mark `dim_date` as the date table.

## 3. Measures
**Modelling → New measure**, and paste each measure from `measures.dax`.

## 4. The KPI page ("cue cards")
- **Card** visuals: Jobs Completed, Labour Hours per m2, Jobs Over Tender 10%, Material Spend GBP.
- **Bar chart**: dim_job[sector] by Labour Hours per m2.
- **Line chart**: dim_date[year] by Actual vs Tender %.
- **Slicers**: dim_job[region], dim_job[sector], dim_job[work_type].

## 5. Connecting to Business Central directly
In a live set-up, replace the CSVs with **Get data → Dynamics 365 Business Central** (or the warehouse's SQL
database), and schedule a refresh in the Power BI service so the dashboard updates itself.
