"""Live tests against the real academic APIs (needs internet).

Run:   .venv/bin/pytest -m live
Skip:  .venv/bin/pytest -m "not live"

Assertions only check facts that are stable over time (titles, years,
journals) and lower bounds for citation counts, which keep growing.
Semantic Scholar's anonymous pool is rate limited; if it answers 429 the test
accepts the honest "error" result rather than failing.
"""
import asyncio

import pytest

from backend.processing.data_merger import flatten
from backend.processing.retrieval import retrieve
from backend.services import build_clients

pytestmark = pytest.mark.live


def extract(doi):
    async def go():
        clients = build_clients()
        try:
            return await retrieve(doi, clients)
        finally:
            for c in clients.values():
                await c.close()

    return asyncio.run(go())


def test_live_deep_learning_nature():
    rec = extract("10.1038/nature14539")
    assert rec["sources"]["crossref"]["status"] == "ok"
    assert rec["sources"]["openalex"]["status"] == "ok"
    assert rec["core"]["title"]["value"] == "Deep learning"
    assert rec["core"]["journal"]["value"] == "Nature"
    assert rec["core"]["publication_year"]["value"] == 2015
    assert rec["core"]["authors"]["value"][:3] == ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"]
    cites = {v["source"]: v for v in rec["metrics"]["citation_count"]["values"]}
    assert cites["crossref"]["value"] > 50_000
    assert cites["openalex"]["value"] > 50_000
    assert rec["metrics"]["openalex_id"]["values"][0]["value"].startswith("W")
    f = flatten(rec)["fields"]
    assert f["publication_type"] == "Journal"
    assert (f["volume"], f["issue"], f["page_range"]) == ("521", "7553", "436-444")
    assert "0028-0836" in f["issn"]


def test_live_science_paper_all_three_sources():
    rec = extract("10.1126/science.1157784")
    assert rec["core"]["title"]["value"].startswith("Strategic Reading")
    assert rec["core"]["publication_year"]["value"] == 2009
    s2 = rec["sources"]["semantic_scholar"]["status"]
    assert s2 in ("ok", "error")
    if s2 == "ok":
        infl = rec["metrics"]["influential_citation_count"]["values"][0]
        assert infl["available"] and infl["value"] >= 0
        assert rec["status"] == "complete"
    else:
        assert rec["status"] == "partial"


def test_live_conference_paper():
    rec = extract("10.1109/CVPR.2016.90")
    f = flatten(rec)["fields"]
    assert f["publication_type"] == "Conference"
    assert f["title"] == "Deep Residual Learning for Image Recognition"
    assert f["authors"][0] == "Kaiming He"
    assert f["conference_location"] == "Las Vegas, NV, USA"
    assert f["conference_dates"] == "2016-06-27 to 2016-06-30"
    assert f["proceedings_volume_pages"] == "pp. 770-778"
    assert f["publication_url"].startswith("http")


def test_live_arxiv_attention():
    """arXiv DOI whose NeurIPS version has no DOI. Needs Semantic Scholar (may be rate limited)."""
    f = flatten(extract("10.48550/arXiv.1706.03762"))["fields"]
    assert f["title"] == "Attention Is All You Need"
    assert f["authors"][0] == "Ashish Vaswani" and len(f["authors"]) == 8
    assert f["journal"] is None                              # arXiv is not reported as a journal
    if f["publication_type"] == "Conference":
        assert f["conference_acronym"] == "NeurIPS"
        assert f["proceedings_volume_pages"] == "Vol. 30, pp. 5998-6008"
    else:
        assert "could not be reached" in f["version_note"]


def test_live_arxiv_resnet_resolves_to_cvpr():
    f = flatten(extract("10.48550/arXiv.1512.03385"))["fields"]
    assert f["published_doi"] == "10.1109/cvpr.2016.90"
    assert f["conference_location"] == "Las Vegas, NV, USA"
    assert f["publisher"] == "IEEE"


def test_live_nonexistent_doi():
    rec = extract("10.9999/this.doi.does.not.exist.12345")
    assert rec["sources"]["crossref"]["status"] == "not_found"
    assert rec["sources"]["openalex"]["status"] == "not_found"
    assert rec["status"] in ("not_found", "failed")
    assert rec["core"]["title"]["value"] is None
