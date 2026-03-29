"""Keyword filter + LLM scoring pipeline (Gemini → Groq fallback)."""
from __future__ import annotations

import asyncio
import json
import logging

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

BATCH_SIZE = 5


def keyword_filter(jobs: list[RawJob], query: ParsedQuery) -> list[RawJob]:
    """Zero-cost pre-filter: remove jobs with excluded keywords or clearly wrong level."""
    exclude = [kw.lower() for kw in query.exclude_keywords]
    filtered = []
    for job in jobs:
        text = f"{job.title} {job.description}".lower()
        if any(kw in text for kw in exclude):
            continue
        filtered.append(job)
    return filtered


async def score_jobs(
    jobs: list[RawJob],
    query: ParsedQuery,
    progress_cb=None,
) -> list[ScoredJob]:
    """Score all jobs against query. Returns only jobs meeting MIN_FIT_SCORE."""
    settings = get_settings()
    min_score = settings.min_fit_score

    # Step 1: keyword filter
    jobs = keyword_filter(jobs, query)
    logger.info("scoring: %d jobs after keyword filter", len(jobs))

    results: list[ScoredJob] = []
    requirements = build_requirements_block(query)

    for i in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[i : i + BATCH_SIZE]
        if progress_cb:
            await progress_cb(f"Scoring jobs {i+1}–{min(i+BATCH_SIZE, len(jobs))} of {len(jobs)}...")

        scores = await _score_batch(batch, requirements)
        for job, score in zip(batch, scores):
            if score.fit_score >= min_score:
                results.append(ScoredJob(job=job, score=score))

    results.sort(key=lambda s: s.score.fit_score, reverse=True)
    logger.info("scoring complete: %d/%d jobs passed threshold %.2f", len(results), len(jobs), min_score)
    return results


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
        # Pad if LLM returned fewer
        while len(scores) < expected:
            scores.append(JobScore(fit_score=0.0, reasoning="no score returned"))
        return scores
    except Exception as e:
        logger.warning("score parse failed: %s", e)
        return [JobScore(fit_score=0.0, reasoning="parse error") for _ in range(expected)]
