"""USAJobs official government job board API."""
from __future__ import annotations

import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.usajobs")

USAJOBS_URL = "https://data.usajobs.gov/api/Search"


class USAJobsSource(BaseSource):
    source_name = "usajobs"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        settings = self.settings
        if not settings.usajobs_api_key:
            logger.info("usajobs: no API key set, skipping")
            return []

        jobs: list[RawJob] = []
        for title in (query.titles or ["Software Engineer"])[:3]:
            found = await self._search(title, query)
            jobs.extend(found)

        # Deduplicate by id
        seen: set[str] = set()
        deduped = []
        for j in jobs:
            if j.id not in seen:
                seen.add(j.id)
                deduped.append(j)
        return deduped

    async def _search(self, keyword: str, query: ParsedQuery) -> list[RawJob]:
        params = {
            "Keyword": keyword,
            "ResultsPerPage": 25,
            "Fields": "Min",
        }
        if query.locations:
            params["LocationName"] = query.locations[0]
        if query.remote_ok:
            params["RemoteIndicator"] = "True"

        try:
            resp = await self._get(
                USAJOBS_URL,
                params=params,
                headers={
                    "Authorization": self.settings.usajobs_api_key,
                    "User-Agent": self.settings.user_agent,
                    "Host": "data.usajobs.gov",
                },
            )
            if resp.status_code == 403:
                logger.warning("usajobs: 403 — check USAJOBS_API_KEY")
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("usajobs search failed: %s", e)
            return []

        jobs = []
        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            dv = item.get("MatchedObjectDescriptor", {})
            title = dv.get("PositionTitle", "")
            company = dv.get("OrganizationName", "")
            location = ", ".join(
                loc.get("LocationName", "") for loc in dv.get("PositionLocation", [])[:1]
            )
            url_apply = dv.get("PositionURI", "")
            salary_min = dv.get("PositionRemuneration", [{}])[0].get("MinimumRange", "")
            salary_max = dv.get("PositionRemuneration", [{}])[0].get("MaximumRange", "")
            date_posted = dv.get("PublicationStartDate", "")[:10]

            rj = RawJob(
                id=job_id(company, title, location),
                title=title,
                company=company,
                location=location,
                remote="remote" in location.lower() or dv.get("PositionOfferingType", [{}])[0].get("Name", "").lower() == "remote",
                url=url_apply,
                salary_text=f"${salary_min}–${salary_max}/yr" if salary_min else "",
                date_posted=date_posted,
                source="usajobs",
                source_type=DataSourceType.OFFICIAL_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
