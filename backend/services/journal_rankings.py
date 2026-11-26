"""Journal-level lookups by ISSN: Quartile and Impact Factor (fields 14 and 15).

These are journal-level metrics. They are not properties of a paper and no
free API returns them for a DOI, so they come from files the department
provides:

* Quartile -> SCImago Journal Rank (SJR) export, free to download:
  https://www.scimagojr.com/journalrank.php  ("Download data" button).
  Save the CSV and set SJR_CSV_PATH. Uses the "SJR Best Quartile" column.
  Note: this is the SCImago (Scopus-based) quartile. It is not the Clarivate
  JCR quartile, and the record labels it as such.

* Impact Factor -> Journal Impact Factor is published by Clarivate (Journal
  Citation Reports) under licence. If the department has JCR access, export
  ISSN + JIF to a CSV with columns `issn,impact_factor` (optionally `year`)
  and set IMPACT_FACTOR_CSV_PATH.

When a file is not configured, or the journal is not in it, the value is
reported as unavailable with the reason. Nothing is estimated.
"""
from __future__ import annotations

import csv
import html
import logging
import re
from pathlib import Path

from ..config import settings
from .base import NOT_FOUND, OK, UNAVAILABLE, SourceResult

log = logging.getLogger("extractor.journal_rankings")


def _norm_issn(value: str) -> str:
    v = "".join(ch for ch in str(value).upper() if ch.isalnum())
    return v if len(v) == 8 else ""


def _sniff_reader(fh):
    sample = fh.read(4096)
    fh.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(fh, dialect=dialect)


class _FileLookup:
    """Loads a CSV once and caches it in memory (reloads if the file changes)."""

    def __init__(self, path_getter):
        self._path_getter = path_getter
        self._mtime = None
        self._index: dict[str, dict] = {}

    def path(self) -> Path | None:
        p = self._path_getter()
        return p if p and p.is_file() else None

    def index(self) -> dict[str, dict]:
        p = self.path()
        if not p:
            return {}
        mtime = p.stat().st_mtime
        if mtime != self._mtime:
            self._index = self.load(p)
            self._mtime = mtime
            log.info("Loaded %d ISSN entries from %s", len(self._index), p.name)
        return self._index

    def load(self, path: Path) -> dict[str, dict]:  # pragma: no cover - overridden
        raise NotImplementedError


def _norm_title(value: str | None) -> str:
    """'The Journal of Physical Chemistry B' and 'Journal of physical chemistry. B' match."""
    text = re.sub(r"[^a-z0-9 ]", " ", html.unescape(value or "").lower().replace("&", " and "))
    words = [w for w in text.split() if w != "the"]
    return " ".join(words)


class SJRLookup(_FileLookup):
    """SCImago export: ISSN -> best quartile. Also an exact-title index, used only for
    journals that have no ISSN in any source, and only for titles that occur once in the file."""

    def load(self, path: Path) -> dict[str, dict]:
        out: dict[str, dict] = {}
        titles: dict[str, dict | None] = {}
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in _sniff_reader(fh):
                quartile = (row.get("SJR Best Quartile") or "").strip()
                entry = {"quartile": quartile if quartile in ("Q1", "Q2", "Q3", "Q4") else None,
                         "title": (row.get("Title") or "").strip()}
                for issn in (row.get("Issn") or "").split(","):
                    key = _norm_issn(issn)
                    if key:
                        out.setdefault(key, entry)   # the file is in rank order: keep the first
                title = _norm_title(entry["title"])
                if title:
                    titles[title] = None if title in titles else entry
        self.titles = {k: v for k, v in titles.items() if v}
        return out

    def by_title(self, title: str | None) -> dict | None:
        self.index()   # (re)load the file if needed
        return getattr(self, "titles", {}).get(_norm_title(title)) if title else None

    def year(self) -> str | None:
        """'2025' from scimagojr_2025.csv, for the provenance message."""
        p = self.path()
        m = re.search(r"(19|20)\d\d", p.name) if p else None
        return m.group(0) if m else None


class ImpactFactorLookup(_FileLookup):
    def load(self, path: Path) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in _sniff_reader(fh):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                key = _norm_issn(row.get("issn", ""))
                try:
                    value = float(row.get("impact_factor", ""))
                except ValueError:
                    continue
                if key:
                    out[key] = {"impact_factor": value, "year": row.get("year") or None}
        return out


sjr = SJRLookup(lambda: settings.sjr_csv_path)
impact_factors = ImpactFactorLookup(lambda: settings.impact_factor_csv_path)


def _lookup(table: _FileLookup, issns: list[str]) -> dict | None:
    idx = table.index()
    for issn in issns:
        hit = idx.get(_norm_issn(issn))
        if hit:
            return hit
    return None


def lookup_quartile(issns: list[str], journal_titles: list[str] | None = None) -> SourceResult:
    """SCImago best quartile by ISSN. A journal with no ISSN in any source is matched by its
    exact title instead (punctuation and case ignored)."""
    if not sjr.path():
        return SourceResult("sjr", UNAVAILABLE,
                            message="Not checked: no SCImago (SJR) file configured. Set SJR_CSV_PATH.")
    label = f"SCImago Journal Rank {sjr.year()}" if sjr.year() else "SCImago Journal Rank"
    hit, matched_by = _lookup(sjr, issns), "ISSN"
    if not hit and not issns:
        hit = next((h for h in map(sjr.by_title, journal_titles or []) if h), None)
        matched_by = "exact journal title (no ISSN in any source)"
    if not hit:
        return SourceResult("sjr", NOT_FOUND, message=(
            "Journal ISSN not found in the SCImago file" if issns
            else "No ISSN available for this publication, and its title is not in the SCImago file"))
    quartile = hit["quartile"] or "N/A"
    return SourceResult("sjr", OK, data={"quartile": quartile, "sjr_title": hit["title"]},
                        message=f"{label}: best quartile {quartile} for '{hit['title']}', matched by {matched_by}")


def lookup_impact_factor(issns: list[str]) -> SourceResult:
    if not impact_factors.path():
        return SourceResult("jcr_file", UNAVAILABLE,
                            message="Not available: Journal Impact Factor is licensed by Clarivate (JCR). "
                                    "Provide an ISSN/impact_factor file via IMPACT_FACTOR_CSV_PATH.")
    if not issns:
        return SourceResult("jcr_file", NOT_FOUND, message="No ISSN available for this publication")
    hit = _lookup(impact_factors, issns)
    if not hit:
        return SourceResult("jcr_file", NOT_FOUND, message="Journal ISSN not found in the impact-factor file")
    return SourceResult("jcr_file", OK, data={"impact_factor": hit["impact_factor"], "impact_factor_year": hit["year"]})
