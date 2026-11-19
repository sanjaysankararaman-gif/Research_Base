"""Google Sheets integration — PLACEHOLDER (not implemented yet).

Google Sheets will be the department's final destination for research data.
OAuth / Sheets API access is intentionally not implemented yet; credentials
will be configured later. This module defines the boundary so it can be
completed without touching DOI extraction.

What already works (and is tested)
----------------------------------
`build_header()` and `record_to_row()` turn standardized records (the exact
objects stored in SQLite and returned by GET /papers/{doi}) into flat
spreadsheet rows. Columns come from `processing/metric_mapper.py`, so adding a
metric there automatically adds a column here.

* "prefer" metrics -> one column with the chosen value, plus a
  "<column> Source" column.
* "per_source" metrics -> one column per source, e.g.
  "Citation Count (OpenAlex)", "Citation Count (Crossref)". Values are never
  combined.
* Unavailable values are written as an empty cell; a real 0 is written as 0.
* "Retrieved At" and "Status" columns are included for provenance.

What remains (when credentials are available)
---------------------------------------------
1. `pip install google-api-python-client google-auth google-auth-oauthlib`
2. Put the OAuth client / service-account JSON outside the repo and point
   GOOGLE_SHEETS_CREDENTIALS_FILE and GOOGLE_SHEETS_SPREADSHEET_ID at it in
   .env (both names are already reserved in this module; .gitignore already
   excludes credentials*.json / client_secret*.json / token*.json).
3. Implement `GoogleSheetsSync.append_records()` with
   `spreadsheets().values().append(..., valueInputOption="RAW")`, writing the
   header first if the sheet is empty. Use DOI as the unique key if you need
   upserts (read column A, update matching rows, append the rest).
4. Optionally add `POST /sync/google-sheets` in main.py that loads records
   with `database.list_papers()` and calls `append_records()`.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..processing.metric_mapper import PER_SOURCE, requested_metrics
from ..services import SOURCE_LABELS

CREDENTIALS_ENV = "GOOGLE_SHEETS_CREDENTIALS_FILE"
SPREADSHEET_ENV = "GOOGLE_SHEETS_SPREADSHEET_ID"

BASE_COLUMNS = ["DOI", "DOI URL", "Status", "Retrieved At"]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list) and all(isinstance(x, (str, int, float)) for x in value):
        return "; ".join(str(x) for x in value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_header() -> list[str]:
    header = list(BASE_COLUMNS)
    for m in requested_metrics():
        if m.merge == PER_SOURCE:
            header += [f"{m.column_name} ({SOURCE_LABELS[s]})" for s in m.sources]
        else:
            header += [m.column_name, f"{m.column_name} Source"]
    return header


def record_to_row(record: dict[str, Any]) -> list[Any]:
    row: list[Any] = [record["doi"], record.get("doi_url", ""), record["status"], record.get("retrieved_at", "")]
    metrics = record.get("metrics", {})
    for m in requested_metrics():
        data = metrics.get(m.key, {})
        values = {v["source"]: v for v in data.get("values", [])}
        if m.merge == PER_SOURCE:
            for s in m.sources:
                v = values.get(s)
                row.append(_cell(v["value"]) if v and v["available"] else "")
        else:
            pref = data.get("preferred")
            row.append(_cell(pref["value"]) if pref else "")
            row.append(SOURCE_LABELS.get(pref["source"], pref["source"]) if pref else "")
    return row


def records_to_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    return [build_header()] + [record_to_row(r) for r in records]


class GoogleSheetsNotConfigured(RuntimeError):
    pass


class GoogleSheetsSync:
    """Future sync client. Instantiating works; syncing raises until implemented."""

    def __init__(self) -> None:
        self.credentials_file = os.getenv(CREDENTIALS_ENV, "")
        self.spreadsheet_id = os.getenv(SPREADSHEET_ENV, "")

    @property
    def configured(self) -> bool:
        return bool(self.credentials_file and self.spreadsheet_id)

    def append_records(self, records: list[dict[str, Any]], sheet_name: str = "Papers") -> int:
        raise GoogleSheetsNotConfigured(
            "Google Sheets sync is not implemented yet. Configure OAuth credentials and implement "
            "GoogleSheetsSync.append_records() — see the module docstring."
        )
