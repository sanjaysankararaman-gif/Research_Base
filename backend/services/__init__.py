"""Academic data source clients.

`build_clients()` returns the network sources queried for every DOI.
Journal-level lookups (quartile, impact factor) run afterwards from local
files; see journal_rankings.py.
"""
from __future__ import annotations

from .base import ERROR, NOT_FOUND, OK, UNAVAILABLE, SourceClient, SourceResult
from .crossref import CrossrefClient
from .datacite import DataCiteClient
from .google_scholar import GoogleScholarClient
from .indexing import ScopusClient, WebOfScienceClient
from .openalex import OpenAlexClient
from .semantic_scholar import SemanticScholarClient

SOURCE_LABELS = {
    "crossref": "Crossref",
    "datacite": "DataCite",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "scopus": "Scopus",
    "web_of_science": "Web of Science",
    "sjr": "SCImago Journal Rank (file)",
    "jcr_file": "Impact-factor file (JCR)",
    "google_scholar": "Google Scholar",
}


def build_clients(transport=None) -> dict[str, SourceClient]:
    return {
        "crossref": CrossrefClient(transport),
        "datacite": DataCiteClient(transport),
        "openalex": OpenAlexClient(transport),
        "semantic_scholar": SemanticScholarClient(transport),
        "scopus": ScopusClient(transport),
        "web_of_science": WebOfScienceClient(transport),
        "google_scholar": GoogleScholarClient(transport),
    }


__all__ = [
    "OK", "NOT_FOUND", "ERROR", "UNAVAILABLE",
    "SourceClient", "SourceResult", "SOURCE_LABELS", "build_clients",
]
