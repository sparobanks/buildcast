"""A stand-in for Business Central's OData web services, plus the client used to sync from it.

Business Central publishes pages such as Job Card and Job Ledger Entries as OData v4 web services. This mock
serves the simulated records in the same shape: `value` arrays, `$top`/`$skip` paging with `@odata.nextLink`,
and `$filter` on a modified-date field for incremental sync. Field names follow Business Central's
(No, Job_No, Posting_Date, Total_Cost_LCY, ...). It has not been tested against a live tenant: a real one needs
OAuth (Microsoft Entra ID) and the company's published web-service names, which the client takes as settings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request

PAGE = 500
ENTITIES = {"Jobs": ("jobs.json", "Last_Modified_Date_Time"),
            "Job_Ledger_Entries": ("job_ledger_entries.json", "Posting_Date")}


def make_app(data_dir: str | Path) -> FastAPI:
    data_dir = Path(data_dir)
    store = {k: json.loads((data_dir / f).read_text()) for k, (f, _) in ENTITIES.items()}
    app = FastAPI(title="Business Central OData (mock)")

    @app.get("/ODataV4/Company('{company}')/{entity}")
    def entity(company: str, entity: str, request: Request, top: int = Query(PAGE, alias="$top"),
               skip: int = Query(0, alias="$skip"), flt: str | None = Query(None, alias="$filter")):
        if entity not in store:
            raise HTTPException(404, f"No web service named {entity}")
        rows = store[entity]
        if flt:  # supports: <Field> gt <value>
            m = re.fullmatch(r"\s*(\w+)\s+gt\s+'?([\w:\-T.Z]+)'?\s*", flt)
            if not m:
                raise HTTPException(400, "Only '<Field> gt <value>' filters are supported by this mock")
            field, value = m.groups()
            rows = [r for r in rows if str(r.get(field, "")) > value]
        page = rows[skip:skip + min(top, PAGE)]
        body = {"@odata.context": f"{request.base_url}ODataV4/$metadata#{entity}", "value": page}
        if skip + len(page) < len(rows):
            nxt = f"{request.url.path}?$top={min(top, PAGE)}&$skip={skip + len(page)}"
            if flt:
                nxt += f"&$filter={flt}"
            body["@odata.nextLink"] = nxt
        return body

    return app


class BCClient:
    """Pages through an OData entity, optionally only records changed since the last sync."""

    def __init__(self, http, company: str = "Demo Contractor", base: str = ""):
        self.http, self.company, self.base = http, company, base.rstrip("/")

    def fetch(self, entity: str, since: str | None = None) -> list[dict]:
        field = ENTITIES[entity][1]
        params = {"$top": PAGE}
        if since:
            params["$filter"] = f"{field} gt '{since}'"
        url = f"{self.base}/ODataV4/Company('{self.company}')/{entity}"
        out = []
        while url:
            r = self.http.get(url, params=params)
            r.raise_for_status()
            body = r.json()
            out.extend(body["value"])
            url, params = body.get("@odata.nextLink"), None
            if url and self.base and url.startswith("/"):
                url = self.base + url
        return out
