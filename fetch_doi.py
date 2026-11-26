"""Fetch the essential fields for one or more DOIs from the command line.

    .venv/bin/python fetch_doi.py 10.1038/nature14539
    .venv/bin/python fetch_doi.py 10.1109/CVPR.2016.90 10.48550/arXiv.1706.03762 --json

Uses only the data-retrieval code (backend/services + backend/processing);
no database, server or Excel file is needed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from backend.processing.data_merger import flatten
from backend.processing.doi_validator import InvalidDOI, normalize_doi
from backend.processing.retrieval import retrieve
from backend.services import build_clients

FIELDS = [
    ("1. Common Fields", None, [
        (1, "Publication Title", "title"), (2, "Publication Type", "publication_type"),
        (3, "Author Names", "authors_text"), (4, "Publication Year", "publication_year"),
        (5, "Publisher", "publisher"), (6, "DOI", "doi"), (7, "Publication URL", "publication_url"),
        (8, "Indexing Database", "indexing")]),
    ("2. Journal-Specific Fields", ("Journal",), [
        (9, "Journal Name", "journal"), (10, "ISSN / E-ISSN", "issn"), (11, "Volume", "volume"),
        (12, "Issue", "issue"), (13, "Page Range / Article Number", "page_range"),
        (14, "Quartile", "quartile"), (15, "Impact Factor", "impact_factor")]),
    ("3. Conference / Book Fields", ("Conference", "Book chapter", "Book"), [
        (16, "Conference Name", "conference_name"), (17, "Conference Acronym", "conference_acronym"),
        (18, "Conference Dates", "conference_dates"), (19, "Conference Location", "conference_location"),
        (20, "Proceedings Title", "proceedings_title"), (21, "Proceedings Publisher", "proceedings_publisher"),
        (22, "Proceedings ISBN / ISSN", "proceedings_isbn_issn"),
        (23, "Proceedings Volume / Page Range", "proceedings_volume_pages")]),
    ("4. Date and Institution", None, [
        (24, "Publication Date", "publication_date"), (25, "Home Institution", "home_institution"),
        (26, "Home Faculty (Faculty ID)", "home_faculty_text"),
        (27, "Author Affiliations", "author_affiliations_text")]),
]


def print_fields(fields: dict) -> None:
    ptype = fields.get("publication_type")
    for title, applies, rows in FIELDS:
        skip = applies and ptype in ("Journal", "Conference", "Book chapter", "Book") and ptype not in applies
        print(f"\n{title}" + (" (not applicable)" if skip else ""))
        for number, label, key in rows:
            value = "-" if skip else (fields.get(key) if fields.get(key) not in (None, "", []) else "Not available")
            if key in ("home_institution", "home_faculty_text") and value == "Not available" \
                    and fields.get("institutions_checked"):
                value = "No"
            value = str(value).replace("\n", "\n" + " " * 41)   # one affiliation per line
            print(f"  {number:>2}. {label:<34} {value}")
    if fields.get("version_note"):
        print(f"\nNote: {fields['version_note']}")


async def main(dois: list[str], as_json: bool) -> int:
    clients = build_clients()
    exit_code = 0
    try:
        for raw in dois:
            try:
                doi = normalize_doi(raw)
            except InvalidDOI as exc:
                print(f"{raw}: {exc}", file=sys.stderr)
                exit_code = 1
                continue
            record = await retrieve(doi, clients)
            if record["status"] in ("not_found", "failed"):
                print(f"{doi}: no data ({record['status']})", file=sys.stderr)
                exit_code = 1
                continue
            fields = flatten(record)["fields"]
            if as_json:
                print(json.dumps(fields, ensure_ascii=False, indent=2))
            else:
                print("=" * 80 + f"\n{doi}  [{record['status']}]")
                print_fields(fields)
    finally:
        for client in clients.values():
            await client.close()
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dois", nargs="+", help="one or more DOIs (any common format)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dois, args.json)))
