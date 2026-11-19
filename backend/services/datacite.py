"""DataCite REST API client: DOIs that are not registered with Crossref.

Docs: https://support.datacite.org/docs/api
Endpoint used: GET https://api.datacite.org/dois/{doi}   (no key)

arXiv (10.48550/arXiv.*), Zenodo, figshare and many university repositories
register their DOIs with DataCite, so Crossref answers 404 for them. DataCite
also sometimes records the DOI of the published version of a preprint
("IsPreprintOf" / "IsVersionOf"); see `published_doi`.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .base import OK, SourceClient, SourceResult

TYPE_MAP = {
    "Preprint": "Preprint",
    "JournalArticle": "Journal",
    "ConferencePaper": "Conference",
    "ConferenceProceeding": "Conference",
    "BookChapter": "Book chapter",
    "Book": "Book",
}

# Relations that point from this DOI to the formally published version.
PUBLISHED_RELATIONS = {"IsPreprintOf", "IsVersionOf", "IsPreviousVersionOf"}


def _name(creator: dict[str, Any]) -> str | None:
    given, family = creator.get("givenName"), creator.get("familyName")
    if given and family:
        return f"{given} {family}"
    name = creator.get("name") or ""
    if ", " in name and creator.get("nameType") != "Organizational":
        family, given = name.split(", ", 1)
        return f"{given} {family}"
    return name or None


def parse_datacite(attrs: dict[str, Any]) -> dict[str, Any]:
    titles = [t.get("title") for t in attrs.get("titles", []) or [] if t.get("title") and not t.get("titleType")]
    publisher = attrs.get("publisher")
    if isinstance(publisher, dict):
        publisher = publisher.get("name")
    general = (attrs.get("types") or {}).get("resourceTypeGeneral")
    container = attrs.get("container") or {}
    published = [
        r.get("relatedIdentifier", "").lower()
        for r in attrs.get("relatedIdentifiers", []) or []
        if r.get("relationType") in PUBLISHED_RELATIONS and r.get("relatedIdentifierType") == "DOI"
    ]
    pages = None
    if container.get("firstPage"):
        pages = container["firstPage"] + (f"-{container['lastPage']}" if container.get("lastPage") else "")
    return {
        "title": titles[0] if titles else None,
        "authors": [n for n in (_name(c) for c in attrs.get("creators", []) or []) if n] or None,
        "publication_year": attrs.get("publicationYear"),
        "publisher": publisher,
        "publication_type": TYPE_MAP.get(general, "Other" if general else None),
        "resource_type": general,
        "is_preprint": general == "Preprint",
        "publication_url": attrs.get("url"),
        "journal": container.get("title") if general == "JournalArticle" else None,
        "volume": container.get("volume"),
        "issue": container.get("issue"),
        "pages": pages,
        "published_doi": published[0] if published else None,
    }


class DataCiteClient(SourceClient):
    name = "datacite"
    label = "DataCite"
    base_url = "https://api.datacite.org"
    min_interval = 0.1
    timeout = 12.0
    max_retries = 1

    async def fetch(self, doi: str) -> SourceResult:
        payload = await self.get_json(f"/dois/{quote(doi, safe='/')}")
        attrs = (payload.get("data") or {}).get("attributes") or {}
        return SourceResult(self.name, OK, data=parse_datacite(attrs), http_status=200)
