"""Consolidate per-source results into one standardized record per DOI.

Rules enforced here:
* Every value keeps its source and retrieval timestamp.
* Values from different sources are never combined into a new number.
* "Unavailable" (value is None, with a reason) is distinct from a real 0.
* Nothing is invented: a metric no source returned is marked unavailable.
* Partial success: any source that returned data contributes, regardless of
  whether other sources failed.

Standardized record shape (stored in SQLite, returned by the API, and the
input to the Excel export and the Supabase copy):

{
  "doi": "10.1038/nature14539",
  "doi_url": "https://doi.org/10.1038/nature14539",
  "status": "complete" | "partial" | "failed" | "not_found",
  "retrieved_at": ISO timestamp,
  "core": {key: {"value", "source", "retrieved_at"}},       # display fields
  "metrics": {key: {
       "key", "label", "category", "level", "merge", "value_type", "description",
       "available": bool,              # at least one source supplied a value
       "preferred": {"value","source","retrieved_at"} | None,   # "prefer" metrics only
       "values": [{"source","value","available","reason","retrieved_at"}],
       "unavailable_reason": str | None
  }},
  "sources": {source: {"source","status","message","http_status","retrieved_at"}},
  "errors": [{"source","message"}],
  "incomplete_fields": [metric keys that are requested but unavailable],
  "missing_required": [metric keys that are required but unavailable],
}
"""
from __future__ import annotations

import re
from typing import Any

from ..config import settings
from ..timeutil import utc_now
from ..services import ERROR, NOT_FOUND, OK, SOURCE_LABELS, UNAVAILABLE, SourceResult
from .home_institution import home_institution
from .metric_mapper import CORE_KEYS, METRICS, PER_SOURCE

# Sources that can say whether a DOI exists. Indexing checks (Scopus/WoS) and
# journal-ranking files only add detail and do not decide found/not found.
PRIMARY_SOURCES = ("crossref", "openalex", "semantic_scholar")


def has_value(value: Any) -> bool:
    """None, empty strings and empty collections are 'unavailable'. 0 and False are real values."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _reason_for(result: SourceResult | None, source: str, field: str) -> str:
    label = SOURCE_LABELS.get(source, source)
    if result is None:
        return f"{label} was not queried"
    if result.status == NOT_FOUND:
        return f"{label} has no record for this DOI"
    if result.status == ERROR:
        return f"{label} request failed: {result.message or 'unknown error'}"
    if result.status == UNAVAILABLE:
        return result.message or f"{label} is not available"
    return f"{label} does not provide this value for this paper"


def merge_sources(
    doi: str,
    results: dict[str, SourceResult],
    preprint_results: dict[str, SourceResult] | None = None,
    published_doi: str | None = None,
) -> dict[str, Any]:
    """Merge per-source results for `doi`.

    When `published_doi` is given, `results` describe the published version of
    the preprint `doi` (and `preprint_results` the preprint itself).
    """
    metrics: dict[str, Any] = {}
    for m in METRICS:
        values = []
        for source, field in m.sources.items():
            result = results.get(source)
            value = result.data.get(field) if (result and result.status == OK) else None
            available = has_value(value)
            values.append(
                {
                    "source": source,
                    "value": value if available else None,
                    "available": available,
                    "reason": None if available else _reason_for(result, source, field),
                    "retrieved_at": result.retrieved_at if result else None,
                }
            )
        available_values = [v for v in values if v["available"]]
        preferred = None
        if m.merge != PER_SOURCE and available_values:
            first = available_values[0]
            preferred = {"value": first["value"], "source": first["source"], "retrieved_at": first["retrieved_at"]}
        unavailable_reason = None
        if not available_values:
            unavailable_reason = m.unavailable_reason or (
                "; ".join(v["reason"] for v in values) if values else "No source provides this metric"
            )
        metrics[m.key] = {
            "key": m.key,
            "label": m.label,
            "category": m.category,
            "level": m.level,
            "merge": m.merge,
            "value_type": m.value_type,
            "description": m.description,
            "requested": m.requested,
            "available": bool(available_values),
            "preferred": preferred,
            "values": values,
            "unavailable_reason": unavailable_reason,
        }

    _complete_date(metrics["publication_date"])
    _resolve_type(metrics["publication_type"])
    version = _version_info(doi, results, metrics, preprint_results, published_doi)

    core = {}
    for key in CORE_KEYS:
        pref = metrics[key]["preferred"]
        core[key] = pref if pref else {"value": None, "source": None, "retrieved_at": None}

    sources = {name: r.to_dict() for name, r in results.items()}
    errors = [
        {"source": name, "message": r.message or "Request failed"}
        for name, r in results.items()
        if r.status == ERROR
    ]
    incomplete = [m.key for m in METRICS if m.requested and not metrics[m.key]["available"]]
    missing_required = [m.key for m in METRICS if m.required and not metrics[m.key]["available"]]

    queried = [r for n, r in results.items() if n in PRIMARY_SOURCES and r.status != UNAVAILABLE]
    any_ok = any(r.status == OK for r in queried)
    if not any_ok:
        status = "not_found" if queried and all(r.status == NOT_FOUND for r in queried) else "failed"
    elif errors or missing_required:
        status = "partial"
    else:
        status = "complete"

    return {
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}",
        "status": status,
        "retrieved_at": utc_now(),
        "core": core,
        "metrics": metrics,
        "sources": sources,
        "errors": errors,
        "incomplete_fields": incomplete,
        "missing_required": missing_required,
        "version": version,
    }


def _complete_date(metric: dict[str, Any]) -> None:
    """Crossref sometimes has only the year and month ("2015-05"). Use another source's full
    date only when it agrees with that year and month; otherwise keep Crossref's.

    A day of "01" is not used: OpenAlex and Semantic Scholar write the 1st when they only
    know the month, so it would look exact without being known."""
    pref = metric["preferred"]
    if not pref or len(str(pref["value"])) == 10:
        return
    for v in metric["values"]:
        value = str(v["value"] or "")
        if (v["available"] and len(value) == 10 and value.startswith(str(pref["value"]) + "-")
                and not value.endswith("-01")):
            metric["preferred"] = {"value": value, "source": v["source"], "retrieved_at": v["retrieved_at"]}
            return


def _resolve_type(metric: dict[str, Any]) -> None:
    """Crossref's type wins, except where it is vague: Springer and others register many
    conference papers as book chapters, and some chapters as "other". Then a source that
    says "Conference" (OpenAlex, Semantic Scholar) decides, and "Other" takes any more
    specific type. Journal and Conference from Crossref are never overridden."""
    pref = metric["preferred"]
    if not pref or pref["value"] not in ("Book chapter", "Book", "Other"):
        return
    others = [v for v in metric["values"] if v["available"] and v["source"] != pref["source"]]
    better = next((v for v in others if v["value"] == "Conference"), None)
    if better is None and pref["value"] == "Other":
        better = next((v for v in others if v["value"] in ("Journal", "Book chapter", "Book")), None)
    if better:
        metric["preferred"] = {"value": better["value"], "source": better["source"],
                               "retrieved_at": better["retrieved_at"]}


def _value(metrics: dict[str, Any], key: str) -> Any:
    pref = metrics.get(key, {}).get("preferred")
    return pref["value"] if pref else None


def _is_preprint(results: dict[str, SourceResult]) -> str | None:
    """Name of the preprint server if any primary source says this DOI is a preprint."""
    for name in ("datacite", "crossref", "openalex"):
        r = results.get(name)
        if r and r.status == OK and r.data.get("is_preprint"):
            return r.data.get("repository") or (r.data.get("publisher") if name == "datacite" else None) or "a preprint server"
    return None


def _version_info(doi, results, metrics, preprint_results, published_doi) -> dict[str, Any] | None:
    """Explain preprint vs. published version, and keep preprint-server data out of published fields."""
    if published_doi:
        server = _is_preprint(preprint_results or {}) or "a preprint server"
        return {
            "is_preprint": True,
            "entered_doi": doi,
            "published_doi": published_doi,
            "note": (f"The DOI you entered is the preprint on {server}. The details shown are for the "
                     f"published version, DOI {published_doi}."),
        }

    server = _is_preprint(results)
    if not server:
        return None
    ptype = _value(metrics, "publication_type")
    if ptype in ("Journal", "Conference"):
        venue = _value(metrics, "journal") or _value(metrics, "conference_name") or "a " + ptype.lower()
        year = _value(metrics, "publication_year")
        # The publisher DataCite reports for a preprint is the preprint server (e.g. arXiv),
        # not the publisher of the journal/conference version.
        pub = metrics["publisher"]
        if pub["preferred"] and pub["preferred"]["source"] == "datacite":
            reason = (f"Only the preprint server ({server}) is known. The publisher of the published version "
                      "is not in any source, because that version has no DOI.")
            pub["preferred"], pub["available"], pub["unavailable_reason"] = None, False, reason
            for v in pub["values"]:
                v.update(value=None, available=False, reason=reason)
        return {
            "is_preprint": True,
            "entered_doi": doi,
            "published_doi": None,
            "note": (f"The DOI you entered is the preprint on {server}. The paper was published in "
                     f"{venue}{f' ({year})' if year else ''}, but that version has no DOI of its own, so some "
                     "details (such as publisher, conference dates and location) are not available from any source."),
        }
    s2 = results.get("semantic_scholar")
    if s2 is not None and s2.status == ERROR:
        note = (f"This DOI is a preprint on {server}. Semantic Scholar, which records where preprints were "
                "later published, could not be reached, so the published version could not be checked. "
                "Look this DOI up again in a minute.")
    else:
        note = f"This DOI is a preprint on {server}. No formally published version was found."
    return {"is_preprint": True, "entered_doi": doi, "published_doi": None, "note": note}


# ----------------------------------------------------------------------------- flat view for the frontend

def _pick(record: dict[str, Any], key: str) -> Any:
    pref = record.get("metrics", {}).get(key, {}).get("preferred")
    return pref["value"] if pref else None


def _per_source(record: dict[str, Any], key: str) -> dict[str, Any]:
    return {
        v["source"]: (v["value"] if v["available"] else None)
        for v in record.get("metrics", {}).get(key, {}).get("values", [])
    }


def _best_issn(record: dict[str, Any]) -> str | None:
    """Crossref sometimes lists only the print ISSN while OpenAlex has print + electronic."""
    values = [v["value"] for v in record.get("metrics", {}).get("issn", {}).get("values", []) if v["available"]]
    if not values:
        return None
    count = lambda text: len(re.findall(r"\d{4}-\d{3}[\dXx]", text))
    best = max(values, key=count)
    return values[0] if count(values[0]) >= count(best) else best


def _indexing_text(ix: dict[str, Any], sources: dict[str, Any]) -> str:
    parts = []
    for key, label in (("scopus", "Scopus"), ("web_of_science", "Web of Science")):
        value = ix.get(key)
        if value is True:
            parts.append(label)
        elif value is False:
            parts.append(f"Not in {label}")
        else:
            parts.append(f"{label}: not verified")
    other = ix.get("other") or []
    if other:
        names = {"crossref": "Crossref", "pubmed": "PubMed", "doaj": "DOAJ", "arxiv": "arXiv",
                 "datacite": "DataCite"}
        parts.append("Other: " + ", ".join(names.get(o, o) for o in other))
    return "; ".join(parts)


def flatten(record: dict[str, Any]) -> dict[str, Any]:
    """One plain value per field (numbered as in the department's field list), plus provenance.

    `fields` values are null when unavailable; `provenance[field]` says which
    source supplied the value and when, or why it is unavailable.
    """
    metrics = record.get("metrics", {})
    ix_values = _per_source(record, "indexing")
    indexing = {
        "scopus": ix_values.get("scopus"),
        "web_of_science": ix_values.get("web_of_science"),
        "other": ix_values.get("openalex") or [],
    }
    authors = _pick(record, "authors") or []
    home = home_institution(_per_source(record, "author_affiliations"), authors)
    pdf_url = _pick(record, "pdf_url")
    oa = _per_source(record, "is_open_access")
    oa_value = next((v for v in (oa.get("openalex"), oa.get("semantic_scholar")) if v is not None), None)

    fields = {
        # 1. Common
        "title": _pick(record, "title"),
        "publication_type": _pick(record, "publication_type"),
        "authors": authors,
        "authors_text": ", ".join(authors) if authors else None,
        "publication_year": _pick(record, "publication_year"),
        "publisher": _pick(record, "publisher"),
        "doi": record["doi"],
        "publication_url": _pick(record, "publication_url") or record.get("doi_url"),
        "indexing": _indexing_text(indexing, record.get("sources", {})),
        "indexing_detail": indexing,
        # 2. Journal
        "journal": _pick(record, "journal"),
        "issn": _best_issn(record),
        "volume": _pick(record, "volume"),
        "issue": _pick(record, "issue"),
        "page_range": _pick(record, "page_range"),
        "quartile": _pick(record, "quartile"),
        "quartile_detail": (record.get("sources", {}).get("sjr") or {}).get("message")
        if _pick(record, "quartile") else None,
        "impact_factor": _pick(record, "impact_factor"),
        # 3. Conference
        "conference_name": _pick(record, "conference_name"),
        "conference_acronym": _pick(record, "conference_acronym"),
        "conference_dates": _pick(record, "conference_dates"),
        "conference_location": _pick(record, "conference_location"),
        "proceedings_title": _pick(record, "proceedings_title"),
        "proceedings_publisher": _pick(record, "proceedings_publisher"),
        "proceedings_isbn_issn": _pick(record, "proceedings_isbn_issn"),
        "proceedings_volume_pages": _pick(record, "proceedings_volume_pages"),
        # 4. Date and institution
        "publication_date": _pick(record, "publication_date"),
        "home_institution": home["home_institution"],
        "home_faculty": home["faculty"],
        "home_faculty_text": "; ".join(f"{f['name']} ({f['faculty_id']})" for f in home["faculty"]) or (
            f"None on the faculty list (home authors: {', '.join(home['home_authors'])})"
            if home["home_authors"] else None),
        "home_authors": home["home_authors"],
        "institutions_checked": "author_affiliations" in metrics,   # False for papers saved before field 27
        "author_affiliations": home["affiliations"],
        "author_affiliations_text": "\n".join(
            f"{a['author']}: {'; '.join(a['institutions']) or 'not listed'}" for a in home["affiliations"]) or None,
        # Additional
        "published_doi": (record.get("version") or {}).get("published_doi"),
        "version_note": (record.get("version") or {}).get("note"),
        "pdf_url": pdf_url,
        "pdf_available": True if pdf_url else (False if oa_value is False else None),
        "is_open_access": oa_value,
        "citation_counts": _per_source(record, "citation_count"),
    }

    provenance = {}
    for key, m in metrics.items():
        if m["merge"] == PER_SOURCE:
            provenance[key] = {
                "sources": [{"source": v["source"], "retrieved_at": v["retrieved_at"]}
                            for v in m["values"] if v["available"]],
                "reason": m["unavailable_reason"],
            }
        elif m["preferred"]:
            provenance[key] = {"source": m["preferred"]["source"], "retrieved_at": m["preferred"]["retrieved_at"]}
        else:
            provenance[key] = {"source": None, "reason": m["unavailable_reason"]}

    provenance["home_institution"] = _home_provenance(record, home)
    provenance["home_faculty"] = provenance["home_institution"]
    if home["home_authors"] and not home["faculty"]:
        provenance["home_faculty"] = {"source": None, "reason": (
            f"{', '.join(home['home_authors'])}: from {settings.home_institution_name}, but not on the "
            f"faculty list ({settings.faculty_csv_path.name}).")}

    return {
        "id": record.get("id"),
        "doi": record["doi"],
        "doi_url": record.get("doi_url"),
        "status": record["status"],
        "retrieved_at": record.get("retrieved_at"),
        "updated_at": record.get("updated_at"),
        "fields": fields,
        "provenance": provenance,
        "sources": {k: {"status": v["status"], "message": v.get("message")}
                    for k, v in record.get("sources", {}).items()},
        "missing_required": record.get("missing_required", []),
    }


def _home_provenance(record: dict[str, Any], home: dict[str, Any]) -> dict[str, Any]:
    """Where fields 25-26 come from, or why they are empty."""
    if home["home_institution"]:
        return {"source": home["affiliation_source"] or "faculty list", "reason": None}
    if "author_affiliations" not in record.get("metrics", {}):
        reason = "This paper was saved before institutions were recorded. Look up its DOI again."
    elif home["affiliation_source"] is None:
        reason = ("No source lists the authors' affiliations, and no author matches the faculty list "
                  f"of {settings.home_institution_name}.")
    else:
        reason = f"No author is from {settings.home_institution_name}."
    return {"source": None, "reason": reason}


def library_row(record: dict[str, Any]) -> dict[str, Any]:
    """Compact row for the My Library table."""
    authors = _pick(record, "authors") or []
    home = home_institution(_per_source(record, "author_affiliations"), authors)
    return {
        "id": record.get("id"),
        "title": _pick(record, "title"),
        "authors": authors,
        "authors_text": ", ".join(authors) if authors else None,
        "publication_year": _pick(record, "publication_year"),
        "publication_type": _pick(record, "publication_type"),
        "doi": record["doi"],
        "venue": _pick(record, "journal") or _pick(record, "conference_name"),
        "publication_date": _pick(record, "publication_date"),
        "home_institution": home["home_institution"],
        "home_faculty": [{"faculty_id": f["faculty_id"], "name": f["name"]} for f in home["faculty"]],
        "status": record["status"],
        "updated_at": record.get("updated_at"),
    }
