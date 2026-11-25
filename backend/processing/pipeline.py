"""Extraction pipeline: runs a job in the background.

For each DOI in a job, all sources are queried concurrently. Job progress
(per DOI and per source) is written to SQLite as each source finishes, so
GET /status/{job_id} reflects live progress. A limited number of DOIs are
processed at the same time (DOI_CONCURRENCY) to respect source rate limits.
"""
from __future__ import annotations

import asyncio
import logging

from .. import database as db
from ..config import settings
from ..services import OK, SourceClient, SourceResult
from .retrieval import retrieve
from ..integrations.database.supabase_sync import sync_to_supabase

log = logging.getLogger("extractor.pipeline")

_SOURCE_STATE = {"ok": "ok", "not_found": "not_found", "error": "error", "unavailable": "unavailable"}


class _DatabaseCache:
    """Source cache backed by the source_cache table."""

    def get(self, doi, source):
        return db.cached_source(doi, source)

    def put(self, doi, source, data, retrieved_at):
        db.cache_source(doi, source, data, retrieved_at)


async def extract_doi(
    doi: str,
    clients: dict[str, SourceClient],
    on_source_done=None,
) -> dict:
    """Retrieve one DOI (see retrieval.retrieve), store it, and return the record."""
    record = await retrieve(doi, clients, on_source_done, cache=_DatabaseCache())

    # Only records with data go into the library. A DOI no source knows, or a
    # total outage, must not create an empty entry or overwrite good stored data.
    if record["status"] in ("complete", "partial"):
        record["id"] = db.save_paper(record)
        await asyncio.to_thread(sync_to_supabase, record)
    else:
        existing = db.get_paper(doi)
        record["id"] = existing["id"] if existing else None
    log.info(
        "Extracted %s: %s (%s)",
        doi,
        record["status"],
        ", ".join(f"{n}={r['status']}" for n, r in record["sources"].items()),
    )
    return record


async def run_job(job_id: str, items: list[tuple[str, str]], clients: dict[str, SourceClient]) -> None:
    db.set_job_status(job_id, "running")
    semaphore = asyncio.Semaphore(max(1, settings.doi_concurrency))

    async def process(position: int, doi: str) -> None:
        async with semaphore:
            states = {name: "running" for name in clients}
            db.update_job_item(job_id, position, status="running", sources=states)

            def on_source_done(name: str, result: SourceResult) -> None:
                states[name] = _SOURCE_STATE.get(result.status, result.status)
                db.update_job_item(job_id, position, sources=dict(states))

            try:
                record = await extract_doi(doi, clients, on_source_done)
                db.update_job_item(job_id, position, status=record["status"], message=_message(record))
            except Exception:
                # Never leave a DOI stuck on "running".
                log.exception("Extraction crashed for %s", doi)
                db.update_job_item(
                    job_id, position, status="failed",
                    message="Internal error while processing this DOI (see server log)",
                )

    try:
        await asyncio.gather(*(process(i, doi) for i, (_raw, doi) in enumerate(items)))
    finally:
        from ..integrations.excel_export import safe_update
        safe_update()
        db.set_job_status(job_id, "completed")


def _message(record: dict) -> str:
    status = record["status"]
    ok = [s for s, v in record["sources"].items() if v["status"] == OK]
    if status == "not_found":
        return "No source has a record for this DOI. Check that the DOI is correct."
    if status == "failed":
        return "All sources failed: " + "; ".join(e["message"] for e in record["errors"])
    parts = [f"Data from {len(ok)} source(s)"]
    if record["errors"]:
        parts.append("failed: " + "; ".join(e["message"] for e in record["errors"]))
    if record["missing_required"]:
        parts.append("missing: " + ", ".join(record["missing_required"]))
    return " — ".join(parts)
