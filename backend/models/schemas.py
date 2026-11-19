"""Request / response schemas for the HTTP API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PaperRequest(BaseModel):
    """Body for POST /api/papers (Home page search)."""

    doi: str = Field(..., min_length=1, max_length=400, description="DOI in any common form")
    refresh: bool = Field(default=False, description="Re-query sources even if the DOI is already stored")


class ExtractRequest(BaseModel):
    """Either `dois` (a list), `doi` (one value), or `text` (bulk, one per line) — or a mix."""

    doi: str | None = Field(default=None, max_length=400, description="A single DOI")
    dois: list[str] = Field(default_factory=list, description="A list of DOIs")
    text: str | None = Field(default=None, max_length=200_000, description="Bulk input, one DOI per line")
    refresh: bool = Field(
        default=True,
        description="Re-query sources for DOIs already stored. If false, stored records are reused.",
    )

    @field_validator("dois")
    @classmethod
    def _limit_items(cls, v: list[str]) -> list[str]:
        if len(v) > 5000:
            raise ValueError("Too many items")
        return [str(x)[:400] for x in v]


class InvalidInput(BaseModel):
    input: str
    reason: str


class DuplicateInput(BaseModel):
    input: str
    doi: str


class ExtractResponse(BaseModel):
    job_id: str | None
    status: Literal["queued", "completed"]
    accepted: int
    dois: list[str]
    reused: list[str] = Field(description="DOIs answered from storage because refresh=false")
    invalid: list[InvalidInput]
    duplicates: list[DuplicateInput]
    status_url: str | None
    results_url: str | None


class JobItem(BaseModel):
    position: int
    input: str
    doi: str
    status: str
    sources: dict[str, str]
    message: str | None
    updated_at: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    total: int
    completed: int
    counts: dict[str, int]
    created_at: str
    finished_at: str | None
    invalid: list[InvalidInput]
    duplicates: list[DuplicateInput]
    items: list[JobItem]






class JobResults(BaseModel):
    job_id: str
    status: str
    items: list[dict[str, Any]]




