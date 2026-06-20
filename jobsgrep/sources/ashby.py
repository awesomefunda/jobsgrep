"""Ashby ATS public posting API.

Uses the documented REST posting API:
  GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

(The older non-user-graphql endpoint stopped returning boards.)
"""
from __future__ import annotations

import asyncio
import logging
import re

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.ashby")

# Verified-working Ashby job boards (handle = jobs.ashbyhq.com/<handle>).
DEFAULT_BOARDS = [
    "openai", "elevenlabs", "notion", "cohere", "ramp", "vanta", "replit",
    "perplexity", "baseten", "supabase", "watershed", "modal", "linear",
    "astronomer", "posthog", "runway", "hex", "mercury", "deel", "clay",
    "sourcegraph", "retool", "wandb", "census", "mux", "together", "langchain",
]

_SALARY_RE = re.compile(r"\$[\d,]+(?:K|k)?(?:\s*[-–]\s*\$[\d,]+(?:K|k)?)?")


def _parse_salary(comp) -> str:
    if not comp:
        return ""
    text = comp if isinstance(comp, str) else str(comp)
    m = _SALARY_RE.search(text)
    return m.group(0) if m else ""


class AshbySource(BaseSource):
    source_name = "ashby"

    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        from ..discovery.company_list import get_mapping_cache
        cache = get_mapping_cache()
        mapped = [m.ashby_slug for m in cache.values() if m.ashby_slug]

        boards = list(dict.fromkeys(DEFAULT_BOARDS + mapped + [
            c.lower().replace(" ", "-") for c in query.target_companies
        ]))

        sem = asyncio.Semaphore(10)

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
            resp = await self._get(url, params={"includeCompensation": "true"})
            if resp.status_code in (404, 400):
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("ashby board %s failed: %s", board, e)
            return []

        company = board.replace("-", " ").title()
        jobs = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            title = j.get("title", "")
            location = j.get("location", "") or ""
            url_apply = j.get("jobUrl") or j.get("applyUrl") or ""
            is_remote = bool(j.get("isRemote")) or (j.get("workplaceType") == "Remote") \
                or "remote" in location.lower()
            published = j.get("publishedAt", "") or ""
            salary_text = _parse_salary(j.get("compensation"))

            rj = RawJob(
                id=job_id(board, title, location),
                title=title,
                company=company,
                location=location,
                remote=is_remote,
                url=url_apply,
                description=(j.get("descriptionPlain", "") or "")[:2000],
                salary_text=salary_text,
                date_posted=published[:10] if published else "",
                source="ashby",
                source_type=DataSourceType.PUBLIC_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
