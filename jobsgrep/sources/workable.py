"""Workable public widget API."""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.workable")

DEFAULT_BOARDS = [
    "skroutz", "taxfix", "agicap", "spendesk",
    "kyriba", "payfit", "pennylane",
    "alan", "doctolib", "contentsquare",
]


class WorkableSource(BaseSource):
    source_name = "workable"

    BASE_URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        boards = list(dict.fromkeys(DEFAULT_BOARDS + [
            c.lower().replace(" ", "") for c in query.target_companies
        ]))

        sem = asyncio.Semaphore(5)

        async def fetch_board(board: str) -> list[RawJob]:
            async with sem:
                return await self._fetch_board(board, query)

        tasks = [fetch_board(b) for b in boards]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[RawJob] = []
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    async def _fetch_board(self, slug: str, query: ParsedQuery) -> list[RawJob]:
        url = self.BASE_URL.format(slug=slug)
        try:
            resp = await self._get(url, params={"details": "true"})
            if resp.status_code in (404, 403):
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("workable %s failed: %s", slug, e)
            return []

        jobs = []
        for j in data.get("jobs", []):
            title = j.get("title", "")
            location = j.get("location", {})
            if isinstance(location, dict):
                loc_str = location.get("city", "") or location.get("country", "")
            else:
                loc_str = str(location)
            url_apply = j.get("url", "")

            rj = RawJob(
                id=job_id(slug, title, loc_str),
                title=title,
                company=data.get("account", {}).get("name", slug.title()),
                location=loc_str,
                remote=j.get("remote", False),
                url=url_apply,
                source="workable",
                source_type=DataSourceType.PUBLIC_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
