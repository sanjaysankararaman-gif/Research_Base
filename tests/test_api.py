"""API (/api), storage, frontend contract and Google Sheets mapping, with simulated sources."""
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import database as db
from backend.integrations.google_sheets import GoogleSheetsNotConfigured, GoogleSheetsSync, build_header, record_to_row
from backend.main import app
from backend.services import build_clients

from .conftest import CROSSREF_CONFERENCE, CROSSREF_OK, OPENALEX_OK, json_response, make_transport

DOI = "10.1234/test.paper"
CONF = "10.1234/conf.paper"


@pytest.fixture
def client():
    transport = make_transport([
        ("api.crossref.org/works/10.1234/test.paper", json_response(CROSSREF_OK)),
        ("api.crossref.org/works/10.1234/conf.paper", json_response(CROSSREF_CONFERENCE)),
        ("api.openalex.org/works/doi:10.1234/test.paper", json_response(OPENALEX_OK)),
        ("semanticscholar", lambda req, n: httpx.Response(500)),  # S2 down -> partial
    ])
    with TestClient(app) as c:
        app.state.clients = build_clients(transport)
        for cl in app.state.clients.values():
            cl.min_interval = 0
        yield c


def wait_for(c, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = c.get(f"/api/status/{job_id}").json()
        if s["status"] == "completed":
            return s
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] == "ok"
    assert "SCOPUS_API_KEY" in r.json()["sources"]["scopus"]


def test_add_paper_returns_flat_fields(client):
    r = client.post("/api/papers", json={"doi": f"https://doi.org/{DOI.upper()}"})
    assert r.status_code == 201
    p = r.json()
    assert isinstance(p["id"], int) and p["doi"] == DOI and p["status"] == "partial"
    f = p["fields"]
    assert f["title"] == "A Test Paper"
    assert f["publication_type"] == "Journal"
    assert f["authors"] == ["Ada Lovelace", "Alan Turing"]          # published order
    assert f["publication_year"] == 2020 and f["publisher"] == "Test Press"
    assert f["publication_url"] == "https://doi.org/10.1234/test.paper"
    assert f["journal"] == "Journal of Tests" and f["issn"] == "1234-5678 (ISSN)"
    assert (f["volume"], f["issue"], f["page_range"]) == ("12", "3", "100-110")
    assert f["quartile"] is None and f["impact_factor"] is None        # not configured -> null, never guessed
    assert "Scopus: not verified" in f["indexing"] and "PubMed" in f["indexing"]
    assert f["pdf_available"] is True and f["pdf_url"] == "https://example.org/paper.pdf"
    assert f["citation_counts"] == {"crossref": 0, "openalex": 7, "semantic_scholar": None}
    assert p["provenance"]["title"]["source"] == "crossref"
    assert "SJR_CSV_PATH" in p["provenance"]["quartile"]["reason"]

    # Details page lookup by id, and repeat add returns the stored paper.
    assert client.get(f"/api/papers/{p['id']}").json()["fields"]["title"] == "A Test Paper"
    again = client.post("/api/papers", json={"doi": DOI})
    assert again.status_code == 200 and again.json()["already_in_library"] is True and again.json()["id"] == p["id"]


def test_add_conference_paper(client):
    p = client.post("/api/papers", json={"doi": CONF}).json()
    f = p["fields"]
    assert f["publication_type"] == "Conference"
    assert f["conference_name"] == "TC '19: The 25th Test Conference"
    assert f["conference_acronym"] == "TC '19"
    assert f["conference_dates"] == "2019-08-04 to 2019-08-08"
    assert f["conference_location"] == "Chennai, India"
    assert f["proceedings_title"].startswith("Proceedings of the 25th Test Conference")
    assert f["journal"] is None


def test_add_paper_errors(client):
    assert client.post("/api/papers", json={"doi": "not a doi"}).status_code == 400
    assert client.post("/api/papers", json={}).status_code == 422
    r = client.post("/api/papers", json={"doi": "10.5555/missing.paper"})
    # crossref/openalex: 404, S2: 500 -> all failed -> 502, nothing stored
    assert r.status_code == 502
    assert client.get("/api/papers").json()["total"] == 0


def test_not_found_doi_is_404_and_not_stored():
    t = make_transport([])  # every source answers 404
    with TestClient(app) as c:
        app.state.clients = build_clients(t)
        r = c.post("/api/papers", json={"doi": "10.9999/nope"})
        assert r.status_code == 404 and "check the DOI" in r.json()["detail"]
        assert c.get("/api/papers").json()["total"] == 0


def test_library_search_filter_sort_delete(client):
    a = client.post("/api/papers", json={"doi": DOI}).json()
    client.post("/api/papers", json={"doi": CONF})
    lib = client.get("/api/papers").json()
    assert lib["total"] == 2
    row = next(i for i in lib["items"] if i["doi"] == DOI)
    assert set(row) >= {"id", "title", "authors_text", "publication_year", "publication_type", "doi"}
    assert client.get("/api/papers", params={"type": "Conference"}).json()["items"][0]["doi"] == CONF
    assert client.get("/api/papers", params={"type": "Journal", "search": "lovelace"}).json()["total"] == 1
    assert client.get("/api/papers", params={"search": "hopper"}).json()["total"] == 1
    page = client.get("/api/papers", params={"page_size": 1, "page": 2, "sort": "doi", "order": "asc"}).json()
    assert page["pages"] == 2 and page["items"][0]["doi"] == "10.1234/test.paper"
    assert client.get("/api/papers", params={"sort": "drop table"}).status_code == 422
    assert client.get("/api/papers", params={"type": "Magazine"}).status_code == 422
    assert client.delete(f"/api/papers/{a['id']}").status_code == 204
    assert client.get(f"/api/papers/{a['id']}").status_code == 404
    assert db.get_metric_rows(DOI) == []                  # cascade removed per-source values


def test_bulk_extract_flow(client):
    r = client.post("/api/extract", json={"text": f"{DOI}\n{DOI.upper()}\nbad\n{CONF}"})
    assert r.status_code == 202
    body = r.json()
    assert body["dois"] == [DOI, CONF] and len(body["duplicates"]) == 1 and len(body["invalid"]) == 1
    status = wait_for(client, body["job_id"])
    items = {i["doi"]: i for i in status["items"]}
    assert items[DOI]["status"] == "partial"
    assert items[DOI]["sources"]["semantic_scholar"] == "error"
    assert items[DOI]["sources"]["scopus"] == "unavailable"
    results = client.get(f"/api/results/{body['job_id']}").json()["items"]
    assert {r["doi"] for r in results} == {DOI, CONF}


def test_storage_keeps_zero_vs_unavailable(client):
    client.post("/api/papers", json={"doi": DOI})
    rows = {(m["metric_key"], m["source"]): m for m in db.get_metric_rows(DOI)}
    assert rows[("citation_count", "crossref")]["available"] is True and rows[("citation_count", "crossref")]["value"] == 0
    assert rows[("citation_count", "semantic_scholar")]["available"] is False
    assert all(m["retrieved_at"] for m in rows.values())


def test_unknown_ids(client):
    assert client.get("/api/status/abc123").status_code == 404
    assert client.get("/api/papers/999").status_code == 404
    assert client.get("/api/papers/by-doi/10.9999/none").status_code == 404
    assert client.get("/api/papers/by-doi/garbage").status_code == 400


def test_errors_do_not_leak_traces(client, monkeypatch):
    def explode(**kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(db, "list_papers", explode)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/papers")
    assert r.status_code == 500 and "secret" not in r.text


def test_cors_allows_frontend_origin(client):
    r = client.options("/api/papers", headers={"Origin": "http://127.0.0.1:5500",
                                               "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") in ("*", "http://127.0.0.1:5500")


def test_google_sheets_row_mapping(client):
    client.post("/api/papers", json={"doi": DOI})
    rec = db.get_paper(DOI)
    header, row = build_header(), record_to_row(rec)
    assert len(header) == len(row)
    cells = dict(zip(header, row))
    assert cells["DOI"] == DOI
    assert cells["Publication Title"] == "A Test Paper" and cells["Publication Title Source"] == "Crossref"
    assert cells["Author Names"] == "Ada Lovelace; Alan Turing"
    assert cells["Quartile"] == ""                     # unavailable -> blank cell
    with pytest.raises(GoogleSheetsNotConfigured):
        GoogleSheetsSync().append_records([rec])


def test_excel_file_is_written_and_kept_in_sync(client):
    from openpyxl import load_workbook
    from backend.integrations.excel_export import excel_path

    a = client.post("/api/papers", json={"doi": DOI}).json()
    assert a["saved_to_excel"] is True
    client.post("/api/papers", json={"doi": CONF})
    client.post("/api/papers", json={"doi": DOI, "refresh": True})   # re-fetch must not duplicate the row

    ws = load_workbook(excel_path()).active
    rows = list(ws.iter_rows(values_only=True))
    header, data = rows[0], rows[1:]
    assert header[:4] == ("S. No.", "Publication Title", "Publication Type", "Author Names")
    assert len(data) == 2
    journal = dict(zip(header, data[0]))
    conference = dict(zip(header, data[1]))
    assert journal["DOI"] == DOI and journal["Journal Name"] == "Journal of Tests"
    assert journal["Volume"] == "12" and journal["Quartile"] == "Not available"
    assert journal["Conference Name"] == "-"                       # not applicable to a journal paper
    assert conference["Conference Location"] == "Chennai, India" and conference["Journal Name"] == "-"

    # Deleting from the library removes the Excel row; the download endpoint serves the file.
    client.delete(f"/api/papers/{a['id']}")
    assert len(list(load_workbook(excel_path()).active.iter_rows(values_only=True))) == 2
    r = client.get("/api/excel")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]


def test_root_without_or_with_frontend(client):
    """The pages come from the frontend branch; without UI/ the root explains that."""
    from backend.config import settings

    r = client.get("/")
    assert r.status_code == 200
    if settings.frontend_dir.is_dir():
        for page in ("/inventory.html", "/details.html"):
            assert client.get(page).status_code == 200, page
    else:
        assert "frontend branch" in r.json()["message"]


def test_excel_download_built_from_database(client):
    from io import BytesIO
    from openpyxl import load_workbook

    client.post("/api/papers", json={"doi": DOI})
    r = client.get("/api/excel")
    rows = list(load_workbook(BytesIO(r.content)).active.iter_rows(values_only=True))
    assert len(rows) == 2 and rows[1][6] == DOI       # header + one paper; column 7 = DOI


def test_health_reports_storage(client):
    h = client.get("/api/health").json()
    assert h["storage_is_permanent"] is True and h["storage"]


def test_bulk_job_never_left_running(client, monkeypatch):
    """If anything fails after retrieval, the DOI is marked failed, not left 'running'."""
    from backend.processing import pipeline

    monkeypatch.setattr(pipeline, "_message", lambda record: 1 / 0)
    job = client.post("/api/extract", json={"doi": DOI}).json()["job_id"]
    item = wait_for(client, job)["items"][0]
    assert item["status"] == "failed"
