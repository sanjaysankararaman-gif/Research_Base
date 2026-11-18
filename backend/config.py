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
    sjr_csv_path: Path | None = field(
        default_factory=lambda: _path("SJR_CSV_PATH", "") if os.getenv("SJR_CSV_PATH") else None)
    impact_factor_csv_path: Path | None = field(
        default_factory=lambda: _path("IMPACT_FACTOR_CSV_PATH", "") if os.getenv("IMPACT_FACTOR_CSV_PATH") else None)

    # Browser origins allowed to call the API ("*" = any, fine for local development)
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()])

    excel_path: Path = field(default_factory=lambda: _path("EXCEL_PATH", "data/research_papers.xlsx"))
    database_path: Path = field(default_factory=lambda: _path("DATABASE_PATH", "data/research_data.db"))
    log_dir: Path = field(default_factory=lambda: _path("LOG_DIR", "logs"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    request_timeout: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT_SECONDS", 20.0))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    max_retry_wait: float = field(default_factory=lambda: _float("MAX_RETRY_WAIT_SECONDS", 30.0))

    crossref_min_interval: float = field(default_factory=lambda: _float("CROSSREF_MIN_INTERVAL", 0.1))
    openalex_min_interval: float = field(default_factory=lambda: _float("OPENALEX_MIN_INTERVAL", 0.15))
    semantic_scholar_min_interval: float = field(
        default_factory=lambda: _float("SEMANTIC_SCHOLAR_MIN_INTERVAL", 1.0)
    )

    max_dois_per_request: int = field(default_factory=lambda: _int("MAX_DOIS_PER_REQUEST", 200))
    doi_concurrency: int = field(default_factory=lambda: _int("DOI_CONCURRENCY", 3))

    frontend_dir: Path = PROJECT_ROOT / "UI"  # the team's frontend (frontend branch)

    @property
    def user_agent(self) -> str:
        ua = f"ResearchBase/{self.version} (academic metadata tool)"
        if self.contact_email:
            ua += f" mailto:{self.contact_email}"
        return ua


settings = Settings()
