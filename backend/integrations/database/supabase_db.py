"""Supabase (PostgreSQL) storage for publications and home-institution faculty.

Separate from backend/database.py (the library store).

Set SUPABASE_DB_URL in the environment:
  - Locally: in .env
  - On Vercel: in Settings -> Environment Variables

Use the Transaction pooler connection string (port 6543).
Create the tables by running supabase_schema.sql once in the Supabase SQL editor.

Example:
    pub = Publication(doi="10.1038/nature14539", title="Deep learning",
                      publication_type="Journal", authors=["Yann LeCun", "Yoshua Bengio"],
                      publication_year=2015, publication_date="2015-05-28",
                      journal={"journal_name": "Nature", "volume": "521", "issue": "7553",
                               "page_range": "436-444", "quartile": "Q1"})
    save_publication(pub)
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from dotenv import load_dotenv

load_dotenv()  # reads .env when running locally; harmless on Vercel

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")  # set in .env or Vercel, never in source code

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
    journal: dict[str, Any] | None = None       # keys: JOURNAL_COLUMNS
    conference: dict[str, Any] | None = None    # keys: CONFERENCE_COLUMNS


@dataclass(frozen=True)
class FacultyMember:
    faculty_uid: str
    full_name: str
    given: tuple[str, ...]      # lower-case given-name parts, e.g. ("sreehari",) or ("k", "s")
    family: str                 # lower-case surname; a single letter if only the initial is known
    variants: frozenset[str]    # normalized alternative spellings, matched exactly


# --------------------------------------------------------------------------- connection

def connect() -> psycopg.Connection:
    """New connection. Use as `with connect() as conn:` (commits on success, closes on exit)."""
    if not SUPABASE_DB_URL:
        raise RuntimeError("Set SUPABASE_DB_URL (in .env locally, or in Vercel's environment variables).")
    # Supabase's pooler does not support server-side prepared statements.
    return psycopg.connect(SUPABASE_DB_URL, connect_timeout=15, prepare_threshold=None)


def check_connection() -> str:
    """Return the server version. Raises if the database cannot be reached."""
    with connect() as conn:
        return conn.execute("select version()").fetchone()[0]


# --------------------------------------------------------------------------- name matching

_WORD = re.compile(r"[A-Za-z]+")
_NON_LETTERS = re.compile(r"[^a-z ]")


def _words(text: str | None) -> list[str]:
    """Letter-only words, accents removed, original case kept (case is used to spot initials)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _WORD.findall(text)


def normalize_name(name: str | None) -> str:
    """'Yann  LeCun', 'LECUN, Yann' and 'Yann Le-Cun.' -> lower-case letters and spaces.

    Used for exact variant matching only. Order matters here; the structural
    matcher below handles word order and initials.
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = _NON_LETTERS.sub(" ", text.replace("-", " "))
    return " ".join(text.split())


def _signature(author: str) -> tuple[set[str], set[str]]:
    """(full_words, initials) found in an author string.

    full_words: lower-case words of two or more letters, e.g. {"gayathri", "sreehari"}.
    initials:   single letters, including the letters of short all-capital groups,
                so 'Gayathri KS' gives initials {"k", "s"}.
    """
    full: set[str] = set()
    initials: set[str] = set()
    for word in _words(author):
        if len(word) == 1:
            initials.add(word.lower())
        elif word.isupper() and len(word) <= 4:
            full.add(word.lower())
            initials.update(ch.lower() for ch in word)
        else:
            full.add(word.lower())
    return full, initials


def _matches(author: str, member: FacultyMember) -> bool:
    """True when the author string identifies this professor.

    Rules (order-insensitive):
      - The surname must appear in full, or appear as an initial together with
        a full given name (e.g. 'Durgadevi V.').
      - Every given-name part must appear: a full name, its initial, or the
        initial of a given name that is written out in full.
      - At least one full word must match, so a string of initials alone never matches.
    """
    full, initials = _signature(author)

    def given_ok(part: str) -> bool:
        if len(part) > 1:
            return part in full or part[0] in initials
        return part in initials

    given_full = any(len(g) > 1 and g in full for g in member.given)
    family_full = len(member.family) > 1 and member.family in full
    family_ok = family_full or (member.family[0] in initials and given_full)
    return family_ok and all(given_ok(g) for g in member.given)


def load_faculty(conn) -> list[FacultyMember]:
    """Every home-institution professor, with normalized name parts and variants."""
    variants: dict[str, set[str]] = {}
    for uid, variant in conn.execute("select faculty_uid::text, name_variant from faculty_name_variants"):
        variants.setdefault(uid, set()).add(normalize_name(variant))

    members = []
    for uid, full_name, given_names, family_name in conn.execute(
        "select faculty_uid::text, full_name, given_names, family_name from faculty"
    ):
        given = tuple(w.lower() for w in _words(given_names))
        family_words = _words(family_name)
        if not given or len(family_words) != 1:
            raise ValueError(f"{full_name}: set given_names and a single-word family_name in faculty")
        members.append(FacultyMember(uid, full_name, given, family_words[0].lower(),
                                     frozenset(variants.get(uid, ()))))
    return members


def match_faculty(authors: list[str], members: list[FacultyMember]) -> list[tuple[str, str, int]]:
    """Return (faculty_uid, author_as_written, position) for each home-institution author.

    An author that matches more than one professor is not linked (and a warning is
    logged). Each professor is linked once, at their first position in the author list.
    """
    matches, seen = [], set()
    for position, author in enumerate(authors, start=1):
        key = normalize_name(author)
        hits = [m for m in members if _matches(author, m) or key in m.variants]
        if not hits:
            continue
        if len(hits) > 1:
            log.warning("'%s' matches several faculty (%s); not linked",
                        author, ", ".join(m.full_name for m in hits))
            continue
        member = hits[0]
        if member.faculty_uid not in seen:
            seen.add(member.faculty_uid)
            matches.append((member.faculty_uid, author, position))
    return matches


# --------------------------------------------------------------------------- writes

PUBLICATION_SQL = """
insert into publications (doi, publication_title, publication_type, authors, publication_year,
                          publication_date, publisher, publication_url, indexing_database)
values (%(doi)s, %(title)s, %(publication_type)s, %(authors)s, %(year)s,
        %(publication_date)s, %(publisher)s, %(publication_url)s, %(indexing)s)
on conflict (doi) do update set
    publication_title = excluded.publication_title,
    publication_type  = excluded.publication_type,
    authors           = excluded.authors,
    publication_year  = excluded.publication_year,
    publication_date  = excluded.publication_date,
    publisher         = excluded.publisher,
    publication_url   = excluded.publication_url,
    indexing_database = excluded.indexing_database
returning id
"""

LINK_SQL = """
insert into publication_faculty (publication_id, faculty_uid, matched_author, author_position)
values (%s, %s::uuid, %s, %s)
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


def save_publication(pub: Publication) -> dict[str, Any]:
    """Store one publication, its journal or conference details, and its home-institution
    faculty links. Everything happens in one transaction: all of it or none of it.

    Returns {"publication_id", "doi", "home_faculty"}, where home_faculty lists the
    full names of the matched professors. Raises UnsupportedPublicationType for other types.
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
    }

    with connect() as conn:
        members = load_faculty(conn)
        matches = match_faculty(pub.authors or [], members)

        with conn.cursor() as cur:
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
            if matches:
                cur.executemany(LINK_SQL, [(publication_id, uid, author, pos)
                                           for uid, author, pos in matches])

    names_by_uid = {m.faculty_uid: m.full_name for m in members}
    home_faculty = [names_by_uid[uid] for uid, _, _ in matches]
    log.info("Stored %s (id %s); home faculty: %s", doi, publication_id, home_faculty or "none")
    return {"publication_id": publication_id, "doi": doi, "home_faculty": home_faculty}