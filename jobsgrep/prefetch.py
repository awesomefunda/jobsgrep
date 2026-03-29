"""Background prefetch worker — pre-warms the job cache for common searches.

Runs on server startup (after a short delay) and then periodically.
Only active in PRIVATE/PUBLIC modes where remote users benefit from warm cache.
LOCAL mode skips prefetch since the user runs their own searches.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("jobsgrep.prefetch")

_DEFAULT_QUERIES = [
    "Software Engineer",
    "Senior Software Engineer",
    "Staff Software Engineer",
    "Backend Engineer",
    "Frontend Engineer",
    "Full Stack Engineer",
    "Machine Learning Engineer",
    "Data Engineer",
    "Platform Engineer",
    "DevOps Engineer",
]


async def _prefetch_query(query: str) -> int:
    """Run a single prefetch search and store results in cache. Returns job count."""
    from .job_cache import cache_key, get, store
    from .nlp.parser import parse_query
    from .config import get_settings, get_enabled_sources
    from .sources.greenhouse import GreenhouseSource
    from .sources.lever import LeverSource
    from .sources.ashby import AshbySource
    from .sources.hn_hiring import HNHiringSource
    from .sources.yc_companies import YCCompaniesSource
    from .sources.usajobs import USAJobsSource

    parsed = await parse_query(query, None)
    key = cache_key(parsed)

    # Skip if already cached and fresh
    cached = get(key)
    if cached is not None:
        logger.info("prefetch skip (cache hit): %s — %d jobs", query, len(cached))
        return len(cached)

    logger.info("prefetch start: %s", query)
    settings = get_settings()
    enabled = get_enabled_sources()

    source_map = {
        "greenhouse": GreenhouseSource(),
        "lever": LeverSource(),
        "ashby": AshbySource(),
        "hn_hiring": HNHiringSource(),
        "yc_companies": YCCompaniesSource(),
        "usajobs": USAJobsSource(),
    }

    async def run_source(name: str, source):
        if name not in enabled:
            return name, []
        try:
            jobs = await source.fetch_jobs(parsed)
            return name, jobs
        except Exception as e:
            logger.warning("prefetch source %s failed for '%s': %s", name, query, e)
            return name, []

    results = await asyncio.gather(*[run_source(n, s) for n, s in source_map.items()])

    all_jobs = []
    seen_ids: set[str] = set()
    for _, jobs in results:
        for j in jobs:
            if j.id not in seen_ids:
                seen_ids.add(j.id)
                all_jobs.append(j)

    for source in source_map.values():
        await source.close()

    if all_jobs:
        store(key, all_jobs, source="prefetch", label=query)
        logger.info("prefetch complete: '%s' — %d jobs cached", query, len(all_jobs))

    return len(all_jobs)


async def run_prefetch_cycle(queries: list[str], stagger_seconds: float = 30.0) -> None:
    """Run one full prefetch cycle over the given queries, staggered to avoid hammering APIs."""
    logger.info("prefetch cycle starting: %d queries", len(queries))
    for query in queries:
        try:
            count = await _prefetch_query(query)
            logger.debug("prefetch '%s' done: %d jobs", query, count)
        except Exception as e:
            logger.warning("prefetch '%s' error: %s", query, e)
        # Stagger requests to be polite to source APIs
        await asyncio.sleep(stagger_seconds)
    logger.info("prefetch cycle complete")


async def start_prefetch_loop(queries: list[str] | None = None,
                               interval_hours: float = 6.0,
                               startup_delay_seconds: float = 30.0) -> None:
    """
    Long-running asyncio task. Runs one prefetch cycle on startup (after delay),
    then repeats every `interval_hours`.

    Wire into FastAPI lifespan as:
        asyncio.create_task(start_prefetch_loop(...))
    """
    from .config import get_settings
    settings = get_settings()

    # Prefetch only makes sense in server modes
    if settings.is_local:
        logger.debug("prefetch disabled in LOCAL mode")
        return

    effective_queries = queries or _DEFAULT_QUERIES

    logger.info(
        "prefetch worker starting: %d queries, interval=%.1fh, startup delay=%.0fs",
        len(effective_queries), interval_hours, startup_delay_seconds,
    )

    # Wait a bit for the server to finish startup before hammering APIs
    await asyncio.sleep(startup_delay_seconds)

    while True:
        try:
            await run_prefetch_cycle(effective_queries)
        except asyncio.CancelledError:
            logger.info("prefetch worker cancelled")
            return
        except Exception as e:
            logger.error("prefetch cycle failed: %s", e)

        await asyncio.sleep(interval_hours * 3600)
