"""Supabase (PostgreSQL) copy of the library as normalized tables, for reports.

Separate from backend/database.py (the library store the app reads from). Written
after every successful lookup when SUPABASE_DB_URL is set:
  - Locally: in .env
  - On Vercel: in Settings -> Environment Variables

Use the Transaction pooler connection string (port 6543).
Create the tables by running supabase_schema.sql once in the Supabase SQL editor.

The faculty table mirrors reference/faculty.csv (the one faculty list, with each
teacher's fixed faculty_id); it is updated on every write. Which teachers wrote a
paper is decided by processing/home_institution.py, the same rule the app and the
Excel file use.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg

from ...config import settings
from ...processing.home_institution import FacultyMember, load_faculty

log = logging.getLogger("extractor.supabase")

PUBLICATION_TYPES = ("Journal", "Conference")
JOURNAL_COLUMNS = ("journal_name", "issn", "e_issn", "volume", "issue",
                   "page_range", "quartile", "impact_factor")
CONFERENCE_COLUMNS = ("conference_name", "conference_acronym", "conference_start", "conference_end",
                      "conference_location", "proceedings_title", "proceedings_publisher",
                      "proceedings_isbn", "proceedings_issn", "proceedings_volume", "proceedings_pages")


class UnsupportedPublicationType(ValueError):
    """Raised for types the schema does not store (Preprint, Book chapter, ...)."""


@dataclass
class Publication:
    doi: str
    title: str
    publication_type: str                       # "Journal" or "Conference"
    authors: list[str]
    publication_year: int | None = None
    publication_date: str | date | None = None  # full date, "YYYY-MM-DD"
    publisher: str | None = None
    publication_url: str | None = None
    indexing_database: list[str] | None = None
    home_institution: str | None = None         # settings.home_institution_name, or None
    journal: dict[str, Any] | None = None       # keys: JOURNAL_COLUMNS
    conference: dict[str, Any] | None = None    # keys: CONFERENCE_COLUMNS
    # Home-institution teachers: {"faculty_id", "author", "position", "evidence"}
    faculty: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- connection

def connect() -> psycopg.Connection:
    """New connection. Use as `with connect() as conn:` (commits on success, closes on exit)."""
    if not settings.supabase_db_url:
        raise RuntimeError("Set SUPABASE_DB_URL (in .env locally, or in Vercel's environment variables).")
    # Supabase's pooler does not support server-side prepared statements.
    return psycopg.connect(settings.supabase_db_url, connect_timeout=10, prepare_threshold=None)


def check_connection() -> str:
    """Return the server version. Raises if the database cannot be reached."""
    with connect() as conn:
        return conn.execute("select version()").fetchone()[0]


# --------------------------------------------------------------------------- writes

PUBLICATION_SQL = """
insert into publications (doi, publication_title, publication_type, authors, publication_year,
                          publication_date, publisher, publication_url, indexing_database,
                          home_institution)
values (%(doi)s, %(title)s, %(publication_type)s, %(authors)s, %(year)s,
        %(publication_date)s, %(publisher)s, %(publication_url)s, %(indexing)s,
        %(home_institution)s)
on conflict (doi) do update set
    publication_title = excluded.publication_title,
    publication_type  = excluded.publication_type,
    authors           = excluded.authors,
    publication_year  = excluded.publication_year,
    publication_date  = excluded.publication_date,
    publisher         = excluded.publisher,
    publication_url   = excluded.publication_url,
    indexing_database = excluded.indexing_database,
    home_institution  = excluded.home_institution
returning id
"""

FACULTY_SQL = """
insert into faculty (faculty_id, full_name, given_names, family_name, name_variants)
values (%s, %s, %s, %s, %s)
on conflict (faculty_id) do update set
    full_name     = excluded.full_name,
    given_names   = excluded.given_names,
    family_name   = excluded.family_name,
    name_variants = excluded.name_variants
"""

LINK_SQL = """
insert into publication_faculty (publication_id, faculty_id, matched_author, author_position, evidence)
values (%s, %s, %s, %s, %s)
"""


def _to_date(value: str | date | None) -> date | None:
    """Full dates only. A year or year-month is stored as NULL rather than guessed."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _upsert_details(cur, table: str, columns: tuple[str, ...], publication_id: int,
                    values: dict[str, Any]) -> None:
    # Table and column names come from the constants above, never from user input.
    names = ", ".join(("publication_id", *columns))
    placeholders = ", ".join(["%s"] * (len(columns) + 1))
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns)
    cur.execute(
        f"insert into {table} ({names}) values ({placeholders}) "
        f"on conflict (publication_id) do update set {updates}",
        [publication_id, *(values.get(c) for c in columns)],
    )


def _faculty_rows(members: list[FacultyMember]) -> list[tuple]:
    return [(m.faculty_id, m.full_name, " ".join(m.given), m.family, sorted(m.variants) or None)
            for m in members]


def save_publication(pub: Publication) -> dict[str, Any]:
    """Store one publication, its journal or conference details, and its home-institution
    faculty links. Everything happens in one transaction: all of it or none of it.

    Returns {"publication_id", "doi", "home_faculty"} (home_faculty: faculty_ids).
    Raises UnsupportedPublicationType for other types.
    """
    if pub.publication_type not in PUBLICATION_TYPES:
        raise UnsupportedPublicationType(
            f"{pub.doi}: '{pub.publication_type}' is not stored (only Journal and Conference)")

    doi = pub.doi.strip().lower()
    params = {
        "doi": doi,
        "title": pub.title,
        "publication_type": pub.publication_type,
        "authors": list(pub.authors or []),
        "year": pub.publication_year,
        "publication_date": _to_date(pub.publication_date),
        "publisher": pub.publisher,
        "publication_url": pub.publication_url,
        "indexing": pub.indexing_database or None,
        "home_institution": pub.home_institution,
    }

    with connect() as conn:
        with conn.cursor() as cur:
            # Keep the faculty table in step with reference/faculty.csv.
            cur.executemany(FACULTY_SQL, _faculty_rows(load_faculty()))
            cur.execute(PUBLICATION_SQL, params)
            publication_id = cur.fetchone()[0]

            if pub.publication_type == "Journal":
                _upsert_details(cur, "journal_details", JOURNAL_COLUMNS,
                                publication_id, pub.journal or {})
                cur.execute("delete from conference_details where publication_id = %s",
                            (publication_id,))
            else:
                conference = dict(pub.conference or {})
                conference["conference_start"] = _to_date(conference.get("conference_start"))
                conference["conference_end"] = _to_date(conference.get("conference_end"))
                _upsert_details(cur, "conference_details", CONFERENCE_COLUMNS,
                                publication_id, conference)
                cur.execute("delete from journal_details where publication_id = %s",
                            (publication_id,))

            # Replace the faculty links so a re-fetch with a changed author list stays correct.
            cur.execute("delete from publication_faculty where publication_id = %s", (publication_id,))
            if pub.faculty:
                cur.executemany(LINK_SQL, [(publication_id, f["faculty_id"], f["author"], f["position"],
                                            f["evidence"]) for f in pub.faculty])

    home_faculty = [f["faculty_id"] for f in pub.faculty]
    log.info("Stored %s (id %s); home faculty: %s", doi, publication_id, home_faculty or "none")
    return {"publication_id": publication_id, "doi": doi, "home_faculty": home_faculty}
