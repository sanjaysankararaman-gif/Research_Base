"""Central metric registry — the ONE place that defines which metrics exist,
which sources can supply them, and how they appear as spreadsheet columns.

To add a metric
---------------
Append a `MetricDefinition` to `METRICS`. `sources` maps a source name to the
field that source's service module extracts (see `parse_*` functions in
`backend/services/`). If the field is not extracted yet, add one line to that
service's `parse_*` function. Merging, storage and the API read this registry;
to show the field, add it to `flatten()` in data_merger.py, the Excel columns
(integrations/excel_export.py) and the details page (UI/js/details.js).

To remove a metric from output / spreadsheets: set `requested=False`.
To change a spreadsheet column name: edit `column`.

Merge strategies
----------------
"per_source"  values from each source are kept and shown separately and are
              NEVER combined (e.g. citation counts, which legitimately differ
              between Crossref, OpenAlex and Semantic Scholar).
"prefer"      descriptive fields (title, journal...). The first available
              source in `sources` order becomes the displayed value, but every
              source's value is still preserved in the record.

Metric levels
-------------
Only "paper" level metrics can be obtained from a DOI. Author-level metrics
(h-index) and journal-level metrics (impact factor) are registered with no
sources and an explicit reason, so they are always reported as unavailable
instead of being confused with paper metrics or invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PER_SOURCE = "per_source"
PREFER = "prefer"


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    category: str                      # bibliographic | citation | access | classification | affiliation | identifier
    sources: dict[str, str]            # source name -> field in that source's parsed data
    merge: str = PREFER
    level: str = "paper"               # paper | author | journal
    requested: bool = True             # include in standard output / spreadsheet rows
    required: bool = False             # a record missing this is marked "partial"
    column: str | None = None          # spreadsheet column (defaults to label)
    value_type: str = "text"           # text | number | year | boolean | list | date | url | json
    description: str = ""
    unavailable_reason: str | None = None  # fixed reason when no DOI-based source can provide it
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def column_name(self) -> str:
        return self.column or self.label


CR, OA, S2, DC = "crossref", "openalex", "semantic_scholar", "datacite"
SCOPUS, WOS, SJR, JCR = "scopus", "web_of_science", "sjr", "jcr_file"

# Order and numbering follow "Essential Fields for Research Publication Database".
# `extra={"field_no": n, "group": ...}` ties each entry to that document.
COMMON, JOURNAL, CONFERENCE, ADDITIONAL = "common", "journal", "conference", "additional"
# Fields 24-27 were added by the department after the original list of 23.
DATE_INSTITUTION = "date_institution"


def _f(no: int, group: str) -> dict[str, Any]:
    return {"field_no": no, "group": group}


METRICS: list[MetricDefinition] = [
    # ---------------------------------------------------------- 1. Common fields (journal and conference)
    MetricDefinition("title", "Publication Title", "bibliographic", {CR: "title", OA: "title", S2: "title", DC: "title"},
                     required=True, extra=_f(1, COMMON)),
    MetricDefinition("publication_type", "Publication Type", "bibliographic",
                     {CR: "publication_type", S2: "publication_type", OA: "publication_type", DC: "publication_type"},
                     required=True, extra=_f(2, COMMON),
                     description="Journal / Conference, from the Crossref work type (fallback: OpenAlex, "
                                 "Semantic Scholar). Other types (book chapter, preprint) are reported as such."),
    MetricDefinition("authors", "Author Names", "bibliographic",
                     {CR: "authors", OA: "authors", S2: "authors", DC: "authors"},
                     required=True, value_type="list", extra=_f(3, COMMON),
                     description="In published order."),
    MetricDefinition("publication_year", "Publication Year", "bibliographic",
                     {CR: "publication_year", OA: "publication_year", S2: "publication_year", DC: "publication_year"},
                     required=True, value_type="year", extra=_f(4, COMMON)),
    MetricDefinition("publisher", "Publisher", "bibliographic", {CR: "publisher", DC: "publisher"}, extra=_f(5, COMMON)),
    # 6. DOI is the record key: always present, see data_merger.flatten().
    MetricDefinition("publication_url", "Publication URL", "bibliographic",
                     {CR: "publication_url", OA: "publication_url", DC: "publication_url"}, value_type="url",
                     extra=_f(7, COMMON),
                     description="Publisher landing page registered with Crossref."),
    MetricDefinition("indexing", "Indexing Database", "indexing",
                     {SCOPUS: "indexed_scopus", WOS: "indexed_wos", OA: "indexed_in"},
                     merge=PER_SOURCE, extra=_f(8, COMMON),
                     description="Scopus and Web of Science are checked through their official APIs and need "
                                 "institutional keys. OpenAlex lists other indexes (Crossref, PubMed, DOAJ, arXiv)."),

    # ---------------------------------------------------------- 2. Journal-specific fields
    MetricDefinition("journal", "Journal Name", "journal",
                     {CR: "journal", OA: "journal", S2: "journal", DC: "journal"},
                     extra=_f(9, JOURNAL)),
    MetricDefinition("issn", "ISSN / E-ISSN", "journal", {CR: "issn_eissn", OA: "issn_eissn"}, extra=_f(10, JOURNAL)),
    MetricDefinition("volume", "Volume", "journal", {CR: "volume", OA: "volume", S2: "volume"}, extra=_f(11, JOURNAL)),
    MetricDefinition("issue", "Issue", "journal", {CR: "issue", OA: "issue"}, extra=_f(12, JOURNAL)),
    MetricDefinition("page_range", "Page Range / Article Number", "journal",
                     {CR: "page_range_or_article_number", OA: "pages", S2: "pages"}, extra=_f(13, JOURNAL)),
    MetricDefinition("quartile", "Quartile", "journal", {SJR: "quartile"}, level="journal", extra=_f(14, JOURNAL),
                     description="Journal-level. SCImago (SJR) best quartile looked up by ISSN in the "
                                 "department's SJR file. This is not the Clarivate JCR quartile."),
    MetricDefinition("impact_factor", "Impact Factor", "journal", {JCR: "impact_factor"}, level="journal",
                     value_type="number", extra=_f(15, JOURNAL),
                     description="Journal-level. Clarivate Journal Impact Factor is licensed. Only available "
                                 "from a department-supplied JCR export."),

    # ---------------------------------------------------------- 3. Conference-specific fields
    MetricDefinition("conference_name", "Conference Name", "conference",
                     {CR: "conference_name", S2: "conference_name", OA: "conference_name"}, extra=_f(16, CONFERENCE)),
    MetricDefinition("conference_acronym", "Conference Acronym", "conference",
                     {CR: "conference_acronym", S2: "conference_acronym"}, extra=_f(17, CONFERENCE)),
    MetricDefinition("conference_dates", "Conference Dates", "conference", {CR: "conference_dates"},
                     extra=_f(18, CONFERENCE)),
    MetricDefinition("conference_location", "Conference Location", "conference", {CR: "conference_location"},
                     extra=_f(19, CONFERENCE)),
    MetricDefinition("proceedings_title", "Proceedings Title", "conference", {CR: "proceedings_title"},
                     extra=_f(20, CONFERENCE)),
    MetricDefinition("proceedings_publisher", "Proceedings Publisher", "conference", {CR: "proceedings_publisher"},
                     extra=_f(21, CONFERENCE)),
    MetricDefinition("proceedings_isbn_issn", "Proceedings ISBN / ISSN", "conference",
                     {CR: "proceedings_isbn_issn"}, extra=_f(22, CONFERENCE)),
    MetricDefinition("proceedings_volume_pages", "Proceedings Volume / Page Range", "conference",
                     {CR: "proceedings_volume_pages", OA: "volume_pages"}, extra=_f(23, CONFERENCE)),

    # ---------------------------------------------------------- 4. Date and institution
    MetricDefinition("publication_date", "Publication Date", "bibliographic",
                     {CR: "publication_date", OA: "publication_date", S2: "publication_date"}, value_type="date",
                     extra=_f(24, DATE_INSTITUTION),
                     description="Earliest publication date (online or print) from Crossref, as YYYY-MM-DD, or "
                                 "YYYY-MM when the publisher deposited no day. A full date from OpenAlex or "
                                 "Semantic Scholar is used only when it agrees with Crossref's year and month "
                                 "and its day is not 01 (their placeholder for an unknown day)."),
    # 25 Home Institution and 26 Home Faculty are worked out from these affiliations and the
    # faculty list (processing/home_institution.py); they are not fetched from a source.
    MetricDefinition("author_affiliations", "Author Affiliations", "affiliation",
                     {OA: "authorships", CR: "authorships"}, value_type="json", extra=_f(27, DATE_INSTITUTION),
                     description="Institutions of each author, in published order: OpenAlex (normalized "
                                 "institution names with ROR IDs), else the affiliation text in Crossref."),

    # ---------------------------------------------------------- Additional (not in the field list)
    MetricDefinition("published_doi", "Published Version DOI", "identifier",
                     {CR: "published_doi", DC: "published_doi", S2: "published_doi"}, extra=_f(0, ADDITIONAL),
                     description="When the entered DOI is a preprint, the DOI of the formally published version."),
    MetricDefinition("pdf_url", "PDF Link", "access", {OA: "pdf_url", S2: "pdf_url"}, value_type="url",
                     extra=_f(0, ADDITIONAL), description="Legal open-access PDF, when one exists."),
    MetricDefinition("is_open_access", "Open Access", "access",
                     {OA: "is_open_access", S2: "is_open_access"}, merge=PER_SOURCE, value_type="boolean",
                     extra=_f(0, ADDITIONAL)),
    MetricDefinition("citation_count", "Citation Count", "citation",
                     {CR: "citation_count", OA: "citation_count", S2: "citation_count"},
                     merge=PER_SOURCE, value_type="number", requested=False, extra=_f(0, ADDITIONAL),
                     description="Each source indexes different citing works, so counts differ. They are kept "
                                 "separately and never combined."),
    MetricDefinition("influential_citation_count", "Influential Citation Count", "citation",
                     {S2: "influential_citation_count"}, merge=PER_SOURCE, value_type="number", requested=False,
                     extra=_f(0, ADDITIONAL)),
    MetricDefinition("abstract", "Abstract", "bibliographic", {CR: "abstract", OA: "abstract"}, requested=False,
                     extra=_f(0, ADDITIONAL)),
    MetricDefinition("article_type", "Source Work Type", "bibliographic",
                     {CR: "article_type", S2: "article_type"}, merge=PER_SOURCE, requested=False,
                     extra=_f(0, ADDITIONAL)),
    MetricDefinition("openalex_id", "OpenAlex ID", "identifier", {OA: "openalex_id"}, requested=False,
                     extra=_f(0, ADDITIONAL)),
    MetricDefinition("semantic_scholar_id", "Semantic Scholar ID", "identifier", {S2: "semantic_scholar_id"},
                     requested=False, extra=_f(0, ADDITIONAL)),
    MetricDefinition("author_h_index", "Author h-index", "author_level", {}, level="author", requested=False,
                     value_type="number", extra=_f(0, ADDITIONAL),
                     unavailable_reason="h-index is an author-level metric, not a property of a paper, and "
                                        "cannot be derived from this DOI."),
]

METRICS_BY_KEY: dict[str, MetricDefinition] = {m.key: m for m in METRICS}

# Values copied into the papers table columns (see database.save_paper).
CORE_KEYS = ["title", "publication_type", "authors", "journal", "publisher", "publication_year", "publication_date"]


def describe_metrics() -> list[dict[str, Any]]:
    """Public description of the registry (served at GET /metrics)."""
    return [
        {
            "key": m.key,
            "label": m.label,
            "category": m.category,
            "level": m.level,
            "merge": m.merge,
            "sources": list(m.sources),
            "requested": m.requested,
            "required": m.required,
            "column": m.column_name,
            "value_type": m.value_type,
            "description": m.description,
            "unavailable_reason": m.unavailable_reason,
            "field_no": m.extra.get("field_no"),
            "group": m.extra.get("group"),
        }
        for m in METRICS
    ]


def _validate_registry() -> None:
    from ..services import SOURCE_LABELS

    keys = set()
    for m in METRICS:
        if m.key in keys:
            raise ValueError(f"Duplicate metric key: {m.key}")
        keys.add(m.key)
        if m.merge not in (PER_SOURCE, PREFER):
            raise ValueError(f"Unknown merge strategy for {m.key}: {m.merge}")
        for s in m.sources:
            if s not in SOURCE_LABELS:
                raise ValueError(f"Metric {m.key} references unknown source {s}")
        if not m.sources and not m.unavailable_reason:
            raise ValueError(f"Metric {m.key} has no sources and no unavailable_reason")


_validate_registry()
