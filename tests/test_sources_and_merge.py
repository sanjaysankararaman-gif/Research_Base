"""Source clients (retry / rate-limit / error handling) and merging rules.

HTTP is simulated with httpx.MockTransport here so failure modes (429, 500,
timeouts) can be reproduced deterministically. Real-API behaviour is covered
in test_live.py.
"""
import asyncio

import httpx

from backend.processing.data_merger import has_value, merge_sources
from backend.services import ERROR, NOT_FOUND, OK, UNAVAILABLE, build_clients
from backend.services.crossref import CrossrefClient, strip_jats
from backend.services.openalex import reconstruct_abstract
from backend.services.semantic_scholar import SemanticScholarClient

from .conftest import CROSSREF_OK, OPENALEX_OK, S2_OK, json_response, make_transport

DOI = "10.1234/test.paper"


def run(coro):
    return asyncio.run(coro)


async def fetch_all(transport):
    clients = build_clients(transport)
    try:
        results = {}
        for name, c in clients.items():
            results[name] = await c.safe_fetch(DOI)
        return results
    finally:
        for c in clients.values():
            await c.close()


def test_crossref_parsing():
    t = make_transport([("api.crossref.org", json_response(CROSSREF_OK))])
    r = run(fetch_all(t))["crossref"]
    assert r.status == OK
    assert r.data["title"] == "A Test Paper"
    assert r.data["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert r.data["author_affiliations"] == {"Ada Lovelace": ["Analytical College"]}
    assert r.data["publication_date"] == "2020-05-17"
    assert r.data["journal"] == "Journal of Tests"
    assert r.data["abstract"] == "An abstract ."
    assert r.data["citation_count"] == 0


def test_rate_limit_is_retried_honouring_retry_after():
    def flaky(request, n):
        if n == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=CROSSREF_OK)

    t = make_transport([("api.crossref.org", flaky)])

    async def go():
        c = CrossrefClient(t)
        try:
            return await c.safe_fetch(DOI)
        finally:
            await c.close()

    r = run(go())
    assert r.status == OK
    assert t.calls["api.crossref.org"] == 2


def test_persistent_server_error_becomes_error_result():
    t = make_transport([("semanticscholar", lambda req, n: httpx.Response(503))])

    async def go():
        c = SemanticScholarClient(t)
        c.min_interval = 0
        try:
            return await c.safe_fetch(DOI)
        finally:
            await c.close()

    r = run(go())
    assert r.status == ERROR
    assert "HTTP 503" in r.message
    assert t.calls["semanticscholar"] == 6  # 1 attempt + 5 retries (no API key: retries longer)


def test_timeout_becomes_error_result():
    def boom(request, n):
        raise httpx.ReadTimeout("slow", request=request)

    t = make_transport([("api.crossref.org", boom)])

    async def go():
        c = CrossrefClient(t)
        try:
            return await c.safe_fetch(DOI)
        finally:
            await c.close()

    r = run(go())
    assert r.status == ERROR and "timed out" in r.message


def test_404_is_not_found_and_not_retried():
    t = make_transport([("api.crossref.org", lambda req, n: httpx.Response(404))])

    async def go():
        c = CrossrefClient(t)
        try:
            return await c.safe_fetch(DOI)
        finally:
            await c.close()

    assert run(go()).status == NOT_FOUND
    assert t.calls["api.crossref.org"] == 1


def test_google_scholar_is_reported_unavailable_not_faked():
    t = make_transport([])
    r = run(fetch_all(t))["google_scholar"]
    assert r.status == UNAVAILABLE
    assert r.data == {}
    assert "Google Scholar" in r.message


def test_merge_partial_success_keeps_sources_separate():
    """Crossref + OpenAlex succeed, Semantic Scholar fails -> partial record, nothing discarded."""
    t = make_transport([
        ("api.crossref.org", json_response(CROSSREF_OK)),
        ("api.openalex.org", json_response(OPENALEX_OK)),
        ("semanticscholar", lambda req, n: httpx.Response(500)),
    ])
    results = run(fetch_all(t))
    rec = merge_sources(DOI, results)

    assert rec["status"] == "partial"
    assert rec["errors"][0]["source"] == "semantic_scholar"
    assert rec["core"]["title"] == {"value": "A Test Paper", "source": "crossref",
                                    "retrieved_at": results["crossref"].retrieved_at}

    cites = {v["source"]: v for v in rec["metrics"]["citation_count"]["values"]}
    # Real zero from Crossref is AVAILABLE with value 0 — not "missing".
    assert cites["crossref"]["available"] is True and cites["crossref"]["value"] == 0
    # OpenAlex value preserved separately, not combined with Crossref.
    assert cites["openalex"]["value"] == 7
    # Failed source is unavailable with an explanation, not 0.
    assert cites["semantic_scholar"]["available"] is False
    assert cites["semantic_scholar"]["value"] is None
    assert "request failed" in cites["semantic_scholar"]["reason"]
    assert rec["metrics"]["citation_count"]["preferred"] is None  # per-source metrics are never collapsed

    # Every retrieved value carries its source + timestamp; every missing one a reason.
    for m in rec["metrics"].values():
        for v in m["values"]:
            assert v["source"]
            assert v["retrieved_at"] if v["available"] else v["reason"]


def test_merge_complete_when_all_ok():
    t = make_transport([
        ("api.crossref.org", json_response(CROSSREF_OK)),
        ("api.openalex.org", json_response(OPENALEX_OK)),
        ("semanticscholar", json_response(S2_OK)),
    ])
    rec = merge_sources(DOI, run(fetch_all(t)))
    assert rec["status"] == "complete"
    infl = rec["metrics"]["influential_citation_count"]["values"][0]
    assert infl["available"] and infl["value"] == 0       # zero, not unavailable


def test_merge_not_found_everywhere():
    rec = merge_sources(DOI, run(fetch_all(make_transport([]))))
    assert rec["status"] == "not_found"
    assert rec["core"]["title"]["value"] is None
    assert all(not m["available"] for m in rec["metrics"].values())


def test_author_and_journal_level_metrics_never_invented():
    from backend.services.journal_rankings import lookup_impact_factor, lookup_quartile

    t = make_transport([("api.crossref.org", json_response(CROSSREF_OK))])
    results = run(fetch_all(t))
    results["sjr"] = lookup_quartile(["1234-5678"])          # no file configured
    results["jcr_file"] = lookup_impact_factor(["1234-5678"])
    rec = merge_sources(DOI, results)
    h = rec["metrics"]["author_h_index"]
    q = rec["metrics"]["quartile"]
    jif = rec["metrics"]["impact_factor"]
    assert h["level"] == "author" and not h["available"] and "author-level" in h["unavailable_reason"]
    assert q["level"] == "journal" and not q["available"] and "SJR_CSV_PATH" in q["unavailable_reason"]
    assert jif["level"] == "journal" and not jif["available"] and "licensed" in jif["unavailable_reason"]


def test_crossref_conference_fields():
    from .conftest import CROSSREF_CONFERENCE
    from backend.services.crossref import parse_crossref

    d = parse_crossref(CROSSREF_CONFERENCE["message"])
    assert d["publication_type"] == "Conference"
    assert d["journal"] is None
    assert d["conference_name"] == "TC '19: The 25th Test Conference"
    assert d["conference_acronym"] == "TC '19"
    assert d["conference_dates"] == "2019-08-04 to 2019-08-08"
    assert d["conference_location"] == "Chennai, India"
    assert d["proceedings_title"] == "Proceedings of the 25th Test Conference & Workshop"  # HTML entity decoded
    assert d["proceedings_publisher"] == "ACM"
    assert d["proceedings_isbn_issn"] == "ISBN 9781450362016"
    assert d["proceedings_volume_pages"] == "pp. 10-19"
    assert d["publication_url"] == "https://dl.acm.org/doi/10.1234/conf.paper"


def test_crossref_journal_fields():
    from backend.services.crossref import parse_crossref

    d = parse_crossref(CROSSREF_OK["message"])
    assert d["publication_type"] == "Journal"
    assert d["issn_eissn"] == "1234-5678 (ISSN)"
    assert d["page_range_or_article_number"] == "100-110"
    assert d["conference_name"] is None
    assert parse_crossref(dict(CROSSREF_OK["message"], subtitle=["A Subtitle"]))["title"] == "A Test Paper: A Subtitle"
    m = dict(CROSSREF_OK["message"], page=None, **{"article-number": "e123"})
    assert parse_crossref(m)["page_range_or_article_number"] == "Article e123"


def test_journal_ranking_files(tmp_path, monkeypatch):
    from backend.config import settings
    from backend.services import journal_rankings as jr

    sjr = tmp_path / "sjr.csv"
    sjr.write_text('Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile\n'
                   '1;123;"Journal of Tests";journal;"12345678, 87654321";1,2;Q2\n', encoding="utf-8")
    jif = tmp_path / "jif.csv"
    jif.write_text("issn,impact_factor,year\n1234-5678,0,2024\n", encoding="utf-8")
    object.__setattr__(settings, "sjr_csv_path", sjr)
    object.__setattr__(settings, "impact_factor_csv_path", jif)
    try:
        q = jr.lookup_quartile(["8765-4321"])
        assert q.status == OK and q.data["quartile"] == "Q2"
        f = jr.lookup_impact_factor(["1234-5678"])
        assert f.status == OK and f.data["impact_factor"] == 0.0     # a real 0, not unavailable
        assert jr.lookup_quartile(["0000-0000"]).status == NOT_FOUND
    finally:
        object.__setattr__(settings, "sjr_csv_path", None)
        object.__setattr__(settings, "impact_factor_csv_path", None)


def test_indexing_unavailable_without_keys():
    r = run(fetch_all(make_transport([])))
    assert r["scopus"].status == UNAVAILABLE and "SCOPUS_API_KEY" in r["scopus"].message
    assert r["web_of_science"].status == UNAVAILABLE and "WOS_API_KEY" in r["web_of_science"].message


def test_scopus_with_key(monkeypatch):
    from backend.config import settings
    from backend.services.indexing import ScopusClient

    object.__setattr__(settings, "scopus_api_key", "test-key")
    seen = {}

    def handler(request, n):
        seen["key"] = request.headers.get("X-ELS-APIKey")
        return httpx.Response(200, json={"search-results": {"opensearch:totalResults": "1",
                                                            "entry": [{"eid": "2-s2.0-1"}]}})

    async def go():
        c = ScopusClient(make_transport([("api.elsevier.com", handler)]))
        try:
            return await c.safe_fetch(DOI)
        finally:
            await c.close()

    try:
        r = run(go())
    finally:
        object.__setattr__(settings, "scopus_api_key", "")
    assert r.status == OK and r.data["indexed_scopus"] is True and seen["key"] == "test-key"


def test_has_value_distinguishes_zero_from_missing():
    assert has_value(0) and has_value(0.0) and has_value(False)
    assert not has_value(None) and not has_value("") and not has_value([]) and not has_value({})


def test_helpers():
    assert strip_jats("<jats:title>Abstract</jats:title><jats:p>Hello <b>x</b></jats:p>") == "Hello x"
    assert reconstruct_abstract({"world": [1], "Hello": [0]}) == "Hello world"
    assert reconstruct_abstract(None) is None
