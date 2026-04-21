"""Prompt templates for job scoring."""

SCORE_SYSTEM = """\
You are a job fit evaluator. Score job descriptions against candidate requirements.
Return ONLY a JSON array, one object per job, no markdown, no explanation.
"""

# Tighter prompt: removed scoring guide paragraph (saves ~80 tokens per batch),
# reasoning capped at 10 words (saves ~30 tokens per job).
SCORE_BATCH_TEMPLATE = """\
Score these {count} jobs against the candidate requirements below.

## Requirements
{requirements}

## Jobs
{jobs_block}

Return a JSON array of exactly {count} objects in input order:
{{"job_index":0,"fit_score":0.0-1.0,"reasoning":"≤10 words","matching_skills":[],"missing_skills":[],"red_flags":[],"salary_range":null,"role_type":"e.g. AI Platform|Agentic AI|ML Engineer|Data Engineer|Solutions Architect|Technical PM|DevOps|Backend|Frontend|Full-Stack|Other","seniority_level":"Junior|Mid|Senior|Staff|Principal|Director|VP|Unknown"}}
"""


def build_requirements_block(query, resume_summary: str | None = None) -> str:
    lines = []
    if query.titles:
        lines.append(f"Titles: {', '.join(query.titles)}")
    if query.title_variations:
        lines.append(f"Also: {', '.join(query.title_variations)}")
    if query.min_level:
        lines.append(f"Level: {query.min_level}+")
    if query.skills_required:
        lines.append(f"Must have: {', '.join(query.skills_required)}")
    if query.skills_preferred:
        lines.append(f"Nice to have: {', '.join(query.skills_preferred)}")
    if query.locations:
        lines.append(f"Locations: {', '.join(query.locations)}")
    if query.remote_ok:
        lines.append("Remote: yes")
    if query.exclude_keywords:
        lines.append(f"Exclude: {', '.join(query.exclude_keywords)}")
    if resume_summary:
        lines.append(f"Candidate background: {resume_summary[:400]}")
    return "\n".join(lines)


def build_jobs_block(jobs: list) -> str:
    blocks = []
    for i, job in enumerate(jobs):
        parts = [f"[{i}] {job.title} @ {job.company} ({job.location})"]
        if job.salary_text:
            parts.append(f"Salary: {job.salary_text}")
        if job.description:
            # 400 chars (was 800) — title + first paragraph captures fit signals
            parts.append(job.description[:400].replace("\n", " "))
        blocks.append(" | ".join(parts))
    return "\n".join(blocks)
