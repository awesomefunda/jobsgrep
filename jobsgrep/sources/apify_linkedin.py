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
        """Best-effort input covering common LinkedIn-actor field names."""
        return {
            "keyword": term,
            "title": term,
            "searchQuery": term,
            "location": location,
            "rows": self.settings.apify_results_per_term,
            "limit": self.settings.apify_results_per_term,
            "maxItems": self.settings.apify_results_per_term,
        }

    def _row_to_job(self, row: dict, query: ParsedQuery) -> RawJob | None:
        if not isinstance(row, dict):
            return None

        def pick(*keys):
            for k in keys:
                v = row.get(k)
                if v:
                    return v
            return ""

        title = str(pick("title", "jobTitle", "position"))
        if not title:
            return None
        company = str(pick("companyName", "company", "employer")) or "Unknown"
        location = str(pick("location", "jobLocation", "place"))
        link = str(pick("jobUrl", "url", "link", "applyUrl"))
        desc = str(pick("description", "descriptionText", "jobDescription"))[:2000]
        posted = str(pick("postedAt", "postedTime", "publishedAt", "date"))[:10]

        rj = RawJob(
            id=job_id(company, title, location),
            title=title,
            company=company,
            location=location,
            remote="remote" in f"{title} {location}".lower(),
            url=link,
            description=desc,
            date_posted=posted,
            source="apify:linkedin",
            source_type=DataSourceType.OFFICIAL_API,
        )
        return rj if self._keyword_match(rj, query) else None
