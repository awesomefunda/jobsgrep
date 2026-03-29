"""Greenhouse ATS public job board API."""
from __future__ import annotations

import asyncio
import logging

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.greenhouse")

# 50+ high-value tech company boards (reused from existing JobsGrep scout.py)
DEFAULT_BOARDS = [
    "stripe", "figma", "notion", "miro", "canva", "dbt-labs", "airbyte",
    "huggingface", "cohere", "cloudflare", "databricks", "snowflake",
    "confluent", "hashicorp", "cockroachdb", "planetscale", "neon",
    "vercel", "netlify", "supabase", "planetscale", "railway",
    "brex", "ramp", "plaid", "chime", "robinhood",
    "duolingo", "grammarly", "notion", "loom", "miro",
    "coreweave", "together", "modal", "replicate", "weights-biases",
    "scale", "anyscale", "mosaic", "predibase",
    "retool", "linear", "airtable", "coda",
    "intercom", "zendesk", "salesforce", "hubspot",
    "twilio", "datadog", "newrelic", "pagerduty", "grafana",
    "mongodb", "elastic", "redis",
    "openai", "anthropic", "deepmind", "mistral",
    "benchling", "recursion", "insitro",
    "flexport", "convoy", "project44",
]


class GreenhouseSource(BaseSource):
    source_name = "greenhouse"

    BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        # Combine default boards with any query-specified companies
        boards = list(dict.fromkeys(DEFAULT_BOARDS + [
            c.lower().replace(" ", "-") for c in query.target_companies
        ]))

        # Fetch all boards concurrently (batched to respect rate limit)
        results: list[RawJob] = []
        sem = asyncio.Semaphore(5)

        async def fetch_board(board: str) -> list[RawJob]:
            async with sem:
                return await self._fetch_board(board, query)

        tasks = [fetch_board(b) for b in boards]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        for b in batches:
            if isinstance(b, list):
                results.extend(b)
        return results

    async def _fetch_board(self, board: str, query: ParsedQuery) -> list[RawJob]:
        url = self.BASE_URL.format(board=board)
        try:
            resp = await self._get(url)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("greenhouse board %s failed: %s", board, e)
            return []

        jobs = []
        for j in data.get("jobs", []):
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
            url_apply = j.get("absolute_url", "")
            updated = j.get("updated_at", "")

            rj = RawJob(
                id=job_id(board, title, location),
                title=title,
                company=board.replace("-", " ").title(),
                location=location,
                remote="remote" in location.lower(),
                url=url_apply,
                date_posted=updated[:10] if updated else "",
                source="greenhouse",
                source_type=DataSourceType.PUBLIC_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
