"""Test setup: isolated database and fast retries.

Environment must be set before `backend` is imported, because settings are
read at import time.
"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="doi-extractor-tests-"))
os.environ["DATABASE_PATH"] = str(_TMP / "test.db")
os.environ["LOG_DIR"] = str(_TMP / "logs")
os.environ["EXCEL_PATH"] = str(_TMP / "research_papers.xlsx")
os.environ.setdefault("MAX_RETRY_WAIT_SECONDS", "0.05")
# Tests must not depend on the developer's .env (keys, ranking files).
for _var in ("SJR_CSV_PATH", "IMPACT_FACTOR_CSV_PATH", "SCOPUS_API_KEY", "WOS_API_KEY",
             "SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY"):
    os.environ[_var] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402

try:  # the database exists on the backend branch, not on the scraping branch
    from backend import database as db  # noqa: E402
except ImportError:  # pragma: no cover
    db = None

CROSSREF_OK = {
    "status": "ok",
    "message": {
        "DOI": "10.1234/test.paper",
        "title": ["A Test Paper"],
        "author": [{"given": "Ada", "family": "Lovelace", "affiliation": [{"name": "Analytical College"}]},
                   {"given": "Alan", "family": "Turing", "affiliation": []}],
        "published": {"date-parts": [[2020, 5, 17]]},
        "container-title": ["Journal of Tests"],
        "publisher": "Test Press",
        "volume": "12",
        "issue": "3",
        "page": "100-110",
        "ISSN": ["1234-5678"],
        "issn-type": [{"type": "print", "value": "1234-5678"}],
        "type": "journal-article",
        "abstract": "<jats:p>An <jats:italic>abstract</jats:italic>.</jats:p>",
        "is-referenced-by-count": 0,
        "references-count": 25,
        "URL": "https://doi.org/10.1234/test.paper",
    },
}

CROSSREF_CONFERENCE = {
    "status": "ok",
    "message": {
        "DOI": "10.1234/conf.paper",
        "type": "proceedings-article",
        "title": ["A Conference Paper"],
        "author": [{"given": "Grace", "family": "Hopper"}],
        "published": {"date-parts": [[2019, 8, 4]]},
        "container-title": ["Proceedings of the 25th Test Conference &amp; Workshop"],
        "publisher": "ACM",
        "page": "10-19",
        "ISBN": ["9781450362016"],
        "event": {"name": "TC '19: The 25th Test Conference", "acronym": "TC '19",
                  "location": "Chennai, India",
                  "start": {"date-parts": [[2019, 8, 4]]}, "end": {"date-parts": [[2019, 8, 8]]}},
        "resource": {"primary": {"URL": "https://dl.acm.org/doi/10.1234/conf.paper"}},
    },
}

OPENALEX_OK = {
    "id": "https://openalex.org/W123",
    "display_name": "A Test Paper",
    "publication_year": 2020,
    "publication_date": "2020-05-17",
    "type": "article",
    "cited_by_count": 7,
    "referenced_works_count": 24,
    "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    "authorships": [
        {"author": {"display_name": "Ada Lovelace"},
         "institutions": [{"id": "I1", "display_name": "Analytical College", "country_code": "GB", "type": "education"}]},
    ],
    "institutions_distinct_count": 1,
    "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
    "primary_location": {"source": {"display_name": "Journal of Tests", "issn": ["1234-5678"]}},
    "biblio": {"volume": "12", "issue": "3", "first_page": "100", "last_page": "110"},
    "primary_topic": {"display_name": "Testing", "field": {"display_name": "Computer Science"}},
    "topics": [{"display_name": "Testing"}],
    "fwci": 0.0,
    "abstract_inverted_index": {"An": [0], "abstract.": [1]},
    "indexed_in": ["crossref", "pubmed"],
    "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
}

S2_OK = {
    "paperId": "abc123",
    "title": "A Test Paper",
    "year": 2020,
    "authors": [{"name": "Ada Lovelace"}],
    "citationCount": 5,
    "influentialCitationCount": 0,
    "referenceCount": 25,
    "fieldsOfStudy": ["Computer Science"],
    "isOpenAccess": False,
    "venue": "Journal of Tests",
}


def make_transport(routes):
    """routes: list of (substring, handler). handler(request, call_no) -> httpx.Response."""
    calls = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, fn in routes:
            if key in url:
                calls[key] = calls.get(key, 0) + 1
                return fn(request, calls[key])
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    transport.calls = calls
    return transport


def json_response(payload, status=200, headers=None):
    return lambda request, n: httpx.Response(status, json=payload, headers=headers or {})


@pytest.fixture(autouse=True)
def fresh_db():
    if db is None:
        yield
        return
    db.init_db()
    with db.connect() as conn:
        for table in ("metric_values", "source_fetches", "source_cache", "job_items", "jobs", "papers"):
            conn.execute(f"DELETE FROM {table}")
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the real Crossref / OpenAlex / Semantic Scholar APIs")
