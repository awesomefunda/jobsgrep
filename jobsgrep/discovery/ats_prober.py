"""Company → ATS slug discovery: probes Greenhouse/Lever/Ashby for each company."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

import httpx

from ..config import get_settings
from ..models import ATSMapping

if TYPE_CHECKING:
    pass

logger = logging.getLogger("jobsgrep.discovery")

ATS_PROBE_TIMEOUT = 10.0
CONCURRENT_PROBES = 10


def derive_slug_variants(company_name: str) -> list[str]:
    """Generate common ATS slug patterns for a company name."""
    name = company_name.strip()
    # Remove common suffixes
    name = re.sub(r"\s+(Inc\.?|LLC\.?|Corp\.?|Ltd\.?|Co\.?|Technologies|Technology|Labs?|AI|HQ)$", "", name, flags=re.I).strip()

    base = name.lower()
    hyphenated = re.sub(r"[\s_]+", "-", base)
    underscored = re.sub(r"[\s\-]+", "_", base)
    nospace = re.sub(r"[\s\-_]+", "", base)

    variants = [hyphenated, nospace, underscored]

    # Strip trailing punctuation
    no_dot = nospace.rstrip(".")
    if no_dot != nospace:
        variants.append(no_dot)

    # "io" suffix variant
    if not nospace.endswith("io"):
        variants.append(nospace + "io")

    return list(dict.fromkeys(variants))  # dedupe, preserve order


async def probe_company(
    company_name: str,
    client: httpx.AsyncClient,
    settings=None,
) -> ATSMapping:
    """Probe all ATS endpoints for a company and return a mapping."""
    if settings is None:
        settings = get_settings()

    slugs = derive_slug_variants(company_name)
    mapping = ATSMapping(company=company_name)

    async def try_greenhouse(slug: str) -> bool:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        try:
            r = await client.get(url, timeout=ATS_PROBE_TIMEOUT)
            if r.status_code == 200 and r.json().get("jobs") is not None:
                mapping.greenhouse_slug = slug
                return True
        except Exception:
            pass
        return False

    async def try_lever(slug: str) -> bool:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            r = await client.get(url, timeout=ATS_PROBE_TIMEOUT)
            if r.status_code == 200 and isinstance(r.json(), list):
                mapping.lever_slug = slug
                return True
        except Exception:
            pass
        return False

    async def try_ashby(slug: str) -> bool:
        url = "https://jobs.ashbyhq.com/api/non-user-graphql"
        try:
            r = await client.post(
                url,
                json={"operationName": "ApiJobBoardWithTeams",
                      "query": "query ApiJobBoardWithTeams($boardHandle: String!) { jobBoard: jobBoardWithTeams(handle: $boardHandle) { jobPostings { id } } }",
                      "variables": {"boardHandle": slug}},
                timeout=ATS_PROBE_TIMEOUT,
            )
            if r.status_code == 200:
                board = r.json().get("data", {}).get("jobBoard")
                if board is not None:
                    mapping.ashby_slug = slug
                    return True
        except Exception:
            pass
        return False

    for slug in slugs:
        await asyncio.sleep(0.1)  # gentle rate limiting
        tasks = []
        if not mapping.greenhouse_slug:
            tasks.append(try_greenhouse(slug))
        if not mapping.lever_slug:
            tasks.append(try_lever(slug))
        if not mapping.ashby_slug:
            tasks.append(try_ashby(slug))
        if tasks:
            await asyncio.gather(*tasks)
        if mapping.greenhouse_slug and mapping.lever_slug and mapping.ashby_slug:
            break

    return mapping
