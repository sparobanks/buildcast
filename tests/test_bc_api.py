import json

from fastapi.testclient import TestClient

from buildcast.bc_api import PAGE, BCClient, make_app


def _app(tmp_path, n):
    jobs = [{"No": f"J{i:06d}", "Last_Modified_Date_Time": f"2024-01-{1 + i % 28:02d}T00:00:00Z"} for i in range(n)]
    led = [{"Entry_No": i, "Job_No": "J000001", "Posting_Date": f"2024-{1 + i % 12:02d}-01"} for i in range(n)]
    (tmp_path / "jobs.json").write_text(json.dumps(jobs))
    (tmp_path / "job_ledger_entries.json").write_text(json.dumps(led))
    return TestClient(make_app(tmp_path))


def test_paging_follows_next_link(tmp_path):
    http = _app(tmp_path, PAGE * 2 + 17)
    assert len(BCClient(http).fetch("Jobs")) == PAGE * 2 + 17


def test_incremental_sync_only_returns_newer_rows(tmp_path):
    http = _app(tmp_path, 120)
    rows = BCClient(http).fetch("Job_Ledger_Entries", since="2024-10-01")
    assert rows and all(r["Posting_Date"] > "2024-10-01" for r in rows)


def test_unknown_entity_is_404(tmp_path):
    http = _app(tmp_path, 3)
    assert http.get("/ODataV4/Company('X')/Nope").status_code == 404
