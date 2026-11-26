# Research_Base (`backend` branch)

> This branch = the `scraping` branch (data retrieval) + the backend: API, storage and Excel.
> The pages (`UI/`) are on the `frontend` branch. After the branches are merged into `main`,
> `UI/` sits next to `backend/` and the backend serves it.

Enter a paper's DOI → the app fetches the fields from
*Essential Fields for Research Publication Database* → the paper is saved to
**an Excel file** (`data/research_papers.xlsx`) and shown on screen.

## How to use it

1. Start the app (once per session):

   ```bash
   cd ~/Documents/Research_Base && .venv/bin/uvicorn backend.main:app --reload --port 8000
   ```

2. Open **http://127.0.0.1:8000** in your browser.
3. Type a DOI (e.g. `10.1038/nature14539`) and click **Get details**.
4. The paper's details appear, and the paper is added to the Excel file.
5. **My Library** lists every saved paper. **Download Excel** (top right) downloads the file.

The Excel file has one row per paper and one column per field in the Word document's order.
"Not available" means no source had that value. "-" means the field doesn't apply
(e.g. conference fields for a journal paper). Looking up the same DOI again updates its row
instead of adding a duplicate.

The pages are the team's frontend (`UI/`), connected to the backend. Developer details
for the API are in **[API.md](API.md)**.

## Merging the branches into `main`

```bash
git checkout main && git pull
```
```bash
git merge origin/backend
```
```bash
git merge origin/scraping
```
```bash
git merge -X no-renames origin/frontend
```

Why `-X no-renames`: an earlier commit on `main` renamed `UI/` to `templates/`. Without the
option, git puts the new pages into `templates/`. With it, one conflict remains:
`UI/details.html` ("deleted in HEAD and modified in frontend"). Keep the frontend version:

```bash
git add UI/details.html && git commit --no-edit
```

## Deploying on Vercel

1. vercel.com → **Add New… → Project** → import `Research_Base` (branch `main`). Vercel
   detects FastAPI. `pyproject.toml` (`[tool.vercel] entrypoint = "backend.main:app"`)
   tells it to run `backend/main.py`.
2. Optional environment variables: `CONTACT_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY`, `DATABASE_URL`.
3. Deploy. Check `https://<your-app>.vercel.app/api/health`.

Only `main` and `backend` contain the whole app. `vercel.json` (the same file on every branch)
turns off Vercel builds for the `scraping` and `frontend` branches, which hold only one part of
the app and would fail with "No FastAPI entrypoint found".

**Tests on GitHub:** `.github/workflows/tests.yml` runs the offline tests on every pull request.
The `main` ruleset requires this check ("Research Base Tests / pytest"), so changes reach `main`
through a pull request once it passes.

**The library is temporary for now.** Without a database, Vercel keeps saved papers only until
it restarts the app, which happens often. Lookups and the Excel download work, but the library
can reset.

**To make it permanent later (Supabase):** Supabase project → **Connect** → copy the **Session
pooler** connection string → in Vercel, project → Settings → Environment Variables → add
`DATABASE_URL` with that value → Redeploy. The tables are created automatically; no code
changes are needed. `/api/health` then shows `"storage": "postgres (permanent)"`.

Notes:
- **Quartiles:** come from `reference/scimagojr_2025.csv`, which is in the repository.
- **Excel download:** built from the database on every click, so it always has every saved paper.
- **Bulk lookups (`/api/extract`):** run in the background and are meant for local use; the pages use
  single lookups, which work on Vercel.
- **Running locally without the frontend merged:** set `FRONTEND_DIR` in `.env` to the `UI`
  folder of a checkout of the `frontend` branch.

## Preprint DOIs (arXiv etc.)

A DOI like `10.48550/arXiv.1706.03762` belongs to the **arXiv preprint**, not the journal or
conference version. The app handles this:

1. **The published version has its own DOI** (e.g. BERT → NAACL 2019, ResNet → CVPR 2016): the
   app finds it through Semantic Scholar, Crossref/DataCite links, or an exact title +
   first-author match in Crossref. It then fills every field from the published version. The
   Excel "Published Version DOI" and "Notes" columns say so.
2. **The published version has no DOI** (e.g. "Attention Is All You Need", NeurIPS 2017): the
   app uses the venue, volume and pages that Semantic Scholar and OpenAlex know. Publisher,
   conference dates and location stay "Not available", because no source has them.
3. **Never published:** the paper is marked "Preprint".

Semantic Scholar's free pool is shared and often busy, so a lookup can take up to ~30 seconds
while it retries. A free `SEMANTIC_SCHOLAR_API_KEY` makes this fast and reliable.

## Quartile (SCImago file)

Quartile comes from the free SCImago Journal Rank file `reference/scimagojr_2025.csv`, which is in
the repository and used automatically (also on Vercel). No setup is needed.

To update it each year: go to https://www.scimagojr.com/journalrank.php, click **Download data**,
save it as `reference/scimagojr_<year>.csv`, delete the old file, and restart the app. The newest
`reference/scimagojr*.csv` is always used; `SJR_CSV_PATH` overrides it. Matching is by ISSN; a journal with no ISSN in any source is matched by its exact title. The details
page says which SCImago year and journal the quartile came from. Conference proceedings in the file usually have no quartile (shown as N/A).

## Home institution and faculty IDs (fields 25–27)

- **Field 27** lists each author's institutions (OpenAlex, else the affiliation text in Crossref).
- **Field 25** is "Shiv Nadar University, Chennai" when an author is from it: an affiliation with its
  ROR ID (`039rg7508`), or text containing e.g. "Shiv Nadar University Chennai". SSN College of
  Engineering (ROR `054psm803`) and "SSN School of Engineering" also count, because the sources often
  list SNU Chennai staff under SSN. Change this with `HOME_INSTITUTION_NAME`, `HOME_INSTITUTION_ROR`
  and `HOME_INSTITUTION_ALIASES` in `.env`.
- **Field 26** names the teachers from **`reference/faculty.csv`** who wrote the paper, with their
  **faculty ID** (e.g. `SNUC-F0006`). Every teacher has one fixed ID there; the same ID appears on the
  details page, in the Excel file and in Supabase. To add a teacher, add a row with the next free ID.
  Never change or reuse an ID.
- Name matching accepts initials and either word order ("K.S. Gayathri", "Gayathri KS"). Spellings the
  rules can't reach go in `name_variants` (separated by `;`). A teacher is **not** linked when every
  source places that author at another institution, because common names are shared by different
  people. When no source lists affiliations, a name match is used and marked "name only".
- These fields are worked out when a paper is shown, so editing `faculty.csv` updates every paper at
  once. Papers saved before this feature have no affiliations yet: look their DOI up again.

## Supabase copy (optional)

`SUPABASE_DB_URL` (Transaction pooler, port 6543) also writes every Journal/Conference paper to
normalized tables for reports (`backend/integrations/database/supabase_schema.sql`; run it once in the
Supabase SQL editor). The `faculty` table is filled from `reference/faculty.csv`. A Supabase problem
never breaks a lookup; it is logged. This is separate from `DATABASE_URL`, which stores the library
the app itself reads.

## Temporary outages

The app remembers each source's last good answer per DOI. If a source is down or rate limited
on a later lookup, the earlier answer is reused, with its original date, instead of making the
stored record worse.

## Retrieval, not scraping

The data comes from **official, free APIs**: Crossref, OpenAlex and Semantic Scholar. They are
more reliable than scraping web pages, and they're allowed. Google Scholar, Scopus and Web of
Science web pages are **not scraped**. Their terms forbid it and they block bots. Scopus and Web of
Science are queried only through their official APIs, when the department provides keys.

## Field coverage

| # | Field | Source | Status |
|---|-------|--------|--------|
| 1 | Publication Title | Crossref → OpenAlex → Semantic Scholar → DataCite (arXiv, Zenodo…) | ✅ |
| 2 | Publication Type (Journal / Conference) | Crossref work type → OpenAlex → S2 | ✅ |
| 3 | Author Names (published order) | Crossref → OpenAlex → S2 | ✅ |
| 4 | Publication Year | Crossref → OpenAlex → S2 | ✅ |
| 5 | Publisher | Crossref | ✅ |
| 6 | DOI | input (normalized) | ✅ |
| 7 | Publication URL | Crossref (publisher landing page) → OpenAlex | ✅ |
| 8 | Indexing Database | Scopus API, Web of Science API (**keys needed**); OpenAlex for "Other" (Crossref, PubMed, DOAJ…) | ⚠️ needs keys for Scopus/WoS |
| 9 | Journal Name | Crossref → OpenAlex → S2 | ✅ |
| 10 | ISSN / E-ISSN | Crossref → OpenAlex | ✅ |
| 11–13 | Volume, Issue, Page Range / Article No. | Crossref → OpenAlex → S2 | ✅ (when the publisher deposits them) |
| 14 | Quartile | SCImago (SJR) CSV, matched by ISSN | ⚠️ needs the free SJR file |
| 15 | Impact Factor | Clarivate JCR (licensed), from a department CSV | ⚠️ needs licensed data |
| 16–19 | Conference Name, Acronym, Dates, Location | Crossref `event` metadata (S2/OpenAlex fallback for name/acronym) | ✅ when the publisher deposits them (IEEE/ACM usually do) |
| 20–23 | Proceedings Title, Publisher, ISBN/ISSN, Volume/Pages | Crossref | ✅ (ISBN is often not deposited at article level) |
| 24 | Publication Date | Crossref (earliest of online/print) → OpenAlex → S2; `YYYY-MM` when no source knows the day | ✅ |
| 25 | Home Institution | Author affiliations (OpenAlex, Crossref) and the faculty list | ✅ |
| 26 | Home Faculty (Faculty ID) | `reference/faculty.csv` matched against the authors | ✅ |
| 27 | Author Affiliations | OpenAlex (institutions + ROR) → Crossref (affiliation text) | ✅ when the publisher deposits them |
| extra | PDF link / availability | OpenAlex, Semantic Scholar (legal open-access copies only) | ✅ |

**Book-series proceedings and book chapters.** Springer (LNCS, LNNS, CCIS...) and others register many
conference papers as book chapters. They are recorded as Conference when the book title names a
conference or OpenAlex / Semantic Scholar say so, with the book, series, ISBN, series ISSN and pages in
fields 20-23. True book chapters use fields 20-23 for the book (title, publisher, ISBN, pages); the
journal and conference-event fields are marked not applicable.

A field that can't be retrieved is returned as `null`, with the reason in `provenance`.
Nothing is guessed. A real `0` stays `0`. Citation counts from different sources are kept separately.

## Setup and run

Requires Python 3.10+.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

```bash
.venv/bin/uvicorn backend.main:app --reload --port 8000
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\uvicorn`.

- API: http://127.0.0.1:8000/api/health
- Interactive API docs: http://127.0.0.1:8000/docs
- The app: http://127.0.0.1:8000/

Quick check:

```bash
curl -X POST http://127.0.0.1:8000/api/papers -H "Content-Type: application/json" -d '{"doi": "10.1109/CVPR.2016.90"}'
```

## Configuration (`.env`, all optional)

| Variable | Enables |
|----------|---------|
| `CONTACT_EMAIL` | Crossref/OpenAlex "polite pool" (more reliable). Use a department email. |
| `SEMANTIC_SCHOLAR_API_KEY` | Reliable Semantic Scholar access (the anonymous pool is often rate limited). Free on request. |
| `SCOPUS_API_KEY` | Field 8: Scopus indexing check (Elsevier, https://dev.elsevier.com, institutional). |
| `WOS_API_KEY` | Field 8: Web of Science indexing check (Clarivate WoS Starter API). |
| `SJR_CSV_PATH` | Field 14: path to the SCImago journal ranking CSV (free download from scimagojr.com → "Download data"). Default: the newest `reference/scimagojr*.csv`. |
| `IMPACT_FACTOR_CSV_PATH` | Field 15: CSV with columns `issn,impact_factor[,year]` exported from JCR (licensed). |
| `DATABASE_URL` | Hosted Postgres (e.g. Supabase) to keep the library permanently. Not set: SQLite file `data/research_data.db` (on Vercel: temporary, in `/tmp`). |
| `SUPABASE_DB_URL` | Optional normalized copy for reports (see "Supabase copy"). |
| `FACULTY_CSV_PATH` | Faculty list with IDs (default `reference/faculty.csv`). |
| `HOME_INSTITUTION_NAME`, `HOME_INSTITUTION_ROR`, `HOME_INSTITUTION_ALIASES` | Fields 25–26: which institution counts as home (default: Shiv Nadar University, Chennai). |
| `SOURCE_TIME_BUDGET_SECONDS` | Most time one source may take, retries included (default 40). |
| `EXCEL_PATH` | Where a copy of the Excel file is kept locally (default `data/research_papers.xlsx`; not used on Vercel). |
| `FRONTEND_DIR` | Folder with the pages (default `UI`). |
| `CORS_ORIGINS` | Origins allowed to call the API (default `*` for local development). |

API keys stay on the server. They are never sent to the browser. `.env`, the database and logs are git-ignored.

## Project layout

```
UI/                       the pages: index.html (look up), inventory.html (library), details.html
backend/
  main.py                 API routes (/api/...), CORS, serves UI/ if present
  config.py               settings from .env
  database.py             SQLite (all SQL lives here; can be swapped for PostgreSQL)
  services/
    base.py               shared HTTP client: timeouts, retries, rate limits
    crossref.py           bibliographic + conference metadata
    openalex.py           type/venue fallback, indexing ("Other"), PDF, affiliations
    semantic_scholar.py   fallback metadata, PDF, citation metrics
    indexing.py           Scopus / Web of Science official APIs (optional keys)
    journal_rankings.py   quartile (SJR file) and impact factor (JCR file) by ISSN
  processing/
    doi_validator.py      DOI normalization / validation / de-duplication
    metric_mapper.py      the field list (numbered as in the field document)
    data_merger.py        merges sources, keeps provenance, builds the frontend JSON
    home_institution.py   home institution + faculty matching (fields 25-27)
    retrieval.py          DOI -> standardized record (no database; used by fetch_doi.py too)
    pipeline.py           retrieval + storage, single DOI or background bulk job
  integrations/
    excel_export.py       writes data/research_papers.xlsx after every add/delete
    database/             optional Supabase copy (schema, writer, record mapping)
reference/                scimagojr_2025.csv (quartiles), faculty.csv (teachers and their IDs)
fetch_doi.py              command-line lookup (no server needed)
tests/                    unit, API and live tests
API.md                    endpoint contract for the frontend
```

**Adding or changing a field:** edit `METRICS` in `backend/processing/metric_mapper.py`. If the
value isn't extracted yet, add one line to the relevant `services/*.py` parser. Then add it to
`flatten()` in `data_merger.py` if the frontend needs it as a top-level key.

## Tests

```bash
.venv/bin/pytest
```

That runs 118 tests, 6 of them against the live APIs (one journal paper, one conference paper, one
paper in all three sources, and a DOI that doesn't exist). For offline runs use
`.venv/bin/pytest -m "not live"`.
