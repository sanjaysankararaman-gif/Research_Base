"""Google Scholar — optional, currently NOT queried.

Google Scholar has no official public API, and its terms of service prohibit
automated querying. Scraping it would require working around CAPTCHAs and
anti-bot protection, which this project deliberately does not do.

This module therefore always reports Google Scholar as "unavailable" with an
explanation, so the UI and stored records say plainly that no Google Scholar
data was retrieved — instead of showing empty or invented values.

If the department later obtains a permitted integration (for example a
licensed data feed), implement it here by:
  1. replacing `fetch` with a real call that returns SourceResult(status=OK, data={...})
     using the same normalized field names as the other services
     (e.g. "citation_count");
  2. adding "google_scholar" to the relevant metric definitions in
     processing/metric_mapper.py.
No other module needs to change.
"""
from __future__ import annotations

from .base import UNAVAILABLE, SourceClient, SourceResult

REASON = (
    "Not retrieved: Google Scholar has no official API and its terms prohibit automated "
    "access. No permitted integration is configured, so no Google Scholar data is included."
)


class GoogleScholarClient(SourceClient):
    name = "google_scholar"
    label = "Google Scholar"
    enabled = False

    async def fetch(self, doi: str) -> SourceResult:
        return SourceResult(self.name, UNAVAILABLE, message=REASON)
