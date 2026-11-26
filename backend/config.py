"""Application configuration, read from environment variables (.env supported).

Secrets (API keys) live only here on the server side and are never sent to
the frontend.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name) or default)
    return p if p.is_absolute() else PROJECT_ROOT / p


# Vercel sets VERCEL=1. There, the project folder is read-only and only /tmp is
# writable, and /tmp is wiped whenever the function restarts.
ON_VERCEL = bool(os.getenv("VERCEL"))


def _optional_path(name: str, default: str | None) -> Path | None:
    """Path from the environment. Set to an empty value to switch the feature off."""
    if name in os.environ:
        return _path(name, "") if os.environ[name].strip() else None
    return _path(name, default) if default else None


def _default_sjr_file() -> Path | None:
    """Newest SCImago export committed under reference/ (e.g. reference/scimagojr_2025.csv)."""
    files = sorted((PROJECT_ROOT / "reference").glob("scimagojr*.csv"))
    return files[-1] if files else None


def _sjr_path() -> Path | None:
    if "SJR_CSV_PATH" in os.environ:
        return _optional_path("SJR_CSV_PATH", None)
    return _default_sjr_file()


def _list(name: str, default: str, sep: str = ",") -> tuple[str, ...]:
    return tuple(p.strip() for p in (os.getenv(name) or default).split(sep) if p.strip())


@dataclass(frozen=True)
class Settings:
    app_name: str = "Research Base - Data Retrieval API"
    version: str = "1.0.0"

    contact_email: str = field(default_factory=lambda: os.getenv("CONTACT_EMAIL", "").strip())
    semantic_scholar_api_key: str = field(
        default_factory=lambda: os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    )
    openalex_api_key: str = field(default_factory=lambda: os.getenv("OPENALEX_API_KEY", "").strip())
    scopus_api_key: str = field(default_factory=lambda: os.getenv("SCOPUS_API_KEY", "").strip())
    wos_api_key: str = field(default_factory=lambda: os.getenv("WOS_API_KEY", "").strip())

    # Journal-level files supplied by the department (see services/journal_rankings.py)
    # Quartile: SJR_CSV_PATH, or else the newest reference/scimagojr*.csv in the repository.
    sjr_csv_path: Path | None = field(default_factory=_sjr_path)
    impact_factor_csv_path: Path | None = field(
        default_factory=lambda: _optional_path("IMPACT_FACTOR_CSV_PATH", None))

    # Home institution (see processing/home_institution.py). An author counts as home when an
    # affiliation has one of these ROR IDs, or contains every word of one of the aliases.
    # The SSN aliases are on purpose: SNU Chennai's School of Engineering is the "SSN School of
    # Engineering", and sources often list its staff under SSN College of Engineering.
    home_institution_name: str = field(default_factory=lambda: (
        os.getenv("HOME_INSTITUTION_NAME") or "Shiv Nadar University, Chennai").strip())
    home_institution_rors: tuple[str, ...] = field(default_factory=lambda: _list(
        "HOME_INSTITUTION_ROR", "039rg7508,054psm803"))
    home_institution_aliases: tuple[str, ...] = field(default_factory=lambda: _list(
        "HOME_INSTITUTION_ALIASES",
        "Shiv Nadar University Chennai; Shiv Nadar Kalavakkam; SNU Chennai; Sivasubramaniya Nadar; "
        "SSN College of Engineering; SSN School of Engineering", sep=";"))
    # Home-institution faculty, one row per teacher with a fixed faculty_id.
    faculty_csv_path: Path = field(default_factory=lambda: _path("FACULTY_CSV_PATH", "reference/faculty.csv"))

    # Browser origins allowed to call the API ("*" = any, fine for local development)
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()])

    on_vercel: bool = ON_VERCEL

    # Storage. DATABASE_URL (or POSTGRES_URL): a hosted Postgres database such as Supabase
    # selects a hosted Postgres database, which keeps the library across restarts.
    # Without it, a local SQLite file is used.
    database_url: str = field(default_factory=lambda: (
        os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip())
    database_path: Path = field(default_factory=lambda: _path(
        "DATABASE_PATH", "/tmp/research_data.db" if ON_VERCEL else "data/research_data.db"))
    # Optional second copy in Supabase as normalized tables for reports
    # (backend/integrations/database/supabase_schema.sql). Not set: not written.
    supabase_db_url: str = field(default_factory=lambda: os.getenv("SUPABASE_DB_URL", "").strip())

    # Excel copy on disk (local use). On Vercel the file is built on demand for downloads.
    excel_path: Path | None = field(default_factory=lambda: _optional_path(
        "EXCEL_PATH", None if ON_VERCEL else "data/research_papers.xlsx"))

    # Log files (local use). On Vercel logs go to the Vercel dashboard instead.
    log_dir: Path | None = field(default_factory=lambda: _optional_path(
        "LOG_DIR", None if ON_VERCEL else "logs"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    request_timeout: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT_SECONDS", 20.0))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    max_retry_wait: float = field(default_factory=lambda: _float("MAX_RETRY_WAIT_SECONDS", 30.0))
    # Most time one source may spend on one request, retries included. Keeps a lookup well
    # inside the browser's patience and Vercel's 120-second limit when a source is rate limited.
    source_time_budget: float = field(default_factory=lambda: _float("SOURCE_TIME_BUDGET_SECONDS", 40.0))

    crossref_min_interval: float = field(default_factory=lambda: _float("CROSSREF_MIN_INTERVAL", 0.1))
    openalex_min_interval: float = field(default_factory=lambda: _float("OPENALEX_MIN_INTERVAL", 0.15))
    semantic_scholar_min_interval: float = field(
        default_factory=lambda: _float("SEMANTIC_SCHOLAR_MIN_INTERVAL", 1.0)
    )

    max_dois_per_request: int = field(default_factory=lambda: _int("MAX_DOIS_PER_REQUEST", 200))
    doi_concurrency: int = field(default_factory=lambda: _int("DOI_CONCURRENCY", 3))

    # The pages (UI/ from the frontend branch). Present after the branches are merged;
    # FRONTEND_DIR can point at another checkout of the frontend branch during development.
    frontend_dir: Path = field(default_factory=lambda: _path("FRONTEND_DIR", "UI"))

    @property
    def user_agent(self) -> str:
        ua = f"ResearchBase/{self.version} (academic metadata tool)"
        if self.contact_email:
            ua += f" mailto:{self.contact_email}"
        return ua


settings = Settings()
