"""YC Companies OSS API — 5,690 companies, updated daily, includes isHiring flag."""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.yc_companies")

YC_API_URL = "https://yc-oss.github.io/api/companies/all.json"


class YCCompaniesSource(BaseSource):
    """Fetches YC company list and triggers Greenhouse/Lever/Ashby lookups for hiring companies."""

    source_name = "yc_companies"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        companies = await self._fetch_yc_companies()
        hiring = [c for c in companies if c.get("isHiring")]
        logger.info("yc_companies: %d companies, %d hiring", len(companies), len(hiring))

        # For each hiring company, try their ATS boards
        jobs: list[RawJob] = []
        sem = asyncio.Semaphore(8)

        async def probe_company(company: dict) -> list[RawJob]:
            async with sem:
                return await self._probe_ats(company, query)

        batches = await asyncio.gather(*[probe_company(c) for c in hiring[:300]], return_exceptions=True)
        for b in batches:
            if isinstance(b, list):
                jobs.extend(b)
        return jobs

    async def _fetch_yc_companies(self) -> list[dict]:
        try:
            resp = await self._get(YC_API_URL)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("yc companies fetch failed: %s", e)
            return []

    async def _probe_ats(self, company: dict, query: ParsedQuery) -> list[RawJob]:
        """Try Greenhouse and Lever slug variants for a YC company."""
        name: str = company.get("name", "")
        if not name:
            return []

        # Import here to avoid circular imports
        from ..discovery.ats_prober import derive_slug_variants
        from .ashby import AshbySource
        from .greenhouse import GreenhouseSource
        from .lever import LeverSource

        slugs = derive_slug_variants(name)

        # Check mapping cache first
        from ..discovery.company_list import get_mapping_cache
        cache = get_mapping_cache()
        company_lower = name.lower()

        jobs: list[RawJob] = []
        for slug in slugs[:4]:  # Try top 4 variants
            # Greenhouse
            if company_lower not in cache or cache[company_lower].greenhouse_slug is None:
                try:
                    gh_source = GreenhouseSource()
                    resp = await gh_source._get(
                        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        found = self._parse_greenhouse_jobs(data, name, slug, query)
                        if found:
                            jobs.extend(found)
                            break
                except Exception:
                    pass

            # Lever
            try:
                lv_source = LeverSource()
                resp = await lv_source._get(
                    f"https://api.lever.co/v0/postings/{slug}",
                    params={"mode": "json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        found = self._parse_lever_jobs(data, name, query)
                        if found:
                            jobs.extend(found)
                            break
            except Exception:
                pass

        return jobs

    def _parse_greenhouse_jobs(self, data: dict, company: str, slug: str, query: ParsedQuery) -> list[RawJob]:
        jobs = []
        for j in data.get("jobs", []):
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
            rj = RawJob(
                id=job_id(company, title, location),
                title=title,
                company=company,
                location=location,
                remote="remote" in location.lower(),
                url=j.get("absolute_url", ""),
                source="yc_companies→greenhouse",
                source_type=DataSourceType.COMMUNITY_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs

    def _parse_lever_jobs(self, data: list, company: str, query: ParsedQuery) -> list[RawJob]:
        jobs = []
        for j in data:
            title = j.get("text", "")
            location = j.get("categories", {}).get("location", "")
            rj = RawJob(
                id=job_id(company, title, location),
                title=title,
                company=company,
                location=location,
                remote="remote" in location.lower(),
                url=j.get("hostedUrl", ""),
                source="yc_companies→lever",
                source_type=DataSourceType.COMMUNITY_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
