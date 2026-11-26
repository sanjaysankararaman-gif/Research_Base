"""Map a merged record (data_merger.merge_sources) to a Publication and store it in Supabase.

publication_from_record() is pure, so it can be checked without a database.
sync_to_supabase() is the entry point used by the pipeline. It never raises.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ...config import settings
from ...processing.data_merger import flatten
from .supabase_db import Publication, UnsupportedPublicationType, save_publication

log = logging.getLogger("extractor.supabase_sync")

ISSN_RE = re.compile(r"\d{4}-\d{3}[\dXx]")
ISBN_RE = re.compile(r"ISBN\s+([\dXx-]+)")
PROC_ISSN_RE = re.compile(r"ISSN\s+(\d{4}-\d{3}[\dXx])")
VOL_RE = re.compile(r"Vol\.\s*([^,]+)")
PAGES_RE = re.compile(r"pp\.\s*(.+)$")
QUARTILES = {"Q1", "Q2", "Q3", "Q4", "N/A"}
INDEX_NAMES = {"crossref": "Crossref", "pubmed": "PubMed", "doaj": "DOAJ",
               "arxiv": "arXiv", "datacite": "DataCite"}


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _issn_parts(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """Print and electronic ISSN, collected from every source that supplied one.

    Values labelled "(ISSN)" / "(E-ISSN)" (Crossref) decide first. Unlabelled codes
    (OpenAlex) only fill a gap, and one code is never stored as both.
    """
    labelled: dict[str, str] = {}
    unlabelled: list[str] = []
    for v in record.get("metrics", {}).get("issn", {}).get("values", []):
        if not v["available"]:
            continue
        for part in re.split(r"[;,]", v["value"]):
            match = ISSN_RE.search(part)
            if not match:
                continue
            code = match.group(0).upper()
            if "E-ISSN" in part.upper():
                labelled.setdefault("electronic", code)
            elif "(ISSN)" in part.upper():
                labelled.setdefault("print", code)
            elif code not in unlabelled:
                unlabelled.append(code)
    print_issn, electronic = labelled.get("print"), labelled.get("electronic")
    for code in unlabelled:
        if code in (print_issn, electronic):
            continue
        if print_issn is None:
            print_issn = code
        elif electronic is None:
            electronic = code
    return print_issn, electronic


def _conference_dates(text: str | None) -> tuple[str | None, str | None]:
    """'2016-06-26 to 2016-07-01' -> start and end. A single date becomes the start only."""
    if not text:
        return None, None
    start, _, end = text.partition(" to ")
    return start.strip() or None, end.strip() or None


def _indexing(detail: dict[str, Any] | None) -> list[str] | None:
    detail = detail or {}
    names = []
    if detail.get("scopus") is True:
        names.append("Scopus")
    if detail.get("web_of_science") is True:
        names.append("Web of Science")
    names += [INDEX_NAMES.get(o, o) for o in detail.get("other") or []]
    return names or None


def publication_from_record(record: dict[str, Any]) -> Publication:
    """Build the Publication for the Supabase schema. Raises UnsupportedPublicationType
    for types the schema does not store, and ValueError when a required value is missing."""
    fields = flatten(record)["fields"]
    ptype = fields["publication_type"]
    if ptype not in ("Journal", "Conference"):
        raise UnsupportedPublicationType(f"{record['doi']}: '{ptype}' is not stored")
    if not fields["title"]:
        raise ValueError(f"{record['doi']}: no title, so it cannot be stored")

    # When the lookup resolved a preprint to its published version, store that DOI:
    # it is the version the department records.
    version = record.get("version") or {}
    doi = (version.get("published_doi") or record["doi"]).lower()

    base = dict(
        doi=doi,
        title=fields["title"],
        publication_type=ptype,
        authors=fields["authors"] or [],
        publication_year=fields["publication_year"],
        publication_date=fields["publication_date"],
        publisher=fields["publisher"],
        publication_url=fields["publication_url"],
        indexing_database=_indexing(fields["indexing_detail"]),
        home_institution=fields["home_institution"],
        faculty=fields["home_faculty"],
    )

    if ptype == "Journal":
        if not fields["journal"]:
            raise ValueError(f"{doi}: journal name missing, so it is not stored")
        print_issn, electronic_issn = _issn_parts(record)
        journal = {
            "journal_name": fields["journal"],
            "issn": print_issn,
            "e_issn": electronic_issn,
            "volume": _text(fields["volume"]),
            "issue": _text(fields["issue"]),
            "page_range": _text(fields["page_range"]),
            "quartile": fields["quartile"] if fields["quartile"] in QUARTILES else None,
            "impact_factor": fields["impact_factor"] if (fields["impact_factor"] or 0) < 1000 else None,
        }
        return Publication(**base, journal=journal)

    start, end = _conference_dates(fields["conference_dates"])
    proc_ids = fields["proceedings_isbn_issn"] or ""
    proc_vol = fields["proceedings_volume_pages"] or ""
    isbn, proc_issn = ISBN_RE.search(proc_ids), PROC_ISSN_RE.search(proc_ids)
    vol, pages = VOL_RE.search(proc_vol), PAGES_RE.search(proc_vol)
    conference = {
        "conference_name": fields["conference_name"],
        "conference_acronym": fields["conference_acronym"],
        "conference_start": start,
        "conference_end": end,
        "conference_location": fields["conference_location"],
        "proceedings_title": fields["proceedings_title"],
        "proceedings_publisher": fields["proceedings_publisher"],
        "proceedings_isbn": isbn.group(1) if isbn else None,
        "proceedings_issn": proc_issn.group(1) if proc_issn else None,
        "proceedings_volume": vol.group(1).strip() if vol else None,
        "proceedings_pages": pages.group(1).strip() if pages else None,
    }
    return Publication(**base, conference=conference)


def sync_to_supabase(record: dict[str, Any]) -> None:
    """Store a finished record in Supabase when SUPABASE_DB_URL is set.

    Never raises. A Supabase problem must not break a DOI lookup, so the reason is logged.
    """
    if not settings.supabase_db_url:
        return
    try:
        publication = publication_from_record(record)
    except UnsupportedPublicationType as exc:
        log.info("Not stored in Supabase: %s", exc)
        return
    except ValueError as exc:
        log.warning("Not stored in Supabase: %s", exc)
        return
    except Exception:
        log.exception("Could not map %s for Supabase", record.get("doi"))
        return
    try:
        save_publication(publication)
    except Exception:
        log.exception("Supabase storage failed for %s", publication.doi)
