"""OpenAlex API client — bibliometrics, affiliations, topics, open access.

Docs: https://docs.openalex.org/
Endpoint used: GET https://api.openalex.org/works/doi:{doi}
No key required. CONTACT_EMAIL is sent as `mailto` (polite pool); an optional
OPENALEX_API_KEY is sent as `api_key` if configured.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..config import settings
from .base import OK, SourceClient, SourceResult


def _short_id(url: str | None) -> str | None:
    return url.rsplit("/", 1)[-1] if url else None


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inverted:
        return None
    positions: dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions)) or None


def parse_openalex(work: dict[str, Any]) -> dict[str, Any]:
    authors, authorships = [], []
    for a in work.get("authorships", []) or []:
        name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name")
        if not name:
            continue
        authors.append(name)
        insts = a.get("institutions", []) or []
        authorships.append({
            "name": name,
            "raw_name": a.get("raw_author_name"),
            "institutions": [i["display_name"] for i in insts if i.get("display_name")],
            "ror": [i["ror"] for i in insts if i.get("ror")],
            "raw_affiliations": [r for r in a.get("raw_affiliation_strings") or [] if r],
        })

    oa = work.get("open_access") or {}
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    best_oa = work.get("best_oa_location") or {}
    source_type = source.get("type")
    work_type = work.get("type")
    if source_type == "conference" or work_type == "conference-paper":
        publication_type = "Conference"
    elif source_type == "journal":
        publication_type = "Journal"
    elif work_type == "preprint" or source_type == "repository":
        publication_type = "Preprint"
    else:
        publication_type = None   # unknown: let other sources decide
    is_conference = publication_type == "Conference"
    is_repository = source_type == "repository"
    biblio = work.get("biblio") or {}
    pages = None
    if biblio.get("first_page"):
        pages = biblio["first_page"] + (f"-{biblio['last_page']}" if biblio.get("last_page") else "")
    primary_topic = work.get("primary_topic") or {}
    percentile = work.get("citation_normalized_percentile") or {}

    return {
        "openalex_id": _short_id(work.get("id")),
        "title": work.get("display_name") or work.get("title"),
        "authors": authors or None,
        "authorships": authorships if any(a["institutions"] or a["raw_affiliations"] for a in authorships) else None,
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "publication_type": publication_type,
        "journal": None if (is_conference or is_repository) else source.get("display_name"),
        "repository": source.get("display_name") if is_repository else None,
        "is_preprint": publication_type == "Preprint",
        "conference_name": source.get("display_name") if is_conference else None,
        "publication_url": primary.get("landing_page_url"),
        "pdf_url": best_oa.get("pdf_url") or primary.get("pdf_url"),
        "indexed_in": work.get("indexed_in") or None,
        "issn": None if is_repository else (source.get("issn") or None),
        "issn_eissn": None if is_repository else ("; ".join(source.get("issn") or []) or None),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "pages": pages,
        "volume_pages": ", ".join(x for x in (f"Vol. {biblio['volume']}" if biblio.get("volume") else None,
                                              f"pp. {pages}" if pages else None) if x) or None,
        "article_type": work.get("type"),
        "language": work.get("language"),
        "citation_count": work.get("cited_by_count"),
        "reference_count": work.get("referenced_works_count"),
        "referenced_works": [_short_id(w) for w in work.get("referenced_works", []) or []] or None,
        "counts_by_year": work.get("counts_by_year") or None,
        "fwci": work.get("fwci"),
        "citation_percentile": percentile.get("value"),
        "top_1_percent": percentile.get("is_in_top_1_percent"),
        "top_10_percent": percentile.get("is_in_top_10_percent"),
        "is_open_access": oa.get("is_oa"),
        "oa_status": oa.get("oa_status"),
        "oa_url": oa.get("oa_url"),
        "primary_topic": primary_topic.get("display_name"),
        "topics": [t.get("display_name") for t in work.get("topics", []) or [] if t.get("display_name")] or None,
        "concepts": [
            c.get("display_name")
            for c in work.get("concepts", []) or []
            if c.get("display_name") and (c.get("score") or 0) >= 0.3
        ]
        or None,
        "keywords": [k.get("display_name") for k in work.get("keywords", []) or [] if k.get("display_name")] or None,
        "field": (primary_topic.get("field") or {}).get("display_name"),
        "is_retracted": work.get("is_retracted"),
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "funders": [f.get("display_name") for f in work.get("funders", []) or [] if f.get("display_name")] or None,
        "sdgs": [s.get("display_name") for s in work.get("sustainable_development_goals", []) or [] if s.get("display_name")] or None,
    }


class OpenAlexClient(SourceClient):
    name = "openalex"
    label = "OpenAlex"
    base_url = "https://api.openalex.org"

    def __init__(self, transport=None):
        super().__init__(transport)
        self.min_interval = settings.openalex_min_interval

    def default_params(self) -> dict[str, str]:
        params = {}
        if settings.contact_email:
            params["mailto"] = settings.contact_email
        if settings.openalex_api_key:
            params["api_key"] = settings.openalex_api_key
        return params

    async def fetch(self, doi: str) -> SourceResult:
        work = await self.get_json(f"/works/doi:{quote(doi, safe='/')}")
        return SourceResult(self.name, OK, data=parse_openalex(work), http_status=200)
