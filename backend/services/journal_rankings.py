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
import logging
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


class SJRLookup(_FileLookup):
    def load(self, path: Path) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in _sniff_reader(fh):
                quartile = (row.get("SJR Best Quartile") or "").strip()
                for issn in (row.get("Issn") or "").split(","):
                    key = _norm_issn(issn)
                    if key:
                        out[key] = {"quartile": quartile if quartile in ("Q1", "Q2", "Q3", "Q4") else None,
                                    "title": (row.get("Title") or "").strip()}
        return out


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


def lookup_quartile(issns: list[str]) -> SourceResult:
    if not sjr.path():
        return SourceResult("sjr", UNAVAILABLE,
                            message="Not checked: no SCImago (SJR) file configured. Set SJR_CSV_PATH.")
    if not issns:
        return SourceResult("sjr", NOT_FOUND, message="No ISSN available for this publication, so it cannot be ranked")
    hit = _lookup(sjr, issns)
    if not hit:
        return SourceResult("sjr", NOT_FOUND, message="Journal ISSN not found in the SCImago file")
    return SourceResult("sjr", OK, data={"quartile": hit["quartile"] or "N/A", "sjr_title": hit["title"]},
                        message="SCImago (SJR) best quartile")


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
