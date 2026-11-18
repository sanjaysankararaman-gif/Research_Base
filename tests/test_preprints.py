"""Preprint DOIs (arXiv etc.): finding and using the published version. Simulated sources."""
import asyncio

import httpx

from backend.processing.data_merger import flatten
from backend.processing.retrieval import retrieve
from backend.services import build_clients
from backend.services.datacite import parse_datacite

from .conftest import CROSSREF_CONFERENCE, json_response, make_transport

ARXIV = "10.48550/arxiv.1706.03762"

DATACITE_ARXIV = {"data": {"attributes": {
    "titles": [{"title": "Attention Is All You Need"}],
    "creators": [{"name": "Vaswani, Ashish", "givenName": "Ashish", "familyName": "Vaswani"},
                 {"name": "Shazeer, Noam"}],
    "publisher": "arXiv",
    "publicationYear": 2017,
    "types": {"resourceTypeGeneral": "Preprint"},
    "url": "https://arxiv.org/abs/1706.03762",
    "relatedIdentifiers": [],
}}}

OPENALEX_ARXIV = {
    "id": "https://openalex.org/W2626778328", "display_name": "Attention Is All You Need", "type": "preprint",
    "publication_year": 2017,
    "authorships": [{"author": {"display_name": "Ashish Vaswani"}, "institutions": []}],
    "primary_location": {"landing_page_url": "http://arxiv.org/abs/1706.03762",
                         "source": {"display_name": "arXiv (Cornell University)", "type": "repository",
                                    "issn": ["2331-8422"]}},
    "biblio": {"volume": "30", "issue": None, "first_page": "5998", "last_page": "6008"},
    "open_access": {"is_oa": True}, "indexed_in": ["arxiv", "datacite"],
}

S2_NEURIPS = {
    "paperId": "204e", "title": "Attention is All you Need", "year": 2017,
    "externalIds": {"ArXiv": "1706.03762", "DOI": "10.48550/arXiv.1706.03762"},
    "publicationVenue": {"name": "Neural Information Processing Systems", "type": "conference",
                         "alternate_names": ["Neural Inf Process Syst", "NeurIPS", "NIPS"]},
    "venue": "Neural Information Processing Systems",
    "publicationTypes": ["JournalArticle", "Conference"],
    "journal": {"pages": "5998-6008"},
    "authors": [{"name": "Ashish Vaswani"}],
}


class MemoryCache:
    def __init__(self):
        self.data = {}

    def get(self, doi, source):
        return self.data.get((doi, source))

    def put(self, doi, source, data, retrieved_at):
        self.data[(doi, source)] = (data, retrieved_at)


def run(doi, routes, cache=None):
    async def go():
        clients = build_clients(make_transport(routes))
        for c in clients.values():
            c.min_interval = 0
        try:
            return await retrieve(doi, clients, cache=cache)
        finally:
            for c in clients.values():
                await c.close()
    return asyncio.run(go())


def test_parse_datacite():
    d = parse_datacite(DATACITE_ARXIV["data"]["attributes"])
    assert d["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert d["publication_type"] == "Preprint" and d["is_preprint"] and d["publisher"] == "arXiv"


def test_preprint_published_without_doi():
    """Attention: arXiv DOI, published at NeurIPS without a DOI of its own."""
    rec = run(ARXIV, [
        ("api.datacite.org", json_response(DATACITE_ARXIV)),
        ("api.openalex.org", json_response(OPENALEX_ARXIV)),
        ("semanticscholar.org/graph/v1/paper/ARXIV:1706.03762", json_response(S2_NEURIPS)),
    ])
    f = flatten(rec)["fields"]
    assert f["publication_type"] == "Conference"
    assert f["conference_name"] == "Neural Information Processing Systems"
    assert f["conference_acronym"] == "NeurIPS"
    assert f["proceedings_volume_pages"] == "Vol. 30, pp. 5998-6008"
    assert f["journal"] is None and f["issn"] is None          # arXiv is not a journal
    assert f["publisher"] is None                              # arXiv is not the NeurIPS publisher
    assert "no DOI of its own" in f["version_note"]
    assert rec["status"] in ("complete", "partial")


def test_preprint_linked_to_published_doi_by_semantic_scholar():
    s2 = dict(S2_NEURIPS, externalIds={"ArXiv": "1706.03762", "DOI": "10.1234/conf.paper"})
    rec = run(ARXIV, [
        ("api.datacite.org", json_response(DATACITE_ARXIV)),
        ("api.openalex.org/works/doi:10.48550", json_response(OPENALEX_ARXIV)),
        ("semanticscholar.org/graph/v1/paper/ARXIV", json_response(s2)),
        ("api.crossref.org/works/10.1234/conf.paper", json_response(CROSSREF_CONFERENCE)),
    ])
    f = flatten(rec)["fields"]
    assert rec["doi"] == ARXIV                       # the entered DOI stays the key
    assert f["published_doi"] == "10.1234/conf.paper"
    assert f["title"] == "A Conference Paper" and f["conference_location"] == "Chennai, India"
    assert "published version" in f["version_note"]
    assert rec["version"]["published_doi"] == "10.1234/conf.paper"


def test_preprint_matched_by_title_when_semantic_scholar_is_down():
    datacite = {"data": {"attributes": dict(DATACITE_ARXIV["data"]["attributes"],
                                            titles=[{"title": "A Conference Paper"}],
                                            creators=[{"givenName": "Grace", "familyName": "Hopper"}],
                                            publicationYear=2019)}}
    search = {"message": {"items": [
        {"DOI": "10.9999/other", "title": ["A Conference Paper on Something Else"],
         "author": [{"family": "Hopper"}], "issued": {"date-parts": [[2019]]}},
        {"DOI": "10.1234/conf.paper", "title": ["A Conference Paper"],
         "author": [{"family": "Hopper"}], "issued": {"date-parts": [[2019]]}},
    ]}}
    rec = run(ARXIV, [
        ("api.datacite.org", json_response(datacite)),
        ("api.crossref.org/works?", json_response(search)),
        ("api.crossref.org/works/10.1234/conf.paper", json_response(CROSSREF_CONFERENCE)),
        ("semanticscholar", lambda req, n: httpx.Response(429, headers={"Retry-After": "0"})),
    ])
    assert flatten(rec)["fields"]["published_doi"] == "10.1234/conf.paper"


def test_title_match_rejects_different_first_author():
    search = {"message": {"items": [{"DOI": "10.1234/conf.paper", "title": ["Attention Is All You Need"],
                                     "author": [{"family": "Someone"}], "issued": {"date-parts": [[2017]]}}]}}
    rec = run(ARXIV, [
        ("api.datacite.org", json_response(DATACITE_ARXIV)),
        ("api.crossref.org/works?", json_response(search)),
        ("semanticscholar", lambda req, n: httpx.Response(429, headers={"Retry-After": "0"})),
    ])
    f = flatten(rec)["fields"]
    assert f["published_doi"] is None
    assert f["publication_type"] == "Preprint"
    assert "could not be reached" in f["version_note"]       # honest about why


def test_temporary_failure_reuses_last_good_answer():
    good = [
        ("api.datacite.org", json_response(DATACITE_ARXIV)),
        ("api.openalex.org", json_response(OPENALEX_ARXIV)),
        ("semanticscholar.org/graph/v1/paper/ARXIV:1706.03762", json_response(S2_NEURIPS)),
    ]
    cache = MemoryCache()
    assert flatten(run(ARXIV, good, cache))["fields"]["publication_type"] == "Conference"
    # Semantic Scholar is now rate limited: the record must not get worse.
    rate_limited = good[:2] + [("semanticscholar", lambda req, n: httpx.Response(429, headers={"Retry-After": "0"}))]
    rec = run(ARXIV, rate_limited, cache)
    f = flatten(rec)["fields"]
    assert f["publication_type"] == "Conference" and f["conference_acronym"] == "NeurIPS"
    assert "using data retrieved" in rec["sources"]["semantic_scholar"]["message"]
