"""Persistence layer: SQLite locally, PostgreSQL when DATABASE_URL is set.

All SQL lives in this module so the rest of the application never touches the
database directly. The same SQL runs on both databases: the schema uses only
portable column types (TEXT / INTEGER / REAL), ISO-8601 UTC timestamps stored
as TEXT, and `?` placeholders that are translated for PostgreSQL.

* Local development: a SQLite file (DATABASE_PATH, default data/research_data.db).
* Hosted Postgres (e.g. Supabase): set DATABASE_URL to its connection string.
  Without it, on Vercel the SQLite file lives in /tmp, so the library resets
  whenever Vercel restarts the app.

Tables
------
papers          one row per normalized DOI: integer id, standardized metadata + status
metric_values   one row per (doi, metric, source): every value with its
                source and retrieval timestamp. Values from different sources
                are kept side by side, never merged.
source_fetches  one row per (doi, source): outcome of the last call to each
                academic source (ok / not_found / error / unavailable)
jobs            extraction jobs started through POST /extract
job_items       per-DOI progress within a job
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import settings
from .timeutil import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,  -- "paperId" used by the frontend
    doi              TEXT NOT NULL UNIQUE,
    title            TEXT,
    publication_type TEXT,          -- Journal | Conference | Book chapter | Preprint | Other
    authors_json     TEXT,          -- JSON list of author names
    journal          TEXT,
    publisher        TEXT,
    publication_year INTEGER,
    publication_date TEXT,
    status           TEXT NOT NULL, -- complete | partial | failed | not_found
    record_json      TEXT NOT NULL, -- full standardized record (see data_merger)
    missing_json     TEXT,          -- JSON list of requested metrics that are unavailable
    error_json       TEXT,          -- JSON list of source errors
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_values (
    doi           TEXT NOT NULL,
    metric_key    TEXT NOT NULL,
    source        TEXT NOT NULL,
    value_json    TEXT,             -- JSON-encoded value; NULL when unavailable
    available     INTEGER NOT NULL, -- 1 = value retrieved (may legitimately be 0), 0 = unavailable
    reason        TEXT,             -- why unavailable
    retrieved_at  TEXT NOT NULL,
    PRIMARY KEY (doi, metric_key, source),
    FOREIGN KEY (doi) REFERENCES papers(doi) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_fetches (
    doi           TEXT NOT NULL,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL,    -- ok | not_found | error | unavailable
    message       TEXT,
    http_status   INTEGER,
    retrieved_at  TEXT NOT NULL,
    PRIMARY KEY (doi, source),
    FOREIGN KEY (doi) REFERENCES papers(doi) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    status        TEXT NOT NULL,    -- queued | running | completed
    total         INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    finished_at   TEXT,
    invalid_json  TEXT,             -- inputs rejected by validation
    duplicates_json TEXT            -- inputs that were duplicates in the same request
);

CREATE TABLE IF NOT EXISTS job_items (
    job_id        TEXT NOT NULL,
    position      INTEGER NOT NULL,
    input         TEXT NOT NULL,
    doi           TEXT NOT NULL,
    status        TEXT NOT NULL,    -- pending | running | complete | partial | failed | not_found
    sources_json  TEXT,             -- {source: state}
    message       TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (job_id, position),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_cache (
    doi           TEXT NOT NULL,
    source        TEXT NOT NULL,
    data_json     TEXT NOT NULL,    -- last successful parsed response from this source
    retrieved_at  TEXT NOT NULL,
    PRIMARY KEY (doi, source)
);

CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
CREATE INDEX IF NOT EXISTS idx_papers_type ON papers(publication_type);
CREATE INDEX IF NOT EXISTS idx_papers_updated ON papers(updated_at);
CREATE INDEX IF NOT EXISTS idx_job_items_doi ON job_items(doi);
"""

# PostgreSQL version of the schema: identical except for the auto-increment id.
PG_SCHEMA = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")

log = logging.getLogger("extractor.database")

_lock = threading.Lock()
_db_path: Path | None = None
_initialized = False


def using_postgres() -> bool:
    return bool(settings.database_url)


def storage_description() -> str:
    if using_postgres():
        return "postgres (permanent)"
    if settings.on_vercel:
        return "sqlite in /tmp (TEMPORARY: resets when Vercel restarts the app; set DATABASE_URL)"
    return f"sqlite ({_db_path or settings.database_path})"


def init_db(path: Path | None = None) -> None:
    """Create the schema. Safe to call repeatedly."""
    global _db_path, _initialized
    if using_postgres() and path is None:
        _initialized = True
        with connect() as conn:
            conn.executescript(PG_SCHEMA)
        return
    _db_path = Path(path or settings.database_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    _initialized = True
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    if not _initialized:
        init_db()
    if using_postgres() and _db_path is None:
        with _pg_connect() as conn:
            yield conn
        return
    conn = sqlite3.connect(_db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        with _lock:
            yield conn
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- PostgreSQL

class _PgRow(tuple):
    """Row usable like sqlite3.Row: row[0], row["column"], dict(row)."""

    def __new__(cls, values, names):
        row = super().__new__(cls, values)
        row._names = names
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._names.index(key))
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: _PgRow(values, names)


_PLACEHOLDER = re.compile(r"\?")


class _PgConnection:
    """Wraps a psycopg connection with the small part of the sqlite3 API used here."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor(row_factory=_row_factory)
        cur.execute(_PLACEHOLDER.sub("%s", sql), tuple(params) if params else None)
        return cur

    def executemany(self, sql: str, seq):
        cur = self._conn.cursor()
        cur.executemany(_PLACEHOLDER.sub("%s", sql), [tuple(p) for p in seq])
        return cur

    def executescript(self, script: str):
        # Drop "-- comments" first: they may contain ";".
        script = "\n".join(line.split("--", 1)[0] for line in script.splitlines())
        for statement in (s.strip() for s in script.split(";")):
            if statement:
                self._conn.execute(statement)


_pg_conn = None
_pg_last_used = 0.0


def _pg_open():
    import psycopg  # only needed when DATABASE_URL is set

    # prepare_threshold=None: no server-side prepared statements, which connection
    # poolers (e.g. Supabase's pooler on port 6543) do not support.
    return psycopg.connect(settings.database_url, connect_timeout=15, prepare_threshold=None)


@contextmanager
def _pg_connect():
    """One connection per process, reused across requests and re-opened if it dropped.

    Hosted Postgres (e.g. Supabase) closes idle connections, so a connection unused for
    a while is checked before use.
    """
    global _pg_conn, _pg_last_used
    with _lock:
        if _pg_conn is not None and (_pg_conn.closed or _pg_conn.broken):
            _pg_conn = None
        if _pg_conn is not None and time.monotonic() - _pg_last_used > 60:
            try:
                _pg_conn.execute("SELECT 1")
                _pg_conn.commit()
            except Exception:
                log.info("Postgres connection was closed by the server; reconnecting")
                try:
                    _pg_conn.close()
                except Exception:
                    pass
                _pg_conn = None
        if _pg_conn is None:
            _pg_conn = _pg_open()
        conn = _pg_conn
        try:
            yield _PgConnection(conn)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                _pg_conn = None
            raise
        finally:
            _pg_last_used = time.monotonic()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(text: str | None, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- papers

def save_paper(record: dict[str, Any]) -> int:
    """Upsert a standardized record (output of data_merger.merge_sources)."""
    doi = record["doi"]
    now = utc_now()
    core = record.get("core", {})

    def core_value(key: str) -> Any:
        return (core.get(key) or {}).get("value")

    with connect() as conn:
        existing = conn.execute("SELECT created_at FROM papers WHERE doi = ?", (doi,)).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT INTO papers (doi, title, publication_type, authors_json, journal, publisher,
                                publication_year, publication_date, status, record_json, missing_json,
                                error_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doi) DO UPDATE SET
                title=excluded.title, publication_type=excluded.publication_type,
                authors_json=excluded.authors_json,
                journal=excluded.journal, publisher=excluded.publisher,
                publication_year=excluded.publication_year,
                publication_date=excluded.publication_date, status=excluded.status,
                record_json=excluded.record_json, missing_json=excluded.missing_json,
                error_json=excluded.error_json, updated_at=excluded.updated_at
            """,
            (
                doi,
                core_value("title"),
                core_value("publication_type"),
                _dumps(core_value("authors") or []),
                core_value("journal"),
                core_value("publisher"),
                core_value("publication_year"),
                core_value("publication_date"),
                record["status"],
                _dumps(record),
                _dumps(record.get("incomplete_fields", [])),
                _dumps(record.get("errors", [])),
                created_at,
                now,
            ),
        )
        # Replace metric rows for this DOI with the fresh retrieval.
        conn.execute("DELETE FROM metric_values WHERE doi = ?", (doi,))
        for metric in record.get("metrics", {}).values():
            for sv in metric.get("values", []):
                conn.execute(
                    """INSERT INTO metric_values
                       (doi, metric_key, source, value_json, available, reason, retrieved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        doi,
                        metric["key"],
                        sv["source"],
                        _dumps(sv["value"]) if sv["available"] else None,
                        1 if sv["available"] else 0,
                        sv.get("reason"),
                        sv.get("retrieved_at") or now,
                    ),
                )
        conn.execute("DELETE FROM source_fetches WHERE doi = ?", (doi,))
        for src in record.get("sources", {}).values():
            conn.execute(
                """INSERT INTO source_fetches (doi, source, status, message, http_status, retrieved_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    doi,
                    src["source"],
                    src["status"],
                    src.get("message"),
                    src.get("http_status"),
                    src.get("retrieved_at") or now,
                ),
            )
        return conn.execute("SELECT id FROM papers WHERE doi = ?", (doi,)).fetchone()[0]


def get_paper(doi: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchone()
    return _row_to_record(row)


def _row_to_record(row) -> dict[str, Any] | None:
    if not row:
        return None
    record = _loads(row["record_json"], {})
    record["id"] = row["id"]
    record["stored_at"] = row["created_at"]
    record["updated_at"] = row["updated_at"]
    return record


def all_papers() -> list[dict[str, Any]]:
    """Every stored paper, oldest first (used to build the Excel file)."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM papers ORDER BY id").fetchall()
    return [_row_to_record(r) for r in rows]


def get_paper_by_id(paper_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return _row_to_record(row)


def delete_paper(paper_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        return cur.rowcount > 0


def get_papers_by_dois(dois: list[str]) -> list[dict[str, Any]]:
    return [p for d in dois if (p := get_paper(d)) is not None]


SORTABLE = {
    "id": "id",
    "publication_type": "publication_type",
    "doi": "doi",
    "title": "title",
    "journal": "journal",
    "publication_year": "publication_year",
    "status": "status",
    "updated_at": "updated_at",
}


def list_papers(
    *,
    search: str | None = None,
    status: str | None = None,
    publication_type: str | None = None,
    sort: str = "updated_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    where, params = [], []
    if search:
        like = f"%{search.lower()}%"
        where.append(
            "(lower(doi) LIKE ? OR lower(coalesce(title,'')) LIKE ? OR "
            "lower(coalesce(authors_json,'')) LIKE ? OR lower(coalesce(journal,'')) LIKE ?)"
        )
        params += [like] * 4
    if status:
        where.append("status = ?")
        params.append(status)
    if publication_type:
        where.append("publication_type = ?")
        params.append(publication_type)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    column = SORTABLE.get(sort, "updated_at")
    direction = "ASC" if order.lower() == "asc" else "DESC"
    # NULLs always last regardless of direction.
    order_by = f"({column} IS NULL), {column} {direction}, doi ASC"
    offset = (max(page, 1) - 1) * page_size
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM papers {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, record_json, created_at, updated_at FROM papers {clause} "
            f"ORDER BY {order_by} LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
    return [_row_to_record(r) for r in rows], total


def get_metric_rows(doi: str) -> list[dict[str, Any]]:
    """Raw metric rows straight from storage (used by tests / integrations)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM metric_values WHERE doi = ? ORDER BY metric_key, source", (doi,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["value"] = _loads(d.pop("value_json"))
        d["available"] = bool(d["available"])
        out.append(d)
    return out


def stats() -> dict[str, int]:
    with connect() as conn:
        counts = dict(
            conn.execute("SELECT status, COUNT(*) FROM papers GROUP BY status").fetchall()
        )
        processed = conn.execute(
            "SELECT COUNT(*) FROM job_items WHERE status NOT IN ('pending','running')"
        ).fetchone()[0]
    total = sum(counts.values())
    return {
        "total_records": total,
        "total_processed": processed,
        "successful": counts.get("complete", 0),
        "partial": counts.get("partial", 0),
        "failed": counts.get("failed", 0) + counts.get("not_found", 0),
        "incomplete_or_failed": counts.get("partial", 0)
        + counts.get("failed", 0)
        + counts.get("not_found", 0),
    }


# --------------------------------------------------------------------------- source cache
# Last good answer from each source. If a source is temporarily down or rate limited
# on a later lookup, its earlier answer is reused (with its original date) instead of
# making the stored record worse.

def cache_source(doi: str, source: str, data: dict[str, Any], retrieved_at: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO source_cache (doi, source, data_json, retrieved_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(doi, source) DO UPDATE SET data_json=excluded.data_json, retrieved_at=excluded.retrieved_at",
            (doi, source, _dumps(data), retrieved_at),
        )


def cached_source(doi: str, source: str) -> tuple[dict[str, Any], str] | None:
    with connect() as conn:
        row = conn.execute("SELECT data_json, retrieved_at FROM source_cache WHERE doi = ? AND source = ?",
                           (doi, source)).fetchone()
    return (_loads(row["data_json"], {}), row["retrieved_at"]) if row else None


# --------------------------------------------------------------------------- jobs

def create_job(
    job_id: str,
    items: list[tuple[str, str]],
    sources: list[str],
    invalid: list[dict[str, str]],
    duplicates: list[dict[str, str]],
) -> None:
    now = utc_now()
    initial_sources = _dumps({s: "pending" for s in sources})
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, total, created_at, invalid_json, duplicates_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, "queued", len(items), now, _dumps(invalid), _dumps(duplicates)),
        )
        conn.executemany(
            "INSERT INTO job_items (job_id, position, input, doi, status, sources_json, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            [(job_id, i, raw, doi, initial_sources, now) for i, (raw, doi) in enumerate(items)],
        )


def set_job_status(job_id: str, status: str) -> None:
    with connect() as conn:
        finished = utc_now() if status == "completed" else None
        conn.execute(
            "UPDATE jobs SET status = ?, finished_at = coalesce(?, finished_at) WHERE job_id = ?",
            (status, finished, job_id),
        )


def update_job_item(
    job_id: str,
    position: int,
    *,
    status: str | None = None,
    sources: dict[str, str] | None = None,
    message: str | None = None,
) -> None:
    sets, params = ["updated_at = ?"], [utc_now()]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if sources is not None:
        sets.append("sources_json = ?")
        params.append(_dumps(sources))
    if message is not None:
        sets.append("message = ?")
        params.append(message)
    with connect() as conn:
        conn.execute(
            f"UPDATE job_items SET {', '.join(sets)} WHERE job_id = ? AND position = ?",
            [*params, job_id, position],
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not job:
            return None
        items = conn.execute(
            "SELECT * FROM job_items WHERE job_id = ? ORDER BY position", (job_id,)
        ).fetchall()
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "total": job["total"],
        "created_at": job["created_at"],
        "finished_at": job["finished_at"],
        "invalid": _loads(job["invalid_json"], []),
        "duplicates": _loads(job["duplicates_json"], []),
        "items": [
            {
                "position": r["position"],
                "input": r["input"],
                "doi": r["doi"],
                "status": r["status"],
                "sources": _loads(r["sources_json"], {}),
                "message": r["message"],
                "updated_at": r["updated_at"],
            }
            for r in items
        ],
    }


def mark_interrupted_jobs() -> int:
    """On startup, close out jobs left running by a previous process.

    Only for a local single-process setup: with a shared Postgres database, other
    live instances may be running jobs right now.
    """
    if using_postgres():
        return 0
    with connect() as conn:
        cur = conn.execute(
            "UPDATE job_items SET status = 'failed', message = 'Interrupted: server restarted', "
            "updated_at = ? WHERE status IN ('pending','running')",
            (utc_now(),),
        )
        conn.execute(
            "UPDATE jobs SET status = 'completed', finished_at = ? WHERE status IN ('queued','running')",
            (utc_now(),),
        )
        return cur.rowcount
