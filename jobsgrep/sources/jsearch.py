"""JSearch — Google for Jobs via API (RapidAPI / OpenWeb Ninja).

JSearch returns real-time Google for Jobs results (which aggregate LinkedIn,
Indeed, Glassdoor, ZipRecruiter and company career pages) as structured JSON —
no scraping, works in every deploy mode. Free tier: 200 requests/month, no card.

Get a key at https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch and set
JSEARCH_API_KEY. Without a key this source returns nothing.
"""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.jsearch")

# Broad role terms used to build a corpus when no specific query is given.
_CORPUS_TERMS = [
    "software engineer", "senior software engineer", "data engineer",
    "machine learning engineer", "devops engineer", "security engineer",
    "frontend engineer", "backend engineer", "engineering manager",
    "product manager", "asic design engineer", "fpga engineer",
    "hardware engineer", "rtl design engineer",
]


class JSearchSource(BaseSource):
    source_name = "jsearch"

    BASE_URL = "https://jsearch.p.rapidapi.com/search"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        key = self.settings.jsearch_api_key
        if not key:
            logger.info("jsearch: no JSEARCH_API_KEY set, skipping")
            return []

        country = (self.settings.jsearch_country or "us").lower()
        num_pages = max(1, self.settings.jsearch_num_pages)
        headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}

        terms = (query.titles + query.title_variations)[:4] if query.titles else _CORPUS_TERMS
        location = query.locations[0] if query.locations else "United States"

        sem = asyncio.Semaphore(3)

        async def fetch_term(term: str) -> list[RawJob]:
            async with sem:
                return await self._fetch_term(term, location, country, num_pages, headers, query)

        batches = await asyncio.gather(*[fetch_term(t) for t in terms], return_exceptions=True)
        results: list[RawJob] = []
        seen: set[str] = set()
        for b in batches:
            if isinstance(b, list):
                for j in b:
                    if j.id not in seen:
                        seen.add(j.id)
                        results.append(j)
        logger.info("jsearch: %d unique jobs from %d terms", len(results), len(terms))
        return results

    async def _fetch_term(self, term, location, country, num_pages, headers, query) -> list[RawJob]:
        params = {
            "query": f"{term} in {location}",
            "page": "1",
            "num_pages": str(num_pages),
            "country": country,
            "date_posted": "month",
        }
        try:
            resp = await self._get(self.BASE_URL, params=params, headers=headers)
            if resp.status_code != 200:
                logger.debug("jsearch '%s' HTTP %d", term, resp.status_code)
                return []
            data = resp.json()
        except Exception as e:
            logger.debug("jsearch '%s' failed: %s", term, e)
            return []

        jobs = []
        for j in data.get("data", []):
            title = j.get("job_title", "")
            if not title:
                continue
            company = j.get("employer_name", "") or "Unknown"
            loc = ", ".join(
                x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x
            )
            ts = j.get("job_posted_at_timestamp")
            from datetime import datetime, timezone
            date_posted = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""
            )
            smin = j.get("job_min_salary")
            smax = j.get("job_max_salary")

            rj = RawJob(
                id=job_id(company, title, loc),
                title=title,
                company=company,
                location=loc,
                remote=bool(j.get("job_is_remote", False)),
                url=j.get("job_apply_link", ""),
                description=(j.get("job_description", "") or "")[:2000],
                salary_min=float(smin) if smin else None,
                salary_max=float(smax) if smax else None,
                date_posted=date_posted,
                source="jsearch",
                source_type=DataSourceType.OFFICIAL_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
