# Research_Base (`scraping` branch): data retrieval

Takes a paper's DOI and fetches the 23 fields from *Essential Fields for Research Publication
Database* from official academic APIs, plus fields 24-27: publication date, home institution,
home faculty (with each teacher's faculty ID) and author affiliations. This branch is the data-retrieval part only. The
`backend` branch builds on it and adds the API, database, Excel file and connected pages.

## Try it

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python fetch_doi.py 10.1038/nature14539
```

Several DOIs, or JSON output:

```bash
.venv/bin/python fetch_doi.py 10.1109/CVPR.2016.90 10.48550/arXiv.1706.03762 --json
```

## Where the data comes from

All sources are official, free APIs. Nothing is scraped from web pages. Google Scholar has no API
and forbids automated access, so it is not used.

| Source | Used for |
|--------|----------|
| Crossref | Title, authors, year, publication date, publisher, URL, journal, ISSN, volume, issue, pages, conference name / acronym / dates / location, proceedings details, affiliation text |
| DataCite | DOIs Crossref doesn't have (arXiv, Zenodo, repositories) |
| OpenAlex | Fallback for type / venue / pages / date, "Other" indexing, PDF link, E-ISSN, author institutions (with ROR IDs) |
| Semantic Scholar | Conference venue and acronym, where a preprint was published, PDF link |
| Scopus / Web of Science APIs | Indexing (field 8), only with institutional keys |
| SCImago file | Quartile (field 14): `reference/scimagojr_2025.csv`, used automatically; matched by ISSN, or exact title when no ISSN |
| Faculty list | Home faculty and their IDs (field 26): `reference/faculty.csv` (see `reference/README.md`) |
| JCR file | Impact factor (field 15), only with licensed data (`IMPACT_FACTOR_CSV_PATH`) |

**Preprints** (e.g. `10.48550/arXiv.1706.03762`): the published version is found through
Crossref/DataCite links, Semantic Scholar, or an exact title + first-author match in Crossref,
and its details are used. Missing values are left empty with a reason; nothing is guessed.

**Home institution** (fields 25-26): an author is from Shiv Nadar University, Chennai when an
affiliation has its ROR ID or name (SSN College of Engineering / SSN School of Engineering also count;
see `HOME_INSTITUTION_*` in `.env.example`). A teacher on the faculty list is linked by name, unless
every source places that author at another institution.

## Code

```
fetch_doi.py                   command-line lookup
backend/
  config.py                    settings from .env (all optional, see .env.example)
  services/                    one client per source (timeouts, retries, rate limits)
  processing/
    doi_validator.py           DOI normalization / validation
    metric_mapper.py           the field list, numbered as in the field document
    data_merger.py             merges sources, keeps provenance, builds the flat fields
    home_institution.py        home institution and faculty matching (fields 25-27)
    retrieval.py               retrieve(doi, clients) -> record   <- entry point
reference/                     scimagojr_2025.csv (quartiles), faculty.csv (teachers and their IDs)
```

Using it from other code:

```python
from backend.processing.retrieval import retrieve
from backend.processing.data_merger import flatten
from backend.services import build_clients

record = await retrieve("10.1038/nature14539", build_clients())
fields = flatten(record)["fields"]      # {"title": ..., "journal": ..., "quartile": ...}
```

## Tests

```bash
.venv/bin/pytest
```

Add `-m "not live"` to skip the tests that call the real APIs.
