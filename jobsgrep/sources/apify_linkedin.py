"""Apify actor source — LinkedIn jobs via a managed scraper.

Apify runs LinkedIn job scrapers as hosted "actors" and handles proxies/rate
limits for you. This calls the actor's synchronous run endpoint and maps the
result rows to RawJob. Works in any deploy mode (it's an API call, not local
scraping) but costs Apify credits ($5/mo free tier).

Setup:
  1. Pick a LinkedIn jobs actor in the Apify store and note its slug
     (e.g. "bebity~linkedin-jobs-scraper").
  2. Set APIFY_TOKEN (and optionally APIFY_ACTOR) in your env.

NOTE: actor input/output schemas vary between actors. The input built below and
the field mapping cover common shapes; adjust `_actor_input` / `_row_to_job`
for the specific actor you choose. Without APIFY_TOKEN this source is skipped.
"""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.apify")

_CORPUS_TERMS = [
    "software engineer", "senior software engineer", "data engineer",
    "machine learning engineer", "devops engineer", "product manager",
    "engineering manager", "security engineer",
]


class ApifyLinkedInSource(BaseSource):
    source_name = "apify"

    RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        token = self.settings.apify_token
        if not token:
            logger.info("apify: no APIFY_TOKEN set, skipping")
            return []

        actor = self.settings.apify_actor
        location = query.locations[0] if query.locations else self.settings.apify_location
        terms = (query.titles + query.title_variations)[:3] if query.titles else _CORPUS_TERMS
        url = self.RUN_URL.format(actor=actor)

        all_jobs: list[RawJob] = []
        seen: set[str] = set()
        for term in terms:
            try:
                resp = await self._post(
                    url,
                    params={"token": token},
                    json=self._actor_input(term, location),
                    timeout=120,
                )
                if resp.status_code not in (200, 201):
                    logger.debug("apify '%s' HTTP %d: %s", term, resp.status_code, resp.text[:120])
                    continue
                rows = resp.json()
            except Exception as e:
                logger.warning("apify term '%s' failed: %s", term, e)
                continue

            if not isinstance(rows, list):
                continue
            for row in rows:
                rj = self._row_to_job(row, query)
                if rj and rj.id not in seen:
                    seen.add(rj.id)
                    all_jobs.append(rj)

        logger.info("apify: %d unique jobs from %d terms", len(all_jobs), len(terms))
        return all_jobs

    def _actor_input(self, term: str, location: str) -> dict:
        """Input for harvestapi/linkedin-job-search (with generic fallbacks)."""
        n = self.settings.apify_results_per_term
        return {
            # harvestapi schema
            "jobTitles": [term],
            "locations": [location],
            "maxItems": n,
            "sortBy": "date",
            # tolerated by some other actors
            "keyword": term,
            "location": location,
            "rows": n,
        }

    @staticmethod
    def _flatten(value, *keys) -> str:
        """Pull a string from a value that may be a str or a nested dict."""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for k in keys:
                v = value.get(k)
                if isinstance(v, str) and v:
                    return v
        return ""

    def _row_to_job(self, row: dict, query: ParsedQuery) -> RawJob | None:
        if not isinstance(row, dict):
            return None

        title = str(row.get("title") or row.get("jobTitle") or "")
        if not title:
            return None

        # company / location may be nested objects (harvestapi) or flat strings.
        company = self._flatten(row.get("company"), "name", "companyName") \
            or str(row.get("companyName") or "") or "Unknown"
        location = self._flatten(row.get("location"), "text", "name", "city", "linkedinText") \
            or str(row.get("jobLocation") or "")
        link = str(row.get("linkedinUrl") or row.get("jobUrl") or row.get("url")
                   or row.get("easyApplyUrl") or "")
        desc = str(row.get("descriptionText") or row.get("description") or "")[:2000]
        posted = str(row.get("postedDate") or row.get("postedAt") or row.get("date") or "")[:10]

        wt = str(row.get("workplaceType") or "")
        remote = "remote" in f"{title} {location} {wt}".lower()

        rj = RawJob(
            id=job_id(company, title, location),
            title=title,
            company=company,
            location=location,
            remote=remote,
            url=link,
            description=desc,
            date_posted=posted,
            source="apify:linkedin",
            source_type=DataSourceType.OFFICIAL_API,
        )
        return rj if self._keyword_match(rj, query) else None
