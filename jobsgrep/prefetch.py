"""Background prefetch + pre-score worker.

For each configured query this worker:
  1. Checks the scored cache → if fresh, skip entirely (zero API calls)
  2. Checks the raw job cache → if fresh, score from cache (no source calls)
  3. Otherwise: fetch from all enabled sources, score via LLM, store both caches

This means the first user to search "Software Engineer" after a prefetch cycle
gets a fully-scored Excel report with zero waiting on sources or LLM.

Only runs in PRIVATE/PUBLIC modes (LOCAL users run their own searches).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("jobsgrep.prefetch")

# Queries to pre-warm on startup. The first entry ("Software Engineer") is
# scored immediately; the rest are staggered to spread API load.
_DEFAULT_QUERIES = [
    "Software Engineer",
    "Senior Software Engineer",
    "Staff Software Engineer",
    "Backend Engineer",
    "Frontend Engineer",
    "Full Stack Engineer",
    "Machine Learning Engineer",
    "Data Engineer",
    "Engineering Manager",
    "Software Development Manager",
    "Director of Engineering",
    "VP of Engineering",
    "Product Manager",
    "Senior Product Manager",
    "Technical Program Manager",
]


async def _prefetch_query(query: str, skip_scoring: bool = False) -> tuple[int, int]:
    """Fetch + score one query. Returns (raw_job_count, scored_job_count)."""
    from .job_cache import cache_key, get as cache_get, store as cache_store
    from .job_cache import get_scored, store_scored
    from .nlp.parser import parse_query
    from .config import get_settings, get_enabled_sources
    from .scoring.engine import score_jobs
    from .sources.greenhouse import GreenhouseSource
    from .sources.lever import LeverSource
    from .sources.ashby import AshbySource
    from .sources.hn_hiring import HNHiringSource
    from .sources.yc_companies import YCCompaniesSource
    from .sources.usajobs import USAJobsSource

    parsed = await parse_query(query, None)
    key = cache_key(parsed)

    # ── 1. Skip if we already have data (scored or raw cache) ──────────────
    scored_hit = get_scored(key)
    if scored_hit is not None:
        jobs_list, _ = scored_hit
        logger.info("prefetch skip (scored cache hit): '%s' — %d jobs", query, len(jobs_list))
        return len(jobs_list), len(jobs_list)

    raw_jobs = cache_get(key)
    if raw_jobs is not None:
        if skip_scoring:
            logger.info("prefetch: raw cache hit '%s' (%d jobs), skipping scoring.", query, len(raw_jobs))
            return len(raw_jobs), 0
        logger.info("prefetch: raw cache hit '%s' (%d jobs), scoring...", query, len(raw_jobs))
        scored = await score_jobs(raw_jobs, parsed)
        if scored:
            store_scored(key, scored, source="prefetch", label=query)
        return len(raw_jobs), len(scored)

    # ── 3. Full fetch + score ───────────────────────────────────────────────
    logger.info("prefetch fetch+score: '%s'", query)
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

    if not all_jobs:
        logger.warning("prefetch: no jobs found for '%s'", query)
        return 0, 0

    # Store raw jobs
    cache_store(key, all_jobs, source="prefetch", label=query)

    if skip_scoring:
        logger.info("prefetch: fetched %d jobs for '%s', scoring skipped.", len(all_jobs), query)
        return len(all_jobs), 0

    logger.info("prefetch: fetched %d jobs for '%s', scoring...", len(all_jobs), query)

    # Score and store
    scored = await score_jobs(all_jobs, parsed)
    if scored:
        store_scored(key, scored, source="prefetch", label=query)

    logger.info("prefetch done: '%s' — %d raw, %d scored", query, len(all_jobs), len(scored))
    return len(all_jobs), len(scored)


async def run_prefetch_cycle(queries: list[str], stagger_seconds: float = 20.0, skip_scoring: bool = False) -> None:
    """Run one full prefetch cycle, staggered to be polite to source APIs."""
    logger.info("prefetch cycle starting: %d queries (skip_scoring=%s)", len(queries), skip_scoring)
    for i, query in enumerate(queries):
        try:
            raw, scored = await _prefetch_query(query, skip_scoring=skip_scoring)
            logger.debug("prefetch '%s': %d raw, %d scored", query, raw, scored)
        except Exception as e:
            logger.warning("prefetch '%s' error: %s", query, e)
        # Don't stagger before the first query — get it warm ASAP
        if i < len(queries) - 1:
            await asyncio.sleep(stagger_seconds)
    logger.info("prefetch cycle complete")


async def start_prefetch_loop(
    queries: list[str] | None = None,
    interval_hours: float = 6.0,
    startup_delay_seconds: float = 15.0,
    skip_scoring: bool = False,
) -> None:
    """Long-running asyncio task. Wire into FastAPI lifespan."""
    from .config import get_settings
    settings = get_settings()

    # LOCAL mode runs prefetch too — skip_scoring=True keeps it LLM-free

    effective_queries = queries or _DEFAULT_QUERIES
    logger.info(
        "prefetch worker starting: %d queries, interval=%.1fh, startup delay=%.0fs, skip_scoring=%s",
        len(effective_queries), interval_hours, startup_delay_seconds, skip_scoring,
    )

    await asyncio.sleep(startup_delay_seconds)

    while True:
        try:
            await run_prefetch_cycle(effective_queries, skip_scoring=skip_scoring)
        except asyncio.CancelledError:
            logger.info("prefetch worker cancelled")
            return
        except Exception as e:
            logger.error("prefetch cycle failed: %s", e)

        await asyncio.sleep(interval_hours * 3600)
