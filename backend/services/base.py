"""Shared plumbing for academic source clients.

Every source module subclasses `SourceClient` and implements `fetch(doi)`,
which returns a `SourceResult`. This class provides:

* one shared `httpx.AsyncClient` per source with a request timeout
* per-source rate limiting (minimum interval between requests)
* retries with exponential backoff for timeouts, network errors, HTTP 429 and
  HTTP 5xx, honouring the `Retry-After` header when the server sends one, within
  a time budget per request (SOURCE_TIME_BUDGET_SECONDS)
* consistent error classification so one failing source never breaks the
  others (partial success)
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings
from ..timeutil import utc_now

log = logging.getLogger("extractor.sources")

# Source result statuses
OK = "ok"                    # the source returned a record for this DOI
NOT_FOUND = "not_found"      # the source answered, but has no record for this DOI
ERROR = "error"              # the source could not be reached / failed after retries
UNAVAILABLE = "unavailable"  # the source is not queried (no permitted integration / disabled)


@dataclass
class SourceResult:
    source: str
    status: str
    retrieved_at: str = field(default_factory=utc_now)
    data: dict[str, Any] = field(default_factory=dict)  # normalized fields extracted from the response
    message: str | None = None
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "retrieved_at": self.retrieved_at,
            "message": self.message,
            "http_status": self.http_status,
        }


class SourceError(Exception):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


class NotFound(Exception):
    pass


class SourceClient:
    name: str = "base"
    label: str = "Base"
    base_url: str = ""
    min_interval: float = 0.0
    max_retries: int | None = None      # None = use MAX_RETRIES from settings
    timeout: float | None = None        # None = use REQUEST_TIMEOUT_SECONDS from settings

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    # ------------------------------------------------------------------ hooks
    def headers(self) -> dict[str, str]:
        return {"User-Agent": settings.user_agent, "Accept": "application/json"}

    def default_params(self) -> dict[str, str]:
        return {}

    async def fetch(self, doi: str) -> SourceResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # ------------------------------------------------------------------ http
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers(),
                timeout=httpx.Timeout(self.timeout or settings.request_timeout),
                follow_redirects=True,
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._rate_lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET with retries. Raises NotFound for 404, SourceError for everything else."""
        merged = {**self.default_params(), **(params or {})}
        attempts = (self.max_retries if self.max_retries is not None else settings.max_retries) + 1
        last_error: SourceError | None = None
        started = time.monotonic()
        for attempt in range(1, attempts + 1):
            await self._throttle()
            left = settings.source_time_budget - (time.monotonic() - started)
            timeout = max(5.0, min(self.timeout or settings.request_timeout, left))
            try:
                response = await self.client.get(path, params=merged or None, timeout=timeout)
            except httpx.TimeoutException:
                last_error = SourceError(f"{self.label} timed out after {timeout:.0f}s")
                delay = None
            except httpx.HTTPError as exc:
                last_error = SourceError(f"Could not connect to {self.label} ({type(exc).__name__})")
                delay = None
            else:
                status = response.status_code
                if status == 200:
                    try:
                        return response.json()
                    except ValueError:   # cut-off or empty body: a network hiccup, so retry
                        last_error = SourceError(f"{self.label} returned an incomplete response", status)
                        delay = None
                elif status == 404:
                    raise NotFound()
                elif status == 429:
                    last_error = SourceError(f"{self.label} rate limit reached (HTTP 429)", status)
                    delay = self._retry_after(response)
                elif status >= 500:
                    last_error = SourceError(f"{self.label} server error (HTTP {status})", status)
                    delay = self._retry_after(response)
                elif status in (401, 403):
                    raise SourceError(
                        f"{self.label} refused the request (HTTP {status}); check the API key", status
                    )
                else:
                    raise SourceError(f"{self.label} rejected the request (HTTP {status})", status)

            if attempt < attempts:
                backoff = delay if delay is not None else (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                backoff = min(backoff, settings.max_retry_wait)
                # Stop retrying when the next attempt could not finish within the time budget.
                remaining = settings.source_time_budget - (time.monotonic() - started) - backoff
                if remaining < 5.0:
                    log.warning("%s: giving up on %s after %.0fs (time budget %.0fs)", self.label, path,
                                time.monotonic() - started, settings.source_time_budget)
                    break
                log.warning(
                    "%s attempt %d/%d failed for %s: %s — retrying in %.1fs",
                    self.label, attempt, attempts, path, last_error, backoff,
                )
                await asyncio.sleep(backoff)
        assert last_error is not None
        raise last_error

    async def safe_fetch(self, doi: str) -> SourceResult:
        """Run `fetch` and convert every failure into a SourceResult (never raises)."""
        try:
            return await self.fetch(doi)
        except NotFound:
            return SourceResult(
                self.name, NOT_FOUND, message=f"{self.label} has no record for this DOI", http_status=404
            )
        except SourceError as exc:
            log.error("%s failed for %s: %s", self.label, doi, exc)
            return SourceResult(self.name, ERROR, message=str(exc), http_status=exc.http_status)
        except Exception:  # unexpected parsing bug etc. — log trace, show generic message
            log.exception("Unexpected error while processing %s response for %s", self.label, doi)
            return SourceResult(
                self.name, ERROR, message=f"Unexpected error while reading the {self.label} response"
            )
