# Research Base: Data Retrieval API (for the frontend)

Base URL when running locally: `http://127.0.0.1:8000/api`

If the `UI/` folder is in the same checkout (after merging the `frontend` branch), the backend
also serves the pages at `http://127.0.0.1:8000/`, so `fetch("/api/...")` works with relative URLs.
If the pages are opened from another server (e.g. VS Code Live Server on port 5500), use the full
URL `http://127.0.0.1:8000/api/...`. CORS is enabled for that.

Every field value is `null` when it could not be retrieved. Show "Not available" in that case.
A value of `0` is a real zero, not "missing".

---

## Home page: look up a DOI (`home.js`)

`POST /api/papers`, body `{"doi": "<what the user typed>"}`

The backend accepts `10.1038/nature14539`, `https://doi.org/10.1038/nature14539` and `doi:10.1038/...`,
and it re-validates the DOI, so the frontend regex check is just a first filter.

| Status | Meaning | Suggested UI |
|--------|---------|--------------|
| `201` | Retrieved and saved to the library | Show the paper / go to details |
| `200` | Already in the library (`already_in_library: true`) | Same |
| `400` | Not a valid DOI (`detail` explains) | Show `detail` |
| `404` | No source knows this DOI | "DOI not found. Please check the DOI." |
| `502` | Sources unreachable / rate limited | "Could not reach the data sources, try again." |

Send `{"doi": "...", "refresh": true}` to force a re-fetch of a paper already in the library.

```js
const res = await fetch("/api/papers", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ doi }),
});
const data = await res.json();
if (!res.ok) {
  alert(data.detail);              // 400 / 404 / 502 messages are user-readable
} else {
  window.location.href = `details.html?paperId=${data.id}`;
}
```

## My Library page (`inventory.js`)

`GET /api/papers?search=<text>&type=<Journal|Conference>&page=1&page_size=20&sort=updated_at&order=desc`

All parameters are optional. `search` matches title, authors, DOI and journal.
`sort`: `id`, `title`, `publication_year`, `publication_type`, `journal`, `doi`, `updated_at`.

```json
{
  "items": [
    {
      "id": 1,
      "title": "Deep learning",
      "authors": ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"],
      "authors_text": "Yann LeCun, Yoshua Bengio, Geoffrey Hinton",
      "publication_year": 2015,
      "publication_type": "Journal",
      "doi": "10.1038/nature14539",
      "venue": "Nature",
      "status": "complete",
      "updated_at": "2026-11-17T18:10:02+00:00"
    }
  ],
  "total": 1, "page": 1, "page_size": 20, "pages": 1
}
```

Table columns map directly: S.No. → row index, Paper Title → `title`, Authors → `authors_text`,
Year → `publication_year`, Publication Type → `publication_type`, DOI → `doi`,
View → `details.html?paperId=${id}`.

`DELETE /api/papers/{id}` removes a paper (returns `204`).

## Details page (`details.js`)

`GET /api/papers/{paperId}` returns `404` if the id does not exist.

Element IDs in `details.html` → JSON keys in `data.fields`:

| Element id | Field | # in field list |
|------------|-------|-----------------|
| `paper-title` | `title` | 1 |
| `publication-type` | `publication_type` (`"Journal"`, `"Conference"`, or `"Book chapter"` / `"Preprint"` / `"Other"`) | 2 |
| `authors` | `authors_text` (or the `authors` array, in published order) | 3 |
| `publication-year` | `publication_year` | 4 |
| `publisher` | `publisher` | 5 |
| `doi` | `doi` | 6 |
| `publication-url` | `publication_url` | 7 |
| `indexing` | `indexing` (text), details in `indexing_detail` | 8 |
| `journal-name` | `journal` | 9 |
| `issn` | `issn` | 10 |
| `volume` | `volume` | 11 |
| `issue` | `issue` | 12 |
| `page-range` | `page_range` | 13 |
| `quartile` | `quartile` | 14 |
| `impact-factor` | `impact_factor` | 15 |
| `conference-name` | `conference_name` | 16 |
| `conference-acronym` | `conference_acronym` | 17 |
| `conference-dates` | `conference_dates` | 18 |
| `conference-location` | `conference_location` | 19 |
| `proceedings-title` | `proceedings_title` | 20 |
| `proceedings-publisher` | `proceedings_publisher` | 21 |
| `proceedings-isbn` | `proceedings_isbn_issn` | 22 |
| `proceedings-volume-pages` | `proceedings_volume_pages` | 23 |
| `publication-date` | `publication_date`: `"YYYY-MM-DD"`, or `"YYYY-MM"` when no source knows the day | 24 |
| `home-institution` | `home_institution`: e.g. `"Shiv Nadar University, Chennai"`, or `null` when no author is from it | 25 |
| `home-faculty` | `home_faculty`: `[{faculty_id, name, author, position, evidence}]` (text: `home_faculty_text`) | 26 |
| `author-affiliations` | `author_affiliations`: `[{author, institutions, is_home}]` in author order (text: `author_affiliations_text`) | 27 |
| `pdf-status` / `pdf-link` | `pdf_available` (`true` / `false` / `null` = unknown), `pdf_url` | (extra) |

Also: `quartile_detail` (which SCImago year and journal the quartile came from), `home_authors`
(authors placed at the home institution by their affiliation, on the faculty list or not) and
`institutions_checked` (`false` only for papers saved before fields 24-27 existed; look them up again).
For fields 25-26, `null` with `institutions_checked: true` means "No", and
`provenance.home_institution.reason` / `provenance.home_faculty.reason` say why.

Conditional display: show `#journal-info` when `publication_type === "Journal"`, and
`#conference-info` + `#proceedings-info` when it is `"Conference"`.

```js
const params = new URLSearchParams(window.location.search);
const paperId = params.get("paperId");
const res = await fetch(`/api/papers/${encodeURIComponent(paperId)}`);
if (!res.ok) { /* 404: show "Paper not found" */ }
const { fields } = await res.json();

const set = (id, value) => {
  document.getElementById(id).textContent =
    value === null || value === undefined || value === "" ? "Not available" : value;
};
set("paper-title", fields.title);
set("publication-type", fields.publication_type);
set("authors", fields.authors_text);
set("journal-name", fields.journal);
// ...one line per row of the table above

document.getElementById("journal-info").hidden = fields.publication_type !== "Journal";
document.getElementById("conference-info").hidden = fields.publication_type !== "Conference";
document.getElementById("proceedings-info").hidden = fields.publication_type !== "Conference";

const pdfLink = document.getElementById("pdf-link");
if (fields.pdf_url) {
  set("pdf-status", "PDF availability: Open-access PDF available");
  pdfLink.href = fields.pdf_url;
} else {
  set("pdf-status", fields.pdf_available === false
    ? "PDF availability: No open-access PDF" : "PDF availability: Unknown");
  pdfLink.hidden = true;
}
```

`data.provenance[<field>]` says which source each value came from, and for missing values,
why (e.g. `quartile: {"source": null, "reason": "Not checked: no SCImago (SJR) file configured..."}`).
It's useful for a tooltip.

## Bulk lookup (optional)

- `POST /api/extract` with `{"text": "doi1\ndoi2\n..."}` (or `{"dois": [...]}`) → `202 {job_id, invalid, duplicates}`
- `GET /api/status/{job_id}` → progress per DOI and per source
- `GET /api/results/{job_id}` → the papers (same shape as the details response)

## Excel

`GET /api/excel` downloads `research_papers.xlsx`. It is rebuilt automatically after every add or delete.

## Other

- `GET /api/health`: backend status and which sources are configured
- `GET /api/fields`: every field with its number, group, sources and description
- Interactive docs: `http://127.0.0.1:8000/docs`
