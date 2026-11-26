"""Home institution and home faculty for a publication.

Two questions are answered for every record:

1. Which institutions did the paper come from?  The author affiliations reported by
   OpenAlex (normalized institution names with ROR IDs) and Crossref (the affiliation
   text the publisher deposited).
2. Did it come from the home institution (settings.home_institution_name), and which
   home-institution teachers wrote it?  Each teacher has a fixed faculty_id in
   reference/faculty.csv.

An author is from the home institution when one of their affiliations carries a home
ROR ID, or contains every word of one of the home aliases (settings).

A teacher on the faculty list is linked to the paper when an author's name matches
theirs (rules in `name_matches`) and the sources do not place that author at another
institution. Common names are shared by many people ("E. Suganya" is three different
researchers in OpenAlex), so a name alone is accepted only when no source lists the
author's affiliation; the result then says "name only".

Nothing here touches the network or a database: everything is computed from the
stored record, so a change to the faculty list applies to every paper at once.
"""
from __future__ import annotations

import csv
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import settings

log = logging.getLogger("extractor.home")

# Sources whose author lists carry affiliations, in display preference order.
AFFILIATION_SOURCES = ("openalex", "crossref")


@dataclass(frozen=True)
class FacultyMember:
    faculty_id: str
    full_name: str
    given: tuple[str, ...]      # lower-case given-name parts, e.g. ("sreehari",) or ("k", "s")
    family: str                 # lower-case surname; a single letter if only the initial is known
    variants: frozenset[str]    # normalized alternative spellings, matched exactly


# --------------------------------------------------------------------------- text helpers

_WORD = re.compile(r"[A-Za-z]+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def _ascii(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _words(text: str | None) -> list[str]:
    """Letter-only words, accents removed, original case kept (case is used to spot initials)."""
    return _WORD.findall(_ascii(text))


def normalize_name(name: str | None) -> str:
    """'Yann  LeCun', 'Yann Le-Cun.' -> 'yann lecun' style text: lower-case letters and spaces."""
    text = _ascii(name).lower().replace("-", " ")
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def _plain(text: str | None) -> set[str]:
    """Lower-case words of an affiliation, for alias matching."""
    return set(_NON_ALNUM.sub(" ", _ascii(text).lower()).split())


def _ror_id(value: str | None) -> str:
    """'https://ror.org/039rg7508' -> '039rg7508'."""
    return (value or "").rstrip("/").rsplit("/", 1)[-1].lower()


# --------------------------------------------------------------------------- name matching

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


def name_matches(author: str, member: FacultyMember) -> bool:
    """True when the author string names this teacher (word order does not matter).

    - The surname must appear in full, or as an initial together with a full given
      name (e.g. 'Durgadevi V.').
    - Every given-name part must appear: in full, as its initial, or (for a written-out
      given name) as its first letter.
    - A string of initials alone never matches.
    - A spelling listed in name_variants always matches.
    """
    if normalize_name(author) in member.variants:
        return True
    full, initials = _signature(author)

    def given_ok(part: str) -> bool:
        if len(part) > 1:
            return part in full or part[0] in initials
        return part in initials

    given_full = any(len(g) > 1 and g in full for g in member.given)
    family_full = len(member.family) > 1 and member.family in full
    family_ok = family_full or (member.family[0] in initials and given_full)
    return family_ok and all(given_ok(g) for g in member.given)


# --------------------------------------------------------------------------- faculty list

FACULTY_COLUMNS = ("faculty_id", "full_name", "given_names", "family_name", "name_variants")
_FACULTY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{1,30}$")


def parse_faculty(rows: list[dict[str, str]]) -> list[FacultyMember]:
    """Validate faculty rows. Raises ValueError naming the bad row, so a typo cannot
    silently link papers to the wrong teacher."""
    members, ids, names = [], set(), set()
    for n, row in enumerate(rows, start=2):           # row 1 is the header
        row = {k: (row.get(k) or "").strip() for k in FACULTY_COLUMNS}
        fid, full_name = row["faculty_id"], row["full_name"]
        if not fid and not full_name:
            continue
        given = tuple(w.lower() for w in _words(row["given_names"]))
        family = _words(row["family_name"])
        if not _FACULTY_ID.match(fid):
            raise ValueError(f"faculty.csv row {n}: faculty_id '{fid}' is missing or not a simple code")
        if fid in ids:
            raise ValueError(f"faculty.csv row {n}: faculty_id {fid} is used twice")
        if not full_name or full_name.lower() in names:
            raise ValueError(f"faculty.csv row {n}: full_name is missing or used twice")
        if not given or len(family) != 1:
            raise ValueError(f"faculty.csv row {n} ({full_name}): set given_names and a one-word family_name")
        ids.add(fid)
        names.add(full_name.lower())
        variants = frozenset(normalize_name(v) for v in row["name_variants"].split(";") if v.strip())
        members.append(FacultyMember(fid, full_name, given, family[0].lower(), variants))
    return members


class _FacultyFile:
    """reference/faculty.csv, read once and re-read when the file changes.

    A mistake in the file is logged and reported by /api/health (`error`); the last good
    list stays in use, so one typo cannot break every page.
    """

    def __init__(self):
        self._key = None
        self._members: list[FacultyMember] = []
        self.error: str | None = None

    def members(self) -> list[FacultyMember]:
        path: Path = settings.faculty_csv_path
        if not path.is_file():
            if self._key != "missing":
                log.warning("Faculty list %s not found; no teacher will be linked to papers", path)
                self._key, self._members, self.error = "missing", [], f"{path.name} not found"
            return []
        key = (str(path), path.stat().st_mtime)
        if key != self._key:
            self._key = key
            try:
                with path.open(encoding="utf-8-sig", newline="") as fh:
                    self._members = parse_faculty(list(csv.DictReader(fh)))
                self.error = None
                log.info("Loaded %d faculty from %s", len(self._members), path.name)
            except (ValueError, OSError, csv.Error) as exc:
                self.error = str(exc)
                log.error("Faculty list not loaded (%s); keeping the previous list", exc)
        return self._members


faculty_file = _FacultyFile()


def load_faculty() -> list[FacultyMember]:
    return faculty_file.members()


# --------------------------------------------------------------------------- home institution

def is_home_affiliation(names: list[str] | None, rors: list[str] | None) -> bool:
    home_rors = {_ror_id(r) for r in settings.home_institution_rors}
    if any(_ror_id(r) in home_rors for r in rors or []):
        return True
    aliases = [_plain(a) for a in settings.home_institution_aliases]
    return any(alias and alias <= _plain(name) for name in names or [] for alias in aliases)


def _author_names(entry: dict[str, Any]) -> list[str]:
    return [n for n in (entry.get("name"), entry.get("raw_name")) if n]


def _affiliation_names(entry: dict[str, Any]) -> list[str]:
    return [*(entry.get("institutions") or []), *(entry.get("raw_affiliations") or [])]


def home_institution(authorships: dict[str, list[dict[str, Any]]], authors: list[str],
                     members: list[FacultyMember] | None = None) -> dict[str, Any]:
    """Home institution summary for one paper.

    authorships: {source: [{"name", "raw_name"?, "institutions", "ror", "raw_affiliations"?}]},
                 each list in published author order.
    authors:     the author list shown for the paper (field 3), used for positions.

    Returns {
      "home_institution": the configured name, or None when no author is from it,
      "home_authors":     author names placed at the home institution by their affiliation,
      "faculty":          [{"faculty_id", "name", "author", "position", "evidence"}],
      "affiliations":     [{"author", "institutions", "is_home"}] in author order,
      "affiliation_source": which source the affiliations come from, or None,
    }
    """
    members = load_faculty() if members is None else members

    # Affiliations to display: the first source that has any.
    display_source, display = None, []
    for source in AFFILIATION_SOURCES:
        entries = authorships.get(source) or []
        if any(e.get("institutions") or e.get("raw_affiliations") for e in entries):
            display_source, display = source, entries
            break
    affiliations = [{"author": e.get("name"),
                     "institutions": list(e.get("institutions") or e.get("raw_affiliations") or []),
                     "is_home": is_home_affiliation(_affiliation_names(e), e.get("ror"))}
                    for e in display]

    # Authors placed at the home institution, from the first source that places any there
    # (sources spell names differently, so lists are not combined).
    home_authors: list[str] = []
    for source in AFFILIATION_SOURCES:
        home_authors = [e["name"] for e in authorships.get(source) or []
                        if e.get("name") and is_home_affiliation(_affiliation_names(e), e.get("ror"))]
        if home_authors:
            break

    faculty = []
    for m in members:
        occurrences = [e for source in AFFILIATION_SOURCES for e in authorships.get(source) or []
                       if any(name_matches(n, m) for n in _author_names(e))]
        if not occurrences:
            # No source with affiliations lists them; fall back to the displayed author list.
            occurrences = [{"name": a} for a in authors if name_matches(a, m)]
        if not occurrences:
            continue
        at_home = [e for e in occurrences if is_home_affiliation(_affiliation_names(e), e.get("ror"))]
        elsewhere = [e for e in occurrences if _affiliation_names(e) and e not in at_home]
        if at_home:
            evidence = "name and affiliation"
        elif elsewhere:
            log.debug("%s matches %s by name but is affiliated elsewhere; not linked",
                     occurrences[0].get("name"), m.full_name)
            continue
        else:
            evidence = "name only (no affiliation in the sources)"
        position = next((i for i, a in enumerate(authors, start=1) if name_matches(a, m)), None)
        faculty.append({"faculty_id": m.faculty_id, "name": m.full_name,
                        "author": (authors[position - 1] if position else occurrences[0].get("name")),
                        "position": position, "evidence": evidence})
    # Ambiguous: one author string naming two teachers is not trustworthy.
    by_author: dict[str, int] = {}
    for f in faculty:
        by_author[f["author"]] = by_author.get(f["author"], 0) + 1
    faculty = [f for f in faculty if by_author[f["author"]] == 1]
    faculty.sort(key=lambda f: (f["position"] or 10_000, f["faculty_id"]))

    is_home = bool(home_authors or faculty)
    return {
        "home_institution": settings.home_institution_name if is_home else None,
        "home_authors": home_authors,
        "faculty": faculty,
        "affiliations": affiliations,
        "affiliation_source": display_source,
    }

