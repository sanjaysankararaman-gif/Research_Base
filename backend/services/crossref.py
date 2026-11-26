"""Crossref REST API client — authoritative bibliographic metadata.

Docs: https://api.crossref.org/swagger-ui/index.html
Endpoint used: GET https://api.crossref.org/works/{doi}
No key required. Setting CONTACT_EMAIL routes requests to the "polite pool".
"""
from __future__ import annotations

import html
import re
import unicodedata
from typing import Any
from urllib.parse import quote

from ..config import settings
from .base import OK, SourceClient, SourceResult

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_jats(text: str | None) -> str | None:
    """Crossref abstracts are JATS XML fragments; reduce them to plain text."""
    if not text:
        return None
    plain = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    plain = re.sub(r"^(Abstract|ABSTRACT)\s*[:.]?\s*", "", plain)
    return plain or None


def _date_from_parts(obj: dict[str, Any] | None) -> tuple[str | None, int | None]:
    parts = ((obj or {}).get("date-parts") or [[None]])[0]
    if not parts or parts[0] is None:
        return None, None
    year = int(parts[0])
    if len(parts) >= 3:
        return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}", year
    if len(parts) == 2:
        return f"{year:04d}-{int(parts[1]):02d}", year
    return f"{year:04d}", year


def _first(values: list[str] | None) -> str | None:
    return html.unescape(values[0]).strip() if values else None


# Crossref work types -> the two publication types the department records.
PUBLICATION_TYPES = {
    "journal-article": "Journal",
    "journal-issue": "Journal",
    "proceedings-article": "Conference",
    "proceedings": "Conference",
    "book-chapter": "Book chapter",
    "book-section": "Book chapter",
    "book-part": "Book chapter",
    "reference-entry": "Book chapter",
    "book": "Book",
    "edited-book": "Book",
    "monograph": "Book",
    "reference-book": "Book",
    "posted-content": "Preprint",
}
BOOK_TYPES = {"Book chapter", "Book"}

# Springer (LNCS, LNNS, CCIS, AISC...) and others register conference papers as book chapters
# in a book series. The book or series title gives them away.
CONFERENCE_WORDS = re.compile(r"\b(proceedings?|conference|congress|symposium|workshop|colloquium|summit)\b", re.I)
_PROCEEDINGS_OF = re.compile(r"^proceedings\s+of\s+(the\s+)?", re.I)


def _conference_from_title(title: str | None) -> str | None:
    """'Proceedings of International Conference on X' -> 'International Conference on X'."""
    if not title or not CONFERENCE_WORDS.search(title):
        return None
    return _PROCEEDINGS_OF.sub("", title).strip() or None


def _full_title(message: dict[str, Any]) -> str | None:
    """Crossref keeps subtitles separately ("Optuna" + "A Next-generation ...")."""
    title = _first(message.get("title"))
    subtitle = _first(message.get("subtitle"))
    if title and subtitle and subtitle.lower() not in title.lower():
        return f"{title}: {subtitle}"
    return title


def _event_dates(event: dict[str, Any]) -> str | None:
    start, _ = _date_from_parts(event.get("start"))
    end, _ = _date_from_parts(event.get("end"))
    if start and end and start != end:
        return f"{start} to {end}"
    return start or end


def parse_crossref(message: dict[str, Any]) -> dict[str, Any]:
    authors, authorships = [], []
    for a in message.get("author", []) or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name")
        if not name:
            continue
        authors.append(name)
        affs = a.get("affiliation") or []
        authorships.append({
            "name": name,
            "institutions": [html.unescape(x["name"]).strip() for x in affs if x.get("name")],
            "ror": [i.get("id") for x in affs for i in x.get("id") or []
                    if (i.get("id-type") or "").upper() == "ROR" and i.get("id")],
        })

    # "published" is Crossref's earliest of the print and online dates; the others are fallbacks.
    pub_date, pub_year = None, None
    for key in ("published", "published-online", "published-print", "issued"):
        pub_date, pub_year = _date_from_parts(message.get(key))
        if pub_date:
            break

    issn_by_type = {i.get("type"): i.get("value") for i in message.get("issn-type", []) or []}
    crossref_type = message.get("type")
    publication_type = PUBLICATION_TYPES.get(crossref_type, "Other" if crossref_type else None)
    event = message.get("event") or {}
    containers = [html.unescape(c).strip() for c in message.get("container-title") or [] if c and c.strip()]
    container = containers[0] if containers else None
    # A book chapter's containers are [series, book] or just [book].
    is_book_part = publication_type in BOOK_TYPES or (crossref_type == "other" and bool(message.get("ISBN")))
    book_title = containers[-1] if containers else None
    series = containers[0] if len(containers) > 1 else None
    if is_book_part and (event.get("name") or _conference_from_title(book_title) or _conference_from_title(series)):
        publication_type = "Conference"
    elif crossref_type == "other" and is_book_part:
        publication_type = "Book chapter"
    is_conference = publication_type == "Conference"
    # The venue of a conference paper or chapter: the proceedings / book, with its series.
    if is_book_part:
        venue = f"{book_title} ({series})" if series and series != book_title else book_title
    else:
        venue = container
    pages = message.get("page") or None
    article_number = message.get("article-number") or None
    relation = message.get("relation") or {}
    published = [r.get("id", "").lower() for r in relation.get("is-preprint-of", []) or []
                 if r.get("id-type") == "doi" and r.get("id")]
    resource_url = ((message.get("resource") or {}).get("primary") or {}).get("URL")
    isbn = message.get("ISBN") or None
    issn = message.get("ISSN") or None

    return {
        "doi": (message.get("DOI") or "").lower() or None,
        "title": _full_title(message),
        "authors": authors or None,
        "authorships": authorships if any(a["institutions"] for a in authorships) else None,
        "publication_date": pub_date,
        "publication_year": pub_year,
        "publication_type": publication_type,
        "journal": None if (is_conference or is_book_part) else container,
        "publisher": message.get("publisher"),
        "volume": message.get("volume"),
        "issue": message.get("issue"),
        "pages": pages,
        "article_number": article_number,
        "page_range_or_article_number": pages or (f"Article {article_number}" if article_number else None),
        "issn": issn,
        "issn_print": issn_by_type.get("print"),
        "issn_electronic": issn_by_type.get("electronic"),
        "isbn": isbn,
        "issn_eissn": ", ".join(x for x in (
            f"{issn_by_type['print']} (ISSN)" if issn_by_type.get("print") else None,
            f"{issn_by_type['electronic']} (E-ISSN)" if issn_by_type.get("electronic") else None,
        ) if x) or ("; ".join(issn) if issn else None),
        "article_type": crossref_type,
        "is_preprint": crossref_type == "posted-content",
        "published_doi": published[0] if published else None,
        "publication_url": resource_url or (f"https://doi.org/{message['DOI'].lower()}" if message.get("DOI") else None),
        # Conference-specific (Crossref "event" metadata, deposited by the publisher). For a
        # conference paper published as a book chapter without event data, the book title
        # names the conference when it says so ("Proceedings of ... Conference on ...").
        "conference_name": ((html.unescape(event["name"]) if event.get("name") else None)
                            or (_conference_from_title(book_title) if is_book_part else None)) if is_conference else None,
        "conference_acronym": event.get("acronym") if is_conference else None,
        "conference_dates": _event_dates(event) if is_conference else None,
        "conference_location": event.get("location") if is_conference else None,
        # Proceedings fields; for a book chapter they describe the book.
        "proceedings_title": venue if (is_conference or is_book_part) else None,
        "proceedings_publisher": message.get("publisher") if (is_conference or is_book_part) else None,
        "proceedings_isbn_issn": "; ".join([*(f"ISBN {i}" for i in isbn or []), *(f"ISSN {i}" for i in issn or [])]) or None
        if (is_conference or is_book_part) else None,
        "proceedings_volume_pages": ", ".join(
            x for x in (f"Vol. {message['volume']}" if message.get("volume") else None,
                        f"pp. {pages}" if pages else None) if x
        ) or None if (is_conference or is_book_part) else None,
        "book_series": series if is_book_part else None,
        "abstract": strip_jats(message.get("abstract")),
        "citation_count": message.get("is-referenced-by-count"),
        "reference_count": message.get("references-count", message.get("reference-count")),
        "subjects": message.get("subject") or None,
        "url": message.get("URL"),
        "license": _first([l.get("URL") for l in message.get("license", []) or [] if l.get("URL")]),
        "funders": [f.get("name") for f in message.get("funder", []) or [] if f.get("name")] or None,
    }


class CrossrefClient(SourceClient):
    name = "crossref"
    label = "Crossref"
    base_url = "https://api.crossref.org"

    def __init__(self, transport=None):
        super().__init__(transport)
        # The public pool allows 5 requests a second; the polite pool (CONTACT_EMAIL) allows 10.
        self.min_interval = max(settings.crossref_min_interval, 0.0 if settings.contact_email else 0.2)

    def default_params(self) -> dict[str, str]:
        return {"mailto": settings.contact_email} if settings.contact_email else {}

    async def fetch(self, doi: str) -> SourceResult:
        payload = await self.get_json(f"/works/{quote(doi, safe='/')}")
        message = payload.get("message") or {}
        return SourceResult(self.name, OK, data=parse_crossref(message), http_status=200)


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", html.unescape(text or ""))
    return re.sub(r"[^a-z0-9]", "", text.encode("ascii", "ignore").decode().lower())


def _family(name: str | None) -> str:
    return _norm((name or "").split()[-1]) if name else ""


async def find_published_by_title(client: "CrossrefClient", title: str, first_author: str,
                                  year: int | None) -> str | None:
    """Find the formally published version of a preprint in Crossref.

    Strict match only: identical title (ignoring case and punctuation), same
    first-author family name, published between one year before and three years
    after the preprint, and a journal article / proceedings paper / book chapter.
    Returns the DOI or None. Nothing approximate is accepted.
    """
    if not title or not first_author:
        return None
    payload = await client.get_json("/works", {
        "query.bibliographic": title,
        "query.author": first_author,
        "filter": "type:journal-article,type:proceedings-article,type:book-chapter",
        "rows": 5,
        "select": "DOI,title,author,issued",
    })
    want_title, want_family = _norm(title), _family(first_author)
    for item in (payload.get("message") or {}).get("items", []):
        authors = item.get("author") or []
        got_family = _norm(authors[0].get("family")) if authors else ""
        got_year = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
        if _norm(_first(item.get("title"))) != want_title:
            continue
        if not got_family or not (got_family == want_family or got_family.endswith(want_family)):
            continue
        if year and got_year and not (year - 1 <= got_year <= year + 3):
            continue
        return (item.get("DOI") or "").lower() or None
    return None
