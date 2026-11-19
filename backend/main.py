"""Research Base - data retrieval API (FastAPI).

Run from the repository root:
    .venv/bin/uvicorn backend.main:app --reload --port 8000

All endpoints live under /api. If the team's frontend folder (UI/, from the
frontend branch) is present, it is served at http://127.0.0.1:8000/ so the
pages and the API share one origin. CORS is also enabled, so the pages work
when opened from another local server or port.

See API.md for request/response examples.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import database as db
from .config import settings
from .integrations import excel_export
from .models.schemas import ExtractRequest, ExtractResponse, JobResults, JobStatus, PaperRequest
from .processing.data_merger import flatten, library_row
from .processing.doi_validator import InvalidDOI, normalize_doi, parse_dois, split_bulk
from .processing.metric_mapper import describe_metrics
from .processing.pipeline import extract_doi, run_job
from .services import SOURCE_LABELS, build_clients


def configure_logging() -> None:
    """Log to the console (shown in Vercel's logs) and, locally, to logs/researchbase.log."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger("extractor")
    root.setLevel(settings.log_level)
    if root.handlers:
        return
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if settings.log_dir is not None:
        try:
            settings.log_dir.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(settings.log_dir / "researchbase.log", maxBytes=2_000_000, backupCount=3)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            root.warning("Cannot write log files to %s; logging to the console only", settings.log_dir)


log = logging.getLogger("extractor.api")
_background: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db.init_db()
    interrupted = db.mark_interrupted_jobs()
    if interrupted:
        log.warning("Marked %d unfinished job item(s) from a previous run as failed", interrupted)
    app.state.clients = build_clients()
    log.info("Started %s %s (storage: %s)", settings.app_name, settings.version, db.storage_description())
    if settings.on_vercel and not db.using_postgres():
        log.warning("No DATABASE_URL: the library is stored in /tmp and will reset when Vercel restarts the app")
    yield
    for task in list(_background):
        task.cancel()
    for client in app.state.clients.values():
        await client.close()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
api = APIRouter(prefix="/api")


# ------------------------------------------------------------------ error handling
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    problems = [
        f"{'.'.join(str(p) for p in e.get('loc', [])[1:]) or 'body'}: {e.get('msg')}" for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": problems})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error. The error has been logged."})


# ------------------------------------------------------------------ health / reference
@api.get("/health")
async def health():
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        db_state = "ok"
    except Exception:
        log.exception("Health check: database unavailable")
        db_state = "error"
    sources = {
        "crossref": "enabled",
        "openalex": "enabled",
        "semantic_scholar": "enabled (API key)" if settings.semantic_scholar_api_key
        else "enabled (no API key, shared rate limit)",
        "scopus": "enabled" if settings.scopus_api_key else "not configured (SCOPUS_API_KEY)",
        "web_of_science": "enabled" if settings.wos_api_key else "not configured (WOS_API_KEY)",
        "sjr": "enabled" if settings.sjr_csv_path and settings.sjr_csv_path.is_file()
        else "not configured (SJR_CSV_PATH)",
        "jcr_file": "enabled" if settings.impact_factor_csv_path and settings.impact_factor_csv_path.is_file()
        else "not configured (IMPACT_FACTOR_CSV_PATH)",
        "google_scholar": "not available (no permitted integration)",
    }
    ok = db_state == "ok"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "version": settings.version,
                 "database": db_state, "storage": db.storage_description(),
                 "storage_is_permanent": db.using_postgres() or not settings.on_vercel,
                 "frontend": settings.frontend_dir.is_dir(), "sources": sources},
    )


@api.get("/fields")
async def fields():
    """Every field: number in the department's list, group, sources and description."""
    return {"sources": SOURCE_LABELS, "fields": describe_metrics()}


# ------------------------------------------------------------------ single DOI (Home page)
@api.post("/papers")
async def add_paper(body: PaperRequest, request: Request):
    """Look up one DOI, store it in the library, and return the paper.

    200 -> paper already in the library and refresh=false (returned from storage)
    201 -> retrieved and stored
    400 -> not a valid DOI
    404 -> no source has a record for this DOI
    502 -> every source failed (network / rate limit); try again later
    """
    try:
        doi = normalize_doi(body.doi)
    except InvalidDOI as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    existing = db.get_paper(doi)
    if existing and not body.refresh:
        return JSONResponse(status_code=200, content={**flatten(existing), "already_in_library": True})

    record = await extract_doi(doi, request.app.state.clients)
    if record["status"] == "not_found":
        raise HTTPException(status_code=404, detail=f"No record found for DOI {doi}. Please check the DOI.")
    if record["status"] == "failed":
        if existing:   # keep serving the stored copy
            return JSONResponse(status_code=200, content={
                **flatten(existing), "already_in_library": True,
                "warning": "Sources could not be reached; showing the stored copy."})
        raise HTTPException(status_code=502,
                            detail="The academic data sources could not be reached. Please try again shortly.")
    stored = db.get_paper(doi)
    saved_to_excel = excel_export.safe_update()
    return JSONResponse(status_code=201 if not existing else 200,
                        content={**flatten(stored), "already_in_library": bool(existing),
                                 "saved_to_excel": saved_to_excel})


# ------------------------------------------------------------------ library (My Library page)
@api.get("/papers")
async def list_papers(
    search: str | None = Query(None, max_length=200, description="Matches title, authors, DOI, journal"),
    type: str | None = Query(None, pattern="^(Journal|Conference|Book chapter|Book|Preprint|Other)$",
                             description="Publication type filter"),
    status: str | None = Query(None, pattern="^(complete|partial)$"),
    sort: str = Query("updated_at", pattern="^(id|doi|title|journal|publication_year|publication_type|status|updated_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(20, ge=1, le=200),
):
    items, total = db.list_papers(search=search, status=status, publication_type=type,
                                  sort=sort, order=order, page=page, page_size=page_size)
    return {
        "items": [library_row(r) for r in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


@api.get("/papers/by-doi/{doi:path}")
async def get_paper_by_doi(doi: str):
    try:
        normalized = normalize_doi(doi)
    except InvalidDOI as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    record = db.get_paper(normalized)
    if record is None:
        raise HTTPException(status_code=404, detail=f"DOI {normalized} is not in the library")
    return flatten(record)


@api.get("/papers/{paper_id}")
async def get_paper(paper_id: int, raw: bool = Query(False, description="Full per-source record")):
    """Details page: GET /api/papers/{paperId}."""
    record = db.get_paper_by_id(paper_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    return record if raw else flatten(record)


@api.delete("/papers/{paper_id}", status_code=204)
async def delete_paper(paper_id: int):
    if not db.delete_paper(paper_id):
        raise HTTPException(status_code=404, detail=f"Paper {paper_id} not found")
    excel_export.safe_update()


@api.get("/excel")
async def download_excel():
    """Download an Excel file with every paper in the library (built from the database now)."""
    return Response(
        content=excel_export.workbook_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="research_papers.xlsx"',
                 "Cache-Control": "no-store"},
    )


# ------------------------------------------------------------------ bulk extraction (background jobs)
@api.post("/extract", response_model=ExtractResponse, status_code=202)
async def extract(body: ExtractRequest, request: Request):
    inputs: list[str] = []
    if body.doi:
        inputs.append(body.doi)
    inputs += body.dois
    if body.text:
        inputs += split_bulk(body.text)
    if not inputs:
        raise HTTPException(status_code=400, detail="No DOI provided. Enter at least one DOI.")

    parsed = parse_dois(inputs)
    if not parsed.valid:
        return JSONResponse(status_code=422, content={
            "detail": "None of the provided values is a valid DOI.",
            "invalid": parsed.invalid, "duplicates": parsed.duplicates})
    if len(parsed.valid) > settings.max_dois_per_request:
        raise HTTPException(status_code=413, detail=(
            f"Too many DOIs ({len(parsed.valid)}). The limit is {settings.max_dois_per_request} per request."))

    to_fetch, reused = parsed.valid, []
    if not body.refresh:
        to_fetch = []
        for raw, doi in parsed.valid:
            (reused.append(doi) if db.get_paper(doi) else to_fetch.append((raw, doi)))

    job_id = None
    if to_fetch:
        job_id = uuid.uuid4().hex
        db.create_job(job_id, to_fetch, list(SOURCE_LABELS), parsed.invalid, parsed.duplicates)
        task = asyncio.create_task(run_job(job_id, to_fetch, request.app.state.clients))
        _background.add(task)
        task.add_done_callback(_background.discard)
        log.info("Job %s queued with %d DOI(s)", job_id, len(to_fetch))

    return ExtractResponse(
        job_id=job_id, status="queued" if job_id else "completed", accepted=len(parsed.valid),
        dois=[d for _, d in parsed.valid], reused=reused, invalid=parsed.invalid, duplicates=parsed.duplicates,
        status_url=f"/api/status/{job_id}" if job_id else None,
        results_url=f"/api/results/{job_id}" if job_id else None,
    )


def _job_or_404(job_id: str) -> dict:
    if not job_id.isalnum() or len(job_id) > 64:
        raise HTTPException(status_code=400, detail="Malformed job id")
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@api.get("/status/{job_id}", response_model=JobStatus)
async def job_status(job_id: str):
    job = _job_or_404(job_id)
    counts: dict[str, int] = {}
    for item in job["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    completed = sum(v for k, v in counts.items() if k not in ("pending", "running"))
    return {**job, "completed": completed, "counts": counts}


@api.get("/results/{job_id}", response_model=JobResults)
async def job_results(job_id: str):
    job = _job_or_404(job_id)
    done = [i["doi"] for i in job["items"] if i["status"] in ("complete", "partial")]
    return {"job_id": job_id, "status": job["status"],
            "items": [flatten(r) for r in db.get_papers_by_dois(done)]}


@api.get("/stats")
async def stats():
    return db.stats()


app.include_router(api)

# ------------------------------------------------------------------ frontend (UI/ from the frontend branch)
# The pages live on the frontend branch. After the branches are merged, UI/ sits next to
# backend/ and is served here (on Vercel, from its CDN). Without it, only the API runs.
if settings.frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="ui")
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend():
        return JSONResponse({"message": "Research Base API is running. The pages (UI/) come from the "
                                        "frontend branch; API docs: /docs"})
