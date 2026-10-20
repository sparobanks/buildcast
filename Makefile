data:       ## simulate the four source systems (~2 min)
	python -m buildcast.simulate

pipeline:   ## integrate into the SQL warehouse, review queue, audit against the answer key
	python scripts/run_pipeline.py

model:      ## train and test the forecasting models
	python scripts/train.py

powerbi:    ## export the star schema and build the Power BI project
	python scripts/export_powerbi.py
	python scripts/build_pbip.py

figures:
	python scripts/figures.py

all: data pipeline model powerbi figures

test:
	pytest -q

app:
	streamlit run app/app.py

.PHONY: data pipeline model powerbi figures all test app
