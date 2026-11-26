# How Research Base works

A simple guide to how the **frontend**, **scraping** and **backend** parts fit together.

---

## 1. The big picture

```
 ┌──────────────────────────┐        ┌───────────────────────────────┐        ┌──────────────────────────┐
 │  FRONTEND  (UI/)         │  HTTP  │  BACKEND  (backend/main.py)   │  calls │  SCRAPING                │
 │  HTML + CSS + JavaScript │ ─────▶ │  FastAPI web server           │ ─────▶ │  backend/services/       │
 │  runs in the browser     │ ◀───── │  storage + Excel              │ ◀───── │  backend/processing/     │
 └──────────────────────────┘  JSON  └───────────────────────────────┘        └────────────┬─────────────┘
                                                                                           │ HTTPS
                                                                                           ▼
                                                              Crossref · OpenAlex · Semantic Scholar · DataCite
                                                              + SCImago file (quartiles)
```

| Part | Branch | Folder | Language | Job |
|------|--------|--------|----------|-----|
| Frontend | `frontend` | `UI/` | HTML, CSS, JavaScript | What the user sees and clicks |
| Backend | `backend` | `backend/main.py`, `database.py`, `integrations/` | Python | Receives requests, stores papers, builds Excel |
| Scraping | `scraping` | `backend/services/`, `backend/processing/`, `reference/` | Python | Fetches the 27 fields for a DOI |

All three are merged into `main`, and that is what runs on Vercel.

---

## 2. How the HTML and JavaScript are linked to the backend

The browser and Python **never call each other's code**. They talk only through
**web requests (HTTP)** that carry **JSON** data.

### One server delivers both pages and data

In `backend/main.py`:

```python
app.mount("/", StaticFiles(directory="UI", html=True))   # pages: /, /inventory.html, ...
```

- `http://127.0.0.1:8000/` → sends `UI/index.html` (then its CSS and JS files)
- `http://127.0.0.1:8000/api/...` → runs a Python function and returns JSON

### Each HTML page loads its JavaScript

```html
<script src="js/common.js"></script>   <!-- shared helper: apiFetch() -->
<script src="js/home.js"></script>     <!-- this page's logic -->
```

### The JavaScript calls the backend with `fetch()`

`UI/js/common.js` has one helper used by every page:

```js
async function apiFetch(path, options) {
    const response = await fetch(API_BASE + path, { headers: { "Content-Type": "application/json" }, ...options });
    const data = await response.json();               // JSON reply → JavaScript object
    if (!response.ok) throw new Error(data.detail);   // errors carry a readable message
    return data;
}
```

### Which page calls which address

| Page | JavaScript file | Calls | Python function |
|------|-----------------|-------|-----------------|
| Home (`index.html`) | `home.js` | `POST /api/papers` with `{"doi": "..."}` | `add_paper()` |
| My Library (`inventory.html`) | `inventory.js` | `GET /api/papers?search=&type=` | `list_papers()` |
| Details (`details.html?paperId=3`) | `details.js` | `GET /api/papers/3` | `get_paper()` |
| Every page | `common.js` | "Download Excel" link → `GET /api/excel` | `download_excel()` |

### Example: searching a DOI, start to finish

1. You type `10.1038/nature14539` and click **Get details**.
2. `home.js` sends:
   ```
   POST /api/papers
   {"doi": "10.1038/nature14539", "refresh": true}
   ```
3. Python `add_paper()` runs the scraping part, saves the paper, and replies:
   ```json
   {"id": 3, "fields": {"title": "Deep learning", "publication_type": "Journal", "journal": "Nature", "volume": "521", "quartile": "Q1", ...}}
   ```
4. `home.js` opens `details.html?paperId=3`.
5. `details.js` calls `GET /api/papers/3` and writes each field into the table.

If something goes wrong (for example a fake DOI), Python replies with an error code and a
message (`404` "No record found... please check the DOI"), and the page shows that message.

---

## 3. How the scraping part works

**It uses official APIs, not web scraping.** Academic databases offer addresses that return
clean data for a DOI. That is allowed, free and reliable. Google Scholar is not used: it has no
API and forbids automated access.

### Sources

| Source | Gives us |
|--------|----------|
| **Crossref** | Title, authors, year, publisher, journal, ISSN, volume, issue, pages, conference name / dates / location |
| **OpenAlex** | Backup for the above, E-ISSN, PDF link, "Other" indexing |
| **Semantic Scholar** | Conference names and acronyms, where an arXiv preprint was published |
| **DataCite** | DOIs that Crossref does not have (arXiv, Zenodo); asked only if Crossref says "not found" |
| **SCImago file** (`reference/scimagojr_2025.csv`) | Quartile Q1 to Q4, looked up by ISSN |

### Steps for one DOI

1. **Clean the DOI** (`processing/doi_validator.py`): `https://doi.org/10.1038/NATURE14539` becomes `10.1038/nature14539`.
2. **Ask all sources at the same time** (`services/*.py`, one file per source).
3. **Handle problems** (`services/base.py`):
   - **Timeout:** stop waiting for a source after 20 seconds.
   - **Busy source:** wait and retry.
   - **One source fails:** the others still count.
4. **Preprints** (`processing/retrieval.py`): if the DOI is an arXiv preprint, find the published version and use its details.
5. **Combine** (`processing/data_merger.py`): pick the best value per field, remember its source, and never invent missing values.
6. **Result:** the 23 fields of the "Essential Fields" document, in its order, plus 24–27 (publication date, home institution, home faculty with their IDs, author affiliations).

### Libraries used

| Library | What it does |
|---------|--------------|
| `httpx` | Makes the web requests to the APIs |
| `asyncio` (built in) | Lets us wait for several sources at the same time |
| `python-dotenv` | Reads settings and API keys from `.env` |
| `csv`, `re`, `html`, `unicodedata` (built in) | Read the SCImago file, check DOI format, clean up text |

The scraping part also works on its own, without the website:

```bash
.venv/bin/python fetch_doi.py 10.1038/nature14539
```

---

## 4. How the backend works

Think of it as a **receptionist with a filing cabinet**.

### Files

| File | Job |
|------|-----|
| `backend/config.py` | Reads settings: API keys, where to store data, where the pages are |
| `backend/main.py` | The web server: links each `/api/...` address to a Python function |
| `backend/processing/pipeline.py` | "Fetch, then save": runs the scraping part and stores the result |
| `backend/database.py` | The filing cabinet: every save and load goes through this file |
| `backend/integrations/excel_export.py` | Builds the Excel file with the 27 field columns |

### What happens when the server starts (`main.py`)

1. Set up logging (the messages in the terminal).
2. Create the database tables if they don't exist yet.
3. Prepare one connection per academic source.
4. Wait for requests.

### What happens on `POST /api/papers` (`add_paper()` in `main.py`)

1. **Check the DOI.** If it's invalid → reply `400`.
2. **Fetch and save:** `pipeline.extract_doi()` runs the scraping part, then `database.save_paper()`.
3. **Reply:**
   - **No source knows the DOI** → `404`
   - **All sources failed** → `502`
   - **Success** → `201` with the paper's fields

### Storage (`database.py`)

| Table | Holds |
|-------|-------|
| `papers` | One row per paper: id (the `paperId` in page links), DOI, title, type, year, full record |
| `metric_values` | Every field from every source, with when it was fetched |
| `source_cache` | Each source's last good answer, reused if that source is down later |

- **On your computer:** a SQLite file, `data/research_data.db`.
- **On Vercel:** SQLite in `/tmp`, so the library **resets when Vercel restarts the app**.
- **Later:** set `DATABASE_URL` to a Supabase (Postgres) connection string and the library becomes permanent. No code changes needed.

### Excel (`excel_export.py`)

Each click on **Download Excel** reads every paper from the database and builds a fresh file:

- one row per paper, with the 27 field columns in order
- **"Not available":** no source had that value
- **"-":** the field does not apply (e.g. conference fields for a journal paper)

### Libraries used

| Library | What it does |
|---------|--------------|
| `fastapi` | The web server: addresses → Python functions, JSON in and out |
| `uvicorn` | Runs the server on your computer |
| `pydantic` | Checks incoming requests are valid |
| `sqlite3` (built in) | Local database file |
| `psycopg` | Talks to Postgres (Supabase); unused until `DATABASE_URL` is set |
| `openpyxl` | Creates the Excel file |

---

## 5. The whole flow in one view

```
Browser (UI/home.js)
   │  POST /api/papers {"doi": "..."}
   ▼
backend/main.py ── add_paper()           checks the DOI, decides the reply
   │
   ▼
backend/processing/pipeline.py           fetch, then save
   │                         │
   ▼                         ▼
retrieval.py (scraping)   database.py (save_paper)
   │  asks Crossref, OpenAlex, Semantic Scholar, DataCite + SCImago file
   ▼
JSON reply ──▶ browser opens details.html?paperId=…  ──▶ GET /api/papers/… ──▶ table filled
```

---

## 6. Running it

```bash
.venv/bin/uvicorn backend.main:app --reload --port 8000
```

Open http://127.0.0.1:8000. API documentation (try every endpoint): http://127.0.0.1:8000/docs.
The full list of endpoints and the JSON fields is in `API.md`. Merge and Vercel steps are in `README.md`.
