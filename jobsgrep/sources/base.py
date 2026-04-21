"""Abstract base class for all data sources."""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx

from ..config import SOURCE_REGISTRY, get_settings
from ..legal.audit import log_api_call
from ..legal.compliance import assert_source_allowed
from ..legal.rate_limiter import wait_for_source
from ..models import DataSourceMeta, ParsedQuery, RawJob


def job_id(company: str, title: str, location: str) -> str:
    raw = f"{company.lower()}|{title.lower()}|{location.lower()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class BaseSource(ABC):
    source_name: str  # must match key in SOURCE_REGISTRY

    def __init__(self) -> None:
        self.meta: DataSourceMeta = SOURCE_REGISTRY[self.source_name]
        self.settings = get_settings()
        self.logger = logging.getLogger(f"jobsgrep.sources.{self.source_name}")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.settings.user_agent},
                timeout=10.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _check_allowed(self) -> None:
        assert_source_allowed(self.meta)

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        """Rate-limited, audited GET request."""
        await wait_for_source(self.source_name)
        resp = await self.client.get(url, **kwargs)
        await log_api_call(self.source_name, url, resp.status_code)
        return resp

    async def _post(self, url: str, **kwargs) -> httpx.Response:
        await wait_for_source(self.source_name)
        resp = await self.client.post(url, **kwargs)
        await log_api_call(self.source_name, url, resp.status_code)
        return resp

    @abstractmethod
    async def fetch_jobs(self, query: ParsedQuery) -> list[RawJob]:
        """Fetch jobs matching the parsed query. Must call _check_allowed() first."""
        ...

    def _keyword_match(self, job: RawJob, query: ParsedQuery) -> bool:
        """Pre-filter: only reject jobs that explicitly match exclude_keywords.

        Title matching is intentionally NOT done here — a job titled
        "Senior Engineer, Platform" should not be dropped just because it
        doesn't textually contain "Staff Software Engineer". The LLM scorer
        handles semantic fit; this filter only removes obvious mismatches
        (e.g. "manager", "director", "intern") that the user listed.
        """
        if not query.exclude_keywords:
            return True
        text = f"{job.title} {job.description}".lower()
        return not any(kw.lower() in text for kw in query.exclude_keywords)
