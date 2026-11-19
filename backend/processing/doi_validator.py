"""DOI validation, normalization and de-duplication.

DOIs are case-insensitive (ISO 26324), so the canonical form used everywhere
in this application is lower-case without any resolver prefix:

    "https://doi.org/10.1038/NATURE14539" -> "10.1038/nature14539"
    "doi: 10.1126/science.1157784."       -> "10.1126/science.1157784"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

MAX_DOI_LENGTH = 300
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")
_PREFIX_PATTERN = re.compile(
    r"^(?:https?://)?(?:dx\.|www\.)?doi\.org/|^doi\s*:\s*|^urn:doi:", re.IGNORECASE
)
_TRAILING = ".,;:)]}>\"'"


class InvalidDOI(ValueError):
    pass


def normalize_doi(raw: str) -> str:
    """Return the canonical DOI or raise InvalidDOI with a user-readable reason."""
    if raw is None:
        raise InvalidDOI("Empty value")
    text = raw.strip().strip("<>\"'")
    if not text:
        raise InvalidDOI("Empty value")
    if len(text) > MAX_DOI_LENGTH + 40:
        raise InvalidDOI("Value is too long to be a DOI")
    # Strip resolver URLs / "doi:" labels (possibly repeated, e.g. "doi: https://doi.org/...").
    for _ in range(3):
        stripped = _PREFIX_PATTERN.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    text = unquote(text).strip()
    # Drop punctuation commonly copied along from reference lists.
    while text and text[-1] in _TRAILING:
        text = text[:-1]
    text = text.lower()
    if not text.startswith("10."):
        raise InvalidDOI("A DOI must start with '10.' (for example 10.1038/nature14539)")
    if len(text) > MAX_DOI_LENGTH:
        raise InvalidDOI("Value is too long to be a DOI")
    if not DOI_PATTERN.match(text):
        raise InvalidDOI("Not a valid DOI: expected the form 10.<registrant>/<suffix> without spaces")
    return text


def is_valid_doi(raw: str) -> bool:
    try:
        normalize_doi(raw)
        return True
    except InvalidDOI:
        return False


@dataclass
class ParsedInput:
    valid: list[tuple[str, str]]          # (original input, normalized DOI), unique, input order kept
    invalid: list[dict[str, str]]         # {"input", "reason"}
    duplicates: list[dict[str, str]]      # {"input", "doi"} — repeated within this request


def split_bulk(text: str) -> list[str]:
    """Bulk input: one DOI per line. Blank lines are ignored."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def parse_dois(inputs: list[str]) -> ParsedInput:
    valid: list[tuple[str, str]] = []
    invalid: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in inputs:
        if raw is None or not str(raw).strip():
            continue
        raw = str(raw).strip()
        try:
            doi = normalize_doi(raw)
        except InvalidDOI as exc:
            invalid.append({"input": raw[:200], "reason": str(exc)})
            continue
        if doi in seen:
            duplicates.append({"input": raw[:200], "doi": doi})
            continue
        seen.add(doi)
        valid.append((raw[:200], doi))
    return ParsedInput(valid, invalid, duplicates)
