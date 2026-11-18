"""Excel storage: every paper in the library as one row of an .xlsx file.

The workbook is rebuilt from the database after every add or delete, so it
always matches the library exactly (no duplicate rows, deleted papers
disappear). Columns follow "Essential Fields for Research Publication
Database" in order.

Cell conventions:
    "Not available"   the field applies to this paper, but no source had it
    "-"               the field does not apply (e.g. conference fields for a journal paper)

File location: EXCEL_PATH in .env (default data/research_papers.xlsx).
Download it from the app ("Download Excel") or GET /api/excel.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import database as db
from ..config import settings
from ..processing.data_merger import flatten

log = logging.getLogger("extractor.excel")
_lock = threading.Lock()

NOT_AVAILABLE = "Not available"
NOT_APPLICABLE = "-"

# (column header, key in flatten()["fields"], applies-to: None = all, "Journal", "Conference")
COLUMNS: list[tuple[str, str, str | None]] = [
    # 1. Common fields
    ("Publication Title", "title", None),
    ("Publication Type", "publication_type", None),
    ("Author Names", "authors_text", None),
    ("Publication Year", "publication_year", None),
    ("Publisher", "publisher", None),
    ("DOI", "doi", None),
    ("Publication URL", "publication_url", None),
    ("Indexing Database", "indexing", None),
    # 2. Journal-specific
    ("Journal Name", "journal", "Journal"),
    ("ISSN / E-ISSN", "issn", "Journal"),
    ("Volume", "volume", "Journal"),
    ("Issue", "issue", "Journal"),
    ("Page Range / Article Number", "page_range", "Journal"),
    ("Quartile", "quartile", "Journal"),
    ("Impact Factor", "impact_factor", "Journal"),
    # 3. Conference-specific
    ("Conference Name", "conference_name", "Conference"),
    ("Conference Acronym", "conference_acronym", "Conference"),
    ("Conference Dates", "conference_dates", "Conference"),
    ("Conference Location", "conference_location", "Conference"),
    ("Proceedings Title", "proceedings_title", "Conference"),
    ("Proceedings Publisher", "proceedings_publisher", "Conference"),
    ("Proceedings ISBN / ISSN", "proceedings_isbn_issn", "Conference"),
    ("Proceedings Volume / Page Range", "proceedings_volume_pages", "Conference"),
    # Extra
    ("Published Version DOI", "published_doi", None),
    ("PDF Link", "pdf_url", None),
    ("Notes", "version_note", None),
]

WIDTHS = {"Publication Title": 50, "Author Names": 40, "Publication URL": 40, "Indexing Database": 36,
          "Journal Name": 30, "Conference Name": 40, "Proceedings Title": 45, "PDF Link": 40,
          "Notes": 60, "Published Version DOI": 26}


def excel_path() -> Path:
    return settings.excel_path


def _cell(fields: dict[str, Any], key: str, applies_to: str | None) -> Any:
    ptype = fields.get("publication_type")
    if applies_to and ptype in ("Journal", "Conference") and ptype != applies_to:
        return NOT_APPLICABLE
    value = fields.get(key)
    if value is None or value == "" or value == []:
        return "" if key in ("version_note", "published_doi") else NOT_AVAILABLE
    return value


def build_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for n, rec in enumerate(records, start=1):
        f = flatten(rec)
        fields = f["fields"]
        rows.append([n, *[_cell(fields, key, applies) for _, key, applies in COLUMNS],
                     (f.get("updated_at") or f.get("retrieved_at") or "").replace("T", " ").replace("+00:00", " UTC")])
    return rows


def write_workbook(path: Path | None = None) -> Path:
    """Rebuild the Excel file from every paper in the library."""
    path = Path(path or excel_path())
    records = db.all_papers()
    wb = Workbook()
    ws = wb.active
    ws.title = "Publications"
    header = ["S. No.", *[c[0] for c in COLUMNS], "Retrieved At"]
    ws.append(header)
    for row in build_rows(records):
        ws.append(row)

    bold, fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E8C")
    for cell in ws[1]:
        cell.font, cell.fill = bold, fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    for i, name in enumerate(header, start=1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(name, 7 if name == "S. No." else 20)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(cell.value, str) and cell.value.startswith("http"):
                cell.hyperlink = cell.value
                cell.font = Font(color="1F4E8C", underline="single")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        # Write to a temp file, then swap it in, so the file is never half-written.
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
        os.close(fd)
        try:
            wb.save(tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    log.info("Excel updated: %s (%d papers)", path, len(records))
    return path


def safe_update() -> bool:
    """Rebuild the workbook; never let an Excel problem break a lookup."""
    try:
        write_workbook()
        return True
    except Exception:
        log.exception("Could not update the Excel file")
        return False
