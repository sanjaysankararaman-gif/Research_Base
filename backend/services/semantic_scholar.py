"""Semantic Scholar Academic Graph API client — citation metrics, fields of study.

Docs: https://api.semanticscholar.org/api-docs/graph
Endpoint used: GET https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}
Works without a key, but the shared anonymous pool is often rate limited
(HTTP 429). Set SEMANTIC_SCHOLAR_API_KEY (free on request) for reliable access.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from ..config import settings
from .base import OK, SourceClient, SourceResult

FIELDS = ",".join(
    [
        "paperId",
        "externalIds",
        "url",
        "title",
        "venue",
        "publicationVenue",
        "year",
        "publicationDate",
        "publicationTypes",
        "journal",
        "authors",
        "citationCount",
        "influentialCitationCount",
        "referenceCount",
        "fieldsOfStudy",
        "s2FieldsOfStudy",
        "isOpenAccess",
        "openAccessPdf",
        "tldr",
    ]
)


PREPRINT_SERVERS = re.compile(r"arxiv|biorxiv|medrxiv|chemrxiv|ssrn|research square|preprints|techrxiv|osf", re.I)
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)


def parse_semantic_scholar(paper: dict[str, Any], requested_doi: str | None = None) -> dict[str, Any]:
    venue = paper.get("publicationVenue") or {}
    venue_name = venue.get("name") or paper.get("venue") or ""
    preprint_only = bool(PREPRINT_SERVERS.search(venue_name))
    if preprint_only:
        # "arXiv.org" is where the preprint lives, not a journal or conference.
        venue, paper = {}, {**paper, "venue": None, "journal": None}
    if PREPRINT_SERVERS.search((paper.get("journal") or {}).get("name") or ""):
        paper = {**paper, "journal": None}   # e.g. {"name": "ArXiv", "volume": "abs/1810.04805"}
    journal = paper.get("journal") or {}
    oa_pdf = paper.get("openAccessPdf") or {}
    fields = paper.get("fieldsOfStudy") or []
    s2_fields = sorted({f.get("category") for f in paper.get("s2FieldsOfStudy", []) or [] if f.get("category")})
    tldr = paper.get("tldr") or {}
    types = paper.get("publicationTypes") or []
    venue_type = (venue.get("type") or "").lower()
    if preprint_only:
        publication_type = None   # S2 tags arXiv-only papers "JournalArticle"; don't trust that
    elif "Conference" in types or venue_type == "conference":
        publication_type = "Conference"
    elif "JournalArticle" in types or venue_type == "journal":
        publication_type = "Journal"
    else:
        publication_type = None
    is_conference = publication_type == "Conference"
    # Short single-word alternate names, e.g. "NeurIPS", "CVPR", "ICLR"
    acronyms = [a for a in venue.get("alternate_names", []) or []
                if " " not in a and 2 <= len(a) <= 12 and sum(ch.isupper() for ch in a) >= 2]
    return {
        "publication_type": publication_type,
        "conference_name": (venue.get("name") or paper.get("venue") or None) if is_conference else None,
        "conference_acronym": acronyms[0] if is_conference and acronyms else None,
        "pdf_url": oa_pdf.get("url") or None,
        "semantic_scholar_id": paper.get("paperId"),
        "semantic_scholar_url": paper.get("url"),
        "title": paper.get("title"),
        "authors": [a.get("name") for a in paper.get("authors", []) or [] if a.get("name")] or None,
        "publication_year": paper.get("year"),
        "publication_date": paper.get("publicationDate"),
        "journal": None if is_conference else (journal.get("name") or venue.get("name") or paper.get("venue") or None),
        "venue": venue.get("name") or paper.get("venue") or None,
        "venue_type": venue.get("type"),
        "volume": (journal.get("volume") or "").strip() or None,
        "pages": (journal.get("pages") or "").strip() or None,
        "article_type": ", ".join(paper.get("publicationTypes") or []) or None,
        "citation_count": paper.get("citationCount"),
        "influential_citation_count": paper.get("influentialCitationCount"),
        "reference_count": paper.get("referenceCount"),
        "fields_of_study": sorted(set(fields) | set(s2_fields)) or None,
        "is_open_access": paper.get("isOpenAccess"),
        "oa_url": oa_pdf.get("url") or None,
        "tldr": tldr.get("text"),
        "external_ids": paper.get("externalIds") or None,
        "published_doi": _published_doi(paper, requested_doi),
    }


def _published_doi(paper: dict[str, Any], requested_doi: str | None) -> str | None:
    """S2 merges a preprint with its published version; its DOI is then the published one."""
    doi = ((paper.get("externalIds") or {}).get("DOI") or "").lower()
    if not doi or not requested_doi or doi == requested_doi.lower() or ARXIV_DOI.match(doi):
        return None
    return doi


class SemanticScholarClient(SourceClient):
    name = "semantic_scholar"
    label = "Semantic Scholar"
    base_url = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, transport=None):
        super().__init__(transport)
        self.min_interval = settings.semantic_scholar_min_interval
        # The anonymous pool is shared by everyone and often answers 429. It is the only
        # source that knows where many preprints were published, so wait longer for it.
        if not settings.semantic_scholar_api_key:
            self.max_retries = max(settings.max_retries, 5)

    def headers(self) -> dict[str, str]:
        h = super().headers()
        if settings.semantic_scholar_api_key:
            h["x-api-key"] = settings.semantic_scholar_api_key
        return h

    async def fetch(self, doi: str) -> SourceResult:
        # S2 often does not index arXiv DOIs, but always knows the arXiv ID.
        m = ARXIV_DOI.match(doi)
        paper_id = f"ARXIV:{m.group(1)}" if m else f"DOI:{quote(doi, safe='/')}"
        paper = await self.get_json(f"/paper/{paper_id}", {"fields": FIELDS})
        return SourceResult(self.name, OK, data=parse_semantic_scholar(paper, doi), http_status=200)
