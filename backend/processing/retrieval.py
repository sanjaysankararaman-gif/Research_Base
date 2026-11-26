"""DOI -> standardized record. The data-retrieval ("scraping") core.

`retrieve(doi, clients)` queries every source, resolves preprints to their
published version, and merges everything into one record with the 23
fields of "Essential Fields for Research Publication Database". It does not
touch any database, so it can be used on its own (see fetch_doi.py).

An optional `cache` (any object with get/put, see SourceCache) lets a caller
remember each source's last good answer and reuse it when a source is
temporarily down.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from ..services import ERROR, OK, SourceClient, SourceResult
from ..services.crossref import find_published_by_title
from ..services.journal_rankings import lookup_impact_factor, lookup_quartile
from .data_merger import merge_sources
from .doi_validator import InvalidDOI, normalize_doi

log = logging.getLogger("extractor.retrieval")


class SourceCache(Protocol):
    def get(self, doi: str, source: str) -> tuple[dict[str, Any], str] | None: ...
    def put(self, doi: str, source: str, data: dict[str, Any], retrieved_at: str) -> None: ...


async def _query_all(doi: str, clients: dict[str, SourceClient], on_source_done=None,
                     cache: "SourceCache | None" = None) -> dict[str, SourceResult]:
    """Query every network source for one DOI, then the journal-ranking files."""

    async def run(name: str, client: SourceClient) -> tuple[str, SourceResult]:
        result = _with_cache(doi, name, await client.safe_fetch(doi), cache)
        if on_source_done:
            on_source_done(name, result)
        return name, result

    # DataCite is only needed for DOIs Crossref does not have (arXiv, Zenodo, repositories),
    # so it is asked only after Crossref answers "not found" (or fails).
    first = {n: c for n, c in clients.items() if n != "datacite"}
    results = dict(await asyncio.gather(*(run(n, c) for n, c in first.items())))
    if "datacite" in clients:
        crossref = results.get("crossref")
        if crossref is None or crossref.status != OK:
            name, result = await run("datacite", clients["datacite"])
            results[name] = result
        else:
            results["datacite"] = SourceResult("datacite", "unavailable",
                                               message="Not needed: the DOI is registered with Crossref")
            if on_source_done:
                on_source_done("datacite", results["datacite"])

    # Journal-level lookups by ISSN (local files, no network).
    issns: list[str] = []
    journals: list[str] = []
    for name in ("crossref", "openalex"):
        r = results.get(name)
        if r and r.status == OK:
            issns += [i for i in (r.data.get("issn") or []) if i not in issns]
            if r.data.get("journal"):
                journals.append(r.data["journal"])
    lookups = (("sjr", lambda: lookup_quartile(issns, journals)), ("jcr_file", lambda: lookup_impact_factor(issns)))
    for name, lookup in lookups:
        try:
            results[name] = lookup()
        except Exception:
            log.exception("Journal lookup %s failed for %s", name, doi)
            results[name] = SourceResult(name, "error", message="Could not read the journal ranking file")
        if on_source_done:
            on_source_done(name, results[name])
    return results


def _with_cache(doi: str, name: str, result: SourceResult, cache: "SourceCache | None") -> SourceResult:
    """Remember good answers; on a temporary failure, fall back to the last good answer."""
    if cache is None:
        return result
    if result.status == OK:
        cache.put(doi, name, result.data, result.retrieved_at)
        return result
    if result.status == ERROR:
        cached = cache.get(doi, name)
        if cached:
            data, retrieved_at = cached
            log.info("%s failed for %s; using its answer from %s", name, doi, retrieved_at)
            return SourceResult(name, OK, retrieved_at=retrieved_at, data=data,
                                message=f"Live request failed ({result.message}); using data retrieved {retrieved_at}")
    return result


def find_published_doi(doi: str, results: dict[str, SourceResult]) -> str | None:
    """If the DOI is a preprint, the DOI of its formally published version (or None).

    Crossref ("is-preprint-of"), DataCite ("IsPreprintOf"/"IsVersionOf") and
    Semantic Scholar (merged preprint + published record) can each supply it.
    """
    for name in ("crossref", "datacite", "semantic_scholar"):
        r = results.get(name)
        candidate = r.data.get("published_doi") if (r and r.status == OK) else None
        if candidate and candidate.lower() != doi.lower():
            try:
                return normalize_doi(candidate)
            except InvalidDOI:
                continue
    return None


def _looks_like_preprint(results: dict[str, SourceResult]) -> bool:
    return any(r.status == OK and r.data.get("is_preprint")
               for n, r in results.items() if n in ("crossref", "datacite", "openalex"))


async def _match_published_by_title(doi: str, results: dict[str, SourceResult], crossref) -> str | None:
    title = authors = year = None
    for name in ("datacite", "openalex", "crossref"):
        r = results.get(name)
        if r and r.status == OK:
            title = title or r.data.get("title")
            authors = authors or r.data.get("authors")
            year = year or r.data.get("publication_year")
    try:
        found = await find_published_by_title(crossref, title, (authors or [None])[0], year)
    except Exception:
        log.warning("Title match for the published version of %s failed", doi, exc_info=True)
        return None
    if found and found != doi.lower():
        log.info("Matched preprint %s to published %s by title and first author", doi, found)
        return found
    return None


async def retrieve(
    doi: str,
    clients: dict[str, SourceClient],
    on_source_done=None,
    cache: SourceCache | None = None,
) -> dict:
    """Query every source for one DOI and return the merged, standardized record.

    Preprints (e.g. arXiv DOIs): if a published version with its own DOI is
    known, its details are fetched and used, because that is the version the
    department records. The entered DOI stays the record's key, and the
    record notes both DOIs.
    """
    results = await _query_all(doi, clients, on_source_done, cache)
    published = find_published_doi(doi, results)
    if not published and _looks_like_preprint(results) and "crossref" in clients:
        published = await _match_published_by_title(doi, results, clients["crossref"])
    if published:
        log.info("%s is a preprint of %s; fetching the published version", doi, published)
        pub_results = await _query_all(published, clients, cache=cache)
        if merge_sources(published, pub_results)["status"] in ("complete", "partial"):
            return merge_sources(doi, pub_results, preprint_results=results, published_doi=published)
        log.warning("Published version %s of %s could not be retrieved", published, doi)
    return merge_sources(doi, results)
