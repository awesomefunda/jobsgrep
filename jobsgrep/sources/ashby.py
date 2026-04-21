"""Ashby ATS public GraphQL job board API."""
from __future__ import annotations

import asyncio
import logging
import re

from ..models import DataSourceType, ParsedQuery, RawJob
from .base import BaseSource, job_id

logger = logging.getLogger("jobsgrep.sources.ashby")

DEFAULT_BOARDS = [
    "linear", "vercel", "openai", "anthropic", "mistral", "cohere",
    "dbt-labs", "supabase", "neon", "planetscale", "turso",
    "anyscale", "modal", "replicate", "together", "coreweave",
    "weights-biases", "predibase", "mosaic",
    "retool", "airplane", "internal",
    "loom", "notion", "coda",
    "mercury", "brex", "puzzle",
    "replit", "cursor", "sourcegraph",
    "temporal", "inngest", "trigger",
    "render", "railway",
    "deepmind", "inflection", "adept", "stability",
    "scale", "labelbox", "snorkel",
    "benchling", "recursion",
    "figma", "miro", "whimsical",
    # High-paying AI companies (confirmed Ashby)
    "perplexity", "elevenlabs", "runway",
]

_GQL_QUERY = """
query ApiJobBoardWithTeams($boardHandle: String!) {
  jobBoard: jobBoardWithTeams(handle: $boardHandle) {
    jobPostings {
      id
      title
      locationName
      employmentType
      isListed
      externalLink
      compensation {
        summaryComponents {
          label
          summary
        }
      }
    }
  }
}
"""

_SALARY_RE = re.compile(r"\$[\d,]+(?:K|k)?(?:\s*[-–]\s*\$[\d,]+(?:K|k)?)?")


def _parse_salary(comp: dict) -> str:
    for c in comp.get("summaryComponents", []):
        summary = c.get("summary", "")
        m = _SALARY_RE.search(summary)
        if m:
            return m.group(0)
    return ""


class AshbySource(BaseSource):
    source_name = "ashby"

    GQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql"

    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        self._check_allowed()

        boards = list(dict.fromkeys(DEFAULT_BOARDS + [
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
        try:
            resp = await self._post(
                self.GQL_URL,
                json={"operationName": "ApiJobBoardWithTeams",
                      "query": _GQL_QUERY,
                      "variables": {"boardHandle": board}},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in (404, 400):
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("ashby board %s failed: %s", board, e)
            return []

        board_data = data.get("data", {}).get("jobBoard")
        if not board_data:
            return []

        jobs = []
        for j in board_data.get("jobPostings", []):
            if not j.get("isListed"):
                continue
            title = j.get("title", "")
            location = j.get("locationName", "")
            url_apply = j.get("externalLink", "") or f"https://jobs.ashbyhq.com/{board}/{j.get('id', '')}"
            comp = j.get("compensation") or {}
            salary_text = _parse_salary(comp)

            rj = RawJob(
                id=job_id(board, title, location),
                title=title,
                company=board.replace("-", " ").title(),
                location=location,
                remote="remote" in location.lower(),
                url=url_apply,
                salary_text=salary_text,
                source="ashby",
                source_type=DataSourceType.PUBLIC_API,
            )
            if self._keyword_match(rj, query):
                jobs.append(rj)
        return jobs
