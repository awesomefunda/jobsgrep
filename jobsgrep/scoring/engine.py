"""Keyword filter + LLM scoring pipeline (Gemini → Groq fallback).

Optimizations:
  - Title word-overlap pre-filter removes ~40% of jobs before any LLM call
  - Per-job score cache: same job + same requirements = no re-scoring
  - Batch size 15 (was 5): 3× fewer LLM calls for the same job count
  - Description truncated to 400 chars (was 800): ~50% input token reduction
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from ..config import get_settings
from ..llm import complete, strip_fences
from ..models import JobScore, ParsedQuery, RawJob, ScoredJob
from .prompts import (
    SCORE_BATCH_TEMPLATE,
    SCORE_SYSTEM,
    build_jobs_block,
    build_requirements_block,
)

logger = logging.getLogger("jobsgrep.scoring")

BATCH_SIZE = 15   # was 5 — fewer round-trips, same quality

# ─── Per-job in-memory score cache ───────────────────────────────────────────
# Key: md5(job_id + ":" + requirements_hash)[:16]
# Populated by prefetch; reused when user searches the same role.
_score_mem: dict[str, JobScore] = {}


def _score_cache_key(job_id: str, req_hash: str) -> str:
    return hashlib.md5(f"{job_id}:{req_hash}".encode()).hexdigest()[:16]


def _req_hash(requirements: str) -> str:
    return hashlib.md5(requirements.encode()).hexdigest()[:8]


# ─── Title word-overlap pre-filter ───────────────────────────────────────────

_STOP = frozenset({
    "the", "a", "an", "of", "and", "or", "for", "in", "at", "to", "with",
    "is", "are", "we", "our", "you", "new", "i", "ii", "iii", "iv",
})


def _title_words(title: str) -> frozenset[str]:
    return frozenset(
        w for w in re.findall(r"[a-z]+", title.lower())
        if w not in _STOP and len(w) > 2
    )


def title_filter(jobs: list[RawJob], query: ParsedQuery) -> list[RawJob]:
    """Drop jobs whose title has zero word-overlap with any query title/variation.

    This is a cheap zero-cost filter that eliminates clearly wrong roles
    (e.g. "Account Manager", "Sales Rep" when searching "Software Engineer")
    without any LLM calls. The LLM handles nuanced fit for remaining jobs.
    """
    all_titles = query.titles + query.title_variations
    if not all_titles:
        return jobs

    query_words = frozenset().union(*(_title_words(t) for t in all_titles))
    if not query_words:
        return jobs

    kept, dropped = [], 0
    for job in jobs:
        if _title_words(job.title) & query_words:
            kept.append(job)
        else:
            dropped += 1

    if dropped:
        logger.info("title filter dropped %d/%d jobs", dropped, len(jobs))
    return kept


def keyword_filter(jobs: list[RawJob], query: ParsedQuery) -> list[RawJob]:
    """Drop jobs containing any user-specified exclude keywords."""
    if not query.exclude_keywords:
        return jobs
    exclude = [kw.lower() for kw in query.exclude_keywords]
    filtered = []
    for job in jobs:
        text = f"{job.title} {job.description}".lower()
        if not any(kw in text for kw in exclude):
            filtered.append(job)
    return filtered


# ─── Main scoring entry point ─────────────────────────────────────────────────

async def score_jobs(
    jobs: list[RawJob],
    query: ParsedQuery,
    progress_cb=None,
) -> list[ScoredJob]:
    """Score all jobs against query. Returns only jobs meeting MIN_FIT_SCORE."""
    settings = get_settings()
    min_score = settings.min_fit_score

    # Step 1: cheap filters (no LLM)
    jobs = title_filter(jobs, query)
    jobs = keyword_filter(jobs, query)
    logger.info("scoring: %d jobs after pre-filters", len(jobs))

    requirements = build_requirements_block(query)
    rh = _req_hash(requirements)

    # Step 2: split into cache hits and misses
    cached_results: list[ScoredJob] = []
    to_score: list[RawJob] = []

    for job in jobs:
        ck = _score_cache_key(job.id, rh)
        if ck in _score_mem:
            score = _score_mem[ck]
            if score.fit_score >= min_score:
                cached_results.append(ScoredJob(job=job, score=score))
        else:
            to_score.append(job)

    if cached_results:
        logger.info("score cache: %d hits, %d misses", len(cached_results), len(to_score))

    # Step 3: LLM-score the uncached jobs in batches (up to 5 concurrent)
    llm_results: list[ScoredJob] = []
    total_batches = (len(to_score) + BATCH_SIZE - 1) // BATCH_SIZE
    _sem = asyncio.Semaphore(3)
    done_count = 0

    async def _run_batch(batch_idx: int, batch: list) -> list[tuple]:
        nonlocal done_count
        async with _sem:
            scores = await _score_batch(batch, requirements)
            done_count += len(batch)
            if progress_cb:
                await progress_cb(
                    f"Scoring jobs {done_count}/{len(to_score)}"
                    + (f" (batch {batch_idx+1}/{total_batches})" if total_batches > 1 else "")
                )
        return list(zip(batch, scores))

    batches = [
        (bi, to_score[i: i + BATCH_SIZE])
        for bi, i in enumerate(range(0, len(to_score), BATCH_SIZE))
    ]
    batch_results = await asyncio.gather(*[_run_batch(bi, b) for bi, b in batches])

    for pairs in batch_results:
        for job, score in pairs:
            _score_mem[_score_cache_key(job.id, rh)] = score
            if score.fit_score >= min_score:
                llm_results.append(ScoredJob(job=job, score=score))

    all_results = cached_results + llm_results
    all_results.sort(key=lambda s: s.score.fit_score, reverse=True)

    logger.info(
        "scoring complete: %d passed (%.0f%% threshold=%.2f) — %d from cache, %d LLM-scored",
        len(all_results),
        100 * len(all_results) / max(len(jobs), 1),
        min_score,
        len(cached_results),
        len(llm_results),
    )
    return all_results


async def _score_batch(jobs: list[RawJob], requirements: str) -> list[JobScore]:
    prompt = SCORE_BATCH_TEMPLATE.format(
        count=len(jobs),
        requirements=requirements,
        jobs_block=build_jobs_block(jobs),
    )
    raw = await complete(prompt=prompt, system=SCORE_SYSTEM, temperature=0.1, max_tokens=2000)
    if raw is None:
        logger.warning("all providers failed, returning zero scores for batch")
        return [JobScore(fit_score=0.0, reasoning="scoring unavailable") for _ in jobs]
    return _parse_scores(raw, len(jobs))


def _parse_scores(raw: str, expected: int) -> list[JobScore]:
    try:
        data = json.loads(strip_fences(raw))
        if not isinstance(data, list):
            data = [data]
        scores = []
        for item in data[:expected]:
            scores.append(JobScore(
                fit_score=float(item.get("fit_score", 0.0)),
                reasoning=item.get("reasoning", ""),
                matching_skills=item.get("matching_skills", []),
                missing_skills=item.get("missing_skills", []),
                red_flags=item.get("red_flags", []),
                salary_range=item.get("salary_range"),
            ))
        while len(scores) < expected:
            scores.append(JobScore(fit_score=0.0, reasoning="no score returned"))
        return scores
    except Exception as e:
        logger.warning("score parse failed: %s", e)
        return [JobScore(fit_score=0.0, reasoning="parse error") for _ in range(expected)]
