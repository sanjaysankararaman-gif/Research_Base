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
   detects FastAPI. `pyproject.toml` tells it to run `backend/main.py`, so the older root
   `main.py` is not used.
2. Optional environment variables: `CONTACT_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY`.
3. Deploy. Check `https://<your-app>.vercel.app/api/health`.

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

Quartile comes from the free SCImago Journal Rank file:

1. Go to https://www.scimagojr.com/journalrank.php and click **Download data**.
2. Save it as `data/scimagojr_2025.csv` (any name works).
3. In `.env`, set `SJR_CSV_PATH=data/scimagojr_2025.csv` and restart the app.

Download the new file each year. Conference proceedings in the file usually have no quartile
(shown as N/A).

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
| extra | PDF link / availability | OpenAlex, Semantic Scholar (legal open-access copies only) | ✅ |

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
| `SJR_CSV_PATH` | Field 14: path to the SCImago journal ranking CSV (free download from scimagojr.com → "Download data"). |
| `IMPACT_FACTOR_CSV_PATH` | Field 15: CSV with columns `issn,impact_factor[,year]` exported from JCR (licensed). |
| `DATABASE_URL` | Hosted Postgres (e.g. Supabase) to keep the library permanently. Not set: SQLite file `data/research_data.db` (on Vercel: temporary, in `/tmp`). |
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
    google_scholar.py     not queried (no permitted API); reports "unavailable"
  processing/
    doi_validator.py      DOI normalization / validation / de-duplication
    metric_mapper.py      the field list (numbered as in the field document)
    data_merger.py        merges sources, keeps provenance, builds the frontend JSON
    retrieval.py          DOI -> standardized record (no database; used by fetch_doi.py too)
    pipeline.py           retrieval + storage, single DOI or background bulk job
  integrations/
    excel_export.py       writes data/research_papers.xlsx after every add/delete
    google_sheets.py      spreadsheet row builder (sync not implemented yet)
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

That runs 66 tests, 6 of them against the live APIs (one journal paper, one conference paper, one
paper in all three sources, and a DOI that doesn't exist). For offline runs use
`.venv/bin/pytest -m "not live"`.
