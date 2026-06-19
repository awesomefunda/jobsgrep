"""Corpus prefetch worker.

Fetches the *entire* tech-job corpus in one query-independent pass and writes
it to the raw job cache. There is NO per-query fetching and NO LLM scoring here:

  - Sources (Greenhouse, Lever, Ashby, HN, YC) all return whole boards/threads;
    they only filter by `exclude_keywords`. So a single fetch with an empty
    match-all query yields every job, instead of re-fetching the same boards
    once per query (the old design hit each board ~15×).
  - Scoring is intentionally dropped: production serves keyword/title matches
    straight from this cache. The only LLM use left in the system is parsing a
    user's query at serve time (and only on a cache miss).

Workflow:
  local:  `jobsgrep run-prefetch`  → fetch_corpus() → ~/.jobsgrep/job_cache/
          `jobsgrep push ...`       → upload the corpus to a remote server
  remote: serves matches from the pushed corpus; LLM only parses the query.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("jobsgrep.prefetch")

# Fixed cache key for the single corpus entry.
CORPUS_KEY = "corpus"
CORPUS_LABEL = "All tech jobs (corpus)"

# Sources actually fetched into the corpus (and shown on the dashboard).
# USAJobs is intentionally excluded (requires an API key). JobSpy is a scraper
# that only runs in LOCAL/PRIVATE — where prefetch is run — and adds Google Jobs,
# Indeed, LinkedIn & Glassdoor coverage before the corpus is pushed to prod.
CORPUS_SOURCE_NAMES = [
    "greenhouse", "lever", "ashby", "recruitee", "workable",
    "smartrecruiters", "adzuna", "jsearch", "hn_hiring", "yc_companies", "jobspy",
]


async def fetch_corpus() -> int:
    """Fetch every job from all enabled sources once and cache it. Returns count.

    Uses an empty (match-all) ParsedQuery: no exclude_keywords means every job
    passes each source's keyword filter, and no target_companies means each
    source fetches its full default board set.
    """
    from .models import ParsedQuery
    from .config import get_enabled_sources
    from .job_cache import store as cache_store
    from .sources.greenhouse import GreenhouseSource
    from .sources.lever import LeverSource
    from .sources.ashby import AshbySource
    from .sources.recruitee import RecruiteeSource
    from .sources.workable import WorkableSource
    from .sources.hn_hiring import HNHiringSource
    from .sources.yc_companies import YCCompaniesSource
    from .sources.smartrecruiters import SmartRecruitersSource
    from .sources.adzuna import AdzunaSource
    from .sources.jsearch import JSearchSource
    from .sources.jobspy_source import JobSpySource

    match_all = ParsedQuery()  # all fields default to empty / False
    enabled = get_enabled_sources()

    source_map = {
        "greenhouse": GreenhouseSource(),
        "lever": LeverSource(),
        "ashby": AshbySource(),
        "recruitee": RecruiteeSource(),
        "workable": WorkableSource(),
        "smartrecruiters": SmartRecruitersSource(),
        "adzuna": AdzunaSource(),
        "jsearch": JSearchSource(),
        "hn_hiring": HNHiringSource(),
        "yc_companies": YCCompaniesSource(),
        "jobspy": JobSpySource(),
    }

    async def run_source(name: str, source):
        if name not in enabled:
            logger.debug("corpus: source %s not enabled, skipping", name)
            return name, []
        try:
            jobs = await source.fetch_jobs(match_all)
            logger.info("corpus: %s returned %d jobs", name, len(jobs))
            return name, jobs
        except Exception as e:
            logger.warning("corpus source %s failed: %s", name, e)
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
        logger.warning("corpus: no jobs fetched from any source")
        return 0

    cache_store(CORPUS_KEY, all_jobs, source="prefetch", label=CORPUS_LABEL)
    logger.info("corpus: cached %d unique jobs", len(all_jobs))
    return len(all_jobs)


async def run_prefetch_cycle(*_args, **_kwargs) -> None:
    """Run one corpus fetch. Extra args are accepted/ignored for API compatibility."""
    logger.info("prefetch cycle starting: full corpus fetch")
    try:
        count = await fetch_corpus()
        logger.info("prefetch cycle complete: %d jobs cached", count)
    except Exception as e:
        logger.warning("prefetch cycle error: %s", e)


async def start_prefetch_loop(
    interval_hours: float = 6.0,
    startup_delay_seconds: float = 15.0,
    **_kwargs,
) -> None:
    """Long-running asyncio task. Wire into FastAPI lifespan."""
    logger.info(
        "prefetch worker starting: corpus fetch, interval=%.1fh, startup delay=%.0fs",
        interval_hours, startup_delay_seconds,
    )

    await asyncio.sleep(startup_delay_seconds)

    while True:
        try:
            await run_prefetch_cycle()
        except asyncio.CancelledError:
            logger.info("prefetch worker cancelled")
            return
        except Exception as e:
            logger.error("prefetch cycle failed: %s", e)

        await asyncio.sleep(interval_hours * 3600)
