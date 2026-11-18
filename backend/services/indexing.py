"""Indexing-database checks: Scopus and Web of Science (official APIs, optional).

Field 8 of the department's list, "Indexing Database (Scopus / Web of Science
/ Other)", cannot be answered from free sources. Both services need an
institutional API key:

* Scopus Search API (Elsevier): https://dev.elsevier.com/  -> SCOPUS_API_KEY
  GET https://api.elsevier.com/content/search/scopus?query=DOI(<doi>)
* Web of Science Starter API (Clarivate): https://developer.clarivate.com/apis/wos-starter
  -> WOS_API_KEY
  GET https://api.clarivate.com/apis/wos-starter/v1/documents?db=WOS&q=DO=(<doi>)

Without a key the source reports "unavailable" with the reason, and the
indexing field says "not verified". It never guesses. These are official
APIs; nothing is scraped.
"""
from __future__ import annotations

from ..config import settings
from .base import NOT_FOUND, OK, UNAVAILABLE, SourceClient, SourceResult


class ScopusClient(SourceClient):
    name = "scopus"
    label = "Scopus"
    base_url = "https://api.elsevier.com"
    min_interval = 0.2

    def headers(self) -> dict[str, str]:
        h = super().headers()
        if settings.scopus_api_key:
            h["X-ELS-APIKey"] = settings.scopus_api_key
        return h

    async def fetch(self, doi: str) -> SourceResult:
        if not settings.scopus_api_key:
            return SourceResult(self.name, UNAVAILABLE,
                                message="Not checked: SCOPUS_API_KEY is not configured (Elsevier institutional key required)")
        payload = await self.get_json(
            "/content/search/scopus",
            {"query": f'DOI("{doi}")', "field": "eid,dc:identifier,prism:publicationName"},
        )
        results = payload.get("search-results") or {}
        total = int(results.get("opensearch:totalResults") or 0)
        if total == 0:
            # Scopus answered: the DOI is not in Scopus. That is a real "No", not missing data.
            return SourceResult(self.name, OK, data={"indexed_scopus": False}, http_status=200,
                                message="Not found in Scopus")
        entry = (results.get("entry") or [{}])[0]
        return SourceResult(self.name, OK, http_status=200,
                            data={"indexed_scopus": True, "scopus_eid": entry.get("eid")})


class WebOfScienceClient(SourceClient):
    name = "web_of_science"
    label = "Web of Science"
    base_url = "https://api.clarivate.com/apis/wos-starter/v1"
    min_interval = 0.5

    def headers(self) -> dict[str, str]:
        h = super().headers()
        if settings.wos_api_key:
            h["X-ApiKey"] = settings.wos_api_key
        return h

    async def fetch(self, doi: str) -> SourceResult:
        if not settings.wos_api_key:
            return SourceResult(self.name, UNAVAILABLE,
                                message="Not checked: WOS_API_KEY is not configured (Clarivate key required)")
        payload = await self.get_json("/documents", {"db": "WOS", "q": f'DO=("{doi}")', "limit": 1})
        total = int((payload.get("metadata") or {}).get("total") or 0)
        if total == 0:
            return SourceResult(self.name, OK, data={"indexed_wos": False}, http_status=200,
                                message="Not found in Web of Science")
        hit = (payload.get("hits") or [{}])[0]
        return SourceResult(self.name, OK, http_status=200,
                            data={"indexed_wos": True, "wos_uid": hit.get("uid")})


__all__ = ["ScopusClient", "WebOfScienceClient", "NOT_FOUND"]
