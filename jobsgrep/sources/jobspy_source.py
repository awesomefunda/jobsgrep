"""JobSpy scraper — LOCAL/PRIVATE only (or ALLOW_SCRAPE=true).

JobSpy (https://github.com/speedyapply/JobSpy) scrapes Indeed, Google Jobs,
LinkedIn, Glassdoor and ZipRecruiter. Google Jobs is itself an aggregator of
LinkedIn, Glassdoor, ZipRecruiter and company career pages, so enabling the
"google" site yields by far the largest corpus.

This source is compile-time gated to non-PUBLIC modes — it never runs on a public
server. The intended flow is: run `jobsgrep run-prefetch` locally (where scraping
is allowed), then `jobsgrep push` the resulting corpus to production.

Requires the optional dependency:  pip install python-jobspy
"""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.jobspy")

# Broad role terms used to build a corpus when no specific query is given.
_CORPUS_TERMS = [
    "software engineer", "senior software engineer", "backend engineer",
    "frontend engineer", "full stack engineer", "data engineer",
    "machine learning engineer", "data scientist", "devops engineer",
    "site reliability engineer", "security engineer", "mobile engineer",
    "engineering manager", "product manager",
    "asic design engineer", "fpga engineer", "hardware engineer",
    "rtl design engineer", "embedded engineer",
]


class JobSpySource(BaseSource):
    source_name = "jobspy"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        try:
            import jobspy  # type: ignore
        except ImportError:
            logger.info("jobspy not installed — skipping. Install with: pip install python-jobspy")
            return []

        sites = [s.strip() for s in (self.settings.jobspy_sites or "indeed,google").split(",") if s.strip()]
        location = query.locations[0] if query.locations else self.settings.jobspy_location
        results_wanted = self.settings.jobspy_results_per_term
        hours_old = self.settings.jobspy_hours_old

        # Corpus mode (no titles) → sweep broad terms; otherwise use the query.
        terms = (query.titles + query.title_variations)[:4] if query.titles else _CORPUS_TERMS

        logger.info("jobspy: scraping %d terms across %s", len(terms), sites)
        all_jobs: list[RawJob] = []
        seen: set[str] = set()
        loop = asyncio.get_event_loop()

        for term in terms:
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda t=term: jobspy.scrape_jobs(
                        site_name=sites,
                        search_term=t,
                        google_search_term=f"{t} jobs near {location}",
                        location=location,
                        results_wanted=results_wanted,
                        hours_old=hours_old,
                    ),
                )
            except Exception as e:
                logger.warning("jobspy term '%s' failed: %s", term, e)
                continue

            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                rj = self._row_to_job(row, query)
                if rj and rj.id not in seen:
                    seen.add(rj.id)
                    all_jobs.append(rj)

        logger.info("jobspy: %d unique jobs from %d terms", len(all_jobs), len(terms))
        return all_jobs

    def _row_to_job(self, row, query: ParsedQuery) -> RawJob | None:
        def _val(key, default=""):
            v = row.get(key, default)
            return default if v is None or str(v) == "nan" else v

        title = str(_val("title"))
        if not title:
            return None
        company = str(_val("company"))
        location_val = str(_val("location"))
        site = str(_val("site", "unknown"))
        salary_min = row.get("min_amount")
        salary_max = row.get("max_amount")

        rj = RawJob(
            id=job_id(company, title, location_val),
            title=title,
            company=company,
            location=location_val,
            remote=bool(row.get("is_remote", False)),
            url=str(_val("job_url")),
            description=str(_val("description"))[:2000],
            salary_min=float(salary_min) if salary_min and str(salary_min) != "nan" else None,
            salary_max=float(salary_max) if salary_max and str(salary_max) != "nan" else None,
            date_posted=str(_val("date_posted"))[:10],
            source=f"jobspy:{site}",
            source_type=DataSourceType.SCRAPER,
        )
        return rj if self._keyword_match(rj, query) else None
