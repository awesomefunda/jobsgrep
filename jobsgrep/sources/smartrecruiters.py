"""SmartRecruiters public Posting API (no auth required).

Each company exposes its open roles at:
  GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100

This is an official, documented public endpoint intended for job distribution —
no scraping, no ToS grey area. Unknown company identifiers simply 404 and are
skipped, so the default list is safe to extend freely.
"""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.smartrecruiters")

# SmartRecruiters company identifiers (the slug in jobs.smartrecruiters.com/<id>).
# Curated; 404s are skipped, so add freely.
DEFAULT_COMPANIES = [
    "Square", "Block", "Ubisoft", "Visa", "Equinix", "Bosch",
    "WeWork", "Twitch", "BendingSpoons", "Marqeta", "Brillio",
    "Celonis", "ClickUp", "Trustly",
]


class SmartRecruitersSource(BaseSource):
    source_name = "smartrecruiters"

    BASE_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        companies = list(dict.fromkeys(DEFAULT_COMPANIES + [
            c.strip() for c in query.target_companies
        ]))

        sem = asyncio.Semaphore(8)

        async def fetch_company(company: str) -> list[RawJob]:
            async with sem:
                return await self._fetch_company(company, query)

        tasks = [fetch_company(c) for c in companies]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[RawJob] = []
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    async def _fetch_company(self, company: str, query: ParsedQuery) -> list[RawJob]:
        url = self.BASE_URL.format(company=company)
        try:
            resp = await self._get(url, params={"limit": 100})
            if resp.status_code in (404, 400):
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("smartrecruiters company %s failed: %s", company, e)
            return []

        jobs = []
        for p in data.get("content", []):
            title = p.get("name", "")
            loc = p.get("location", {}) or {}
            city = loc.get("city", "")
            region = loc.get("region", "")
            country = loc.get("country", "")
            location = ", ".join(x for x in (city, region, country) if x)
            is_remote = bool(loc.get("remote", False))
            posting_id = p.get("id", "")
            url_apply = (
                p.get("ref")
                or f"https://jobs.smartrecruiters.com/{company}/{posting_id}"
            )
            released = p.get("releasedDate", "") or p.get("createdOn", "")

            rj = RawJob(
                id=job_id(company, title, location),
                title=title,
                company=company,
                location=location,
                remote=is_remote or "remote" in location.lower(),
                url=url_apply,
                date_posted=released[:10] if released else "",
                source="smartrecruiters",
                source_type=DataSourceType.PUBLIC_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
