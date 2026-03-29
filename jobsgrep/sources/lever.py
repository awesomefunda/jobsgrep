"""Lever ATS public postings API."""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.lever")

DEFAULT_BOARDS = [
    "airtable", "figma", "asana", "netlify", "postman",
    "gitlab", "hashicorp", "cockroachdb", "temporal",
    "dbt-labs", "airbyte", "segment", "amplitude",
    "mixpanel", "heap", "fullstory",
    "brex", "mercury", "puzzle",
    "replit", "render", "fly",
    "linear", "notion", "loom",
    "scale-ai", "labelbox",
    "weights-biases", "hugging-face",
    "openai", "cohere", "ai21labs",
    "faire", "fabric", "roam",
]


class LeverSource(BaseSource):
    source_name = "lever"

    BASE_URL = "https://api.lever.co/v0/postings/{board}"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        boards = list(dict.fromkeys(DEFAULT_BOARDS + [
            c.lower().replace(" ", "-") for c in query.target_companies
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

    async def _fetch_board(self, board: str, query: ParsedQuery) -> list[RawJob]:
        url = self.BASE_URL.format(board=board)
        try:
            resp = await self._get(url, params={"mode": "json"})
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("lever board %s failed: %s", board, e)
            return []

        if not isinstance(data, list):
            return []

        jobs = []
        for j in data:
            title = j.get("text", "")
            location_list = j.get("categories", {}).get("location", "")
            if isinstance(location_list, list):
                location = ", ".join(location_list)
            else:
                location = str(location_list) if location_list else ""
            url_apply = j.get("hostedUrl", "")
            description = j.get("descriptionPlain", "") or j.get("description", "")
            commitment = j.get("categories", {}).get("commitment", "")

            # Salary from additional field
            salary_text = ""
            for field in j.get("salaryRange", {}).values() if isinstance(j.get("salaryRange"), dict) else []:
                salary_text = str(field)
                break

            rj = RawJob(
                id=job_id(board, title, location),
                title=title,
                company=board.replace("-", " ").title(),
                location=location,
                remote="remote" in location.lower() or "remote" in commitment.lower(),
                url=url_apply,
                description=description[:2000],
                salary_text=salary_text,
                source="lever",
                source_type=DataSourceType.PUBLIC_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
