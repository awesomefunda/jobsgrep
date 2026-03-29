"""JobSpy web scraper — LOCAL mode only (or ALLOW_SCRAPE=true)."""
from __future__ import annotations

import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.jobspy")


class JobSpySource(BaseSource):
    source_name = "jobspy"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()
        logger.warning(
            "JobSpy is a scraping tool accessing Indeed/LinkedIn/Glassdoor. "
            "Use at your own risk. Ensure compliance with each platform's ToS."
        )

        try:
            import jobspy  # type: ignore
        except ImportError:
            logger.info("jobspy not installed — skipping. Install with: pip install python-jobspy")
            return []

        search_term = " OR ".join(query.titles[:2]) if query.titles else "Software Engineer"
        location = query.locations[0] if query.locations else "United States"

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            jobs_df = await loop.run_in_executor(
                None,
                lambda: jobspy.scrape_jobs(
                    site_name=["indeed", "linkedin", "glassdoor"],
                    search_term=search_term,
                    location=location,
                    results_wanted=50,
                    hours_old=72,
                    is_remote=query.remote_ok,
                ),
            )
        except Exception as e:
            logger.warning("jobspy scrape failed: %s", e)
            return []

        if jobs_df is None or jobs_df.empty:
            return []

        jobs = []
        for _, row in jobs_df.iterrows():
            title = str(row.get("title", ""))
            company = str(row.get("company", ""))
            location_val = str(row.get("location", ""))
            url_apply = str(row.get("job_url", ""))
            description = str(row.get("description", ""))[:2000]
            salary_min = row.get("min_amount")
            salary_max = row.get("max_amount")

            rj = RawJob(
                id=job_id(company, title, location_val),
                title=title,
                company=company,
                location=location_val,
                remote=bool(row.get("is_remote", False)),
                url=url_apply,
                description=description,
                salary_min=float(salary_min) if salary_min and str(salary_min) != "nan" else None,
                salary_max=float(salary_max) if salary_max and str(salary_max) != "nan" else None,
                date_posted=str(row.get("date_posted", ""))[:10],
                source=f"jobspy:{row.get('site', 'unknown')}",
                source_type=DataSourceType.SCRAPER,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
