"""Adzuna Jobs API — licensed aggregator (free developer tier).

Adzuna aggregates listings from thousands of boards under its own API license, so
it's the highest-breadth *legal* source available. Requires free credentials:
register at https://developer.adzuna.com and set ADZUNA_APP_ID + ADZUNA_APP_KEY.
Without keys this source silently returns nothing.

For the corpus we pull the broad `it-jobs` category across several pages rather
than per-query, matching the query-independent prefetch design.
"""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.adzuna")


class AdzunaSource(BaseSource):
    source_name = "adzuna"

    BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        app_id = self.settings.adzuna_app_id
        app_key = self.settings.adzuna_app_key
        if not app_id or not app_key:
            logger.info("adzuna: no ADZUNA_APP_ID/ADZUNA_APP_KEY set, skipping")
            return []

        country = (self.settings.adzuna_country or "us").lower()
        max_pages = max(1, self.settings.adzuna_max_pages)

        base_params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": 50,
            "category": "it-jobs",
            "max_days_old": 30,
            "content-type": "application/json",
        }

        sem = asyncio.Semaphore(4)

        async def fetch_page(page: int) -> list[RawJob]:
            async with sem:
                return await self._fetch_page(country, page, base_params, query)

        tasks = [fetch_page(p) for p in range(1, max_pages + 1)]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[RawJob] = []
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    async def _fetch_page(self, country: str, page: int, params: dict, query: ParsedQuery) -> list[RawJob]:
        url = self.BASE_URL.format(country=country, page=page)
        try:
            resp = await self._get(url, params=params)
            if resp.status_code != 200:
                logger.debug("adzuna page %d HTTP %d", page, resp.status_code)
                return []
            data = resp.json()
        except Exception as e:
            logger.debug("adzuna page %d failed: %s", page, e)
            return []

        jobs = []
        for j in data.get("results", []):
            title = j.get("title", "")
            company = (j.get("company", {}) or {}).get("display_name", "") or "Unknown"
            location = (j.get("location", {}) or {}).get("display_name", "")
            created = j.get("created", "")
            smin = j.get("salary_min")
            smax = j.get("salary_max")

            rj = RawJob(
                id=job_id(company, title, location),
                title=title,
                company=company,
                location=location,
                remote="remote" in location.lower() or "remote" in title.lower(),
                url=j.get("redirect_url", ""),
                description=(j.get("description", "") or "")[:2000],
                salary_min=float(smin) if smin else None,
                salary_max=float(smax) if smax else None,
                date_posted=created[:10] if created else "",
                source="adzuna",
                source_type=DataSourceType.OFFICIAL_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
