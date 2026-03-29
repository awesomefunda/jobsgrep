"""Prompt templates for job scoring."""

SCORE_SYSTEM = """\
You are a job fit evaluator. Score job descriptions against a candidate's requirements.
Return ONLY a JSON array, one object per job. No markdown fences, no explanation.
"""

SCORE_BATCH_TEMPLATE = """\
Evaluate these {count} jobs against the candidate's search requirements.

## Candidate Requirements
{requirements}

## Jobs to Evaluate
{jobs_block}

## Instructions
For each job, return:
{{
  "job_index": 0,
  "fit_score": 0.0-1.0,
  "reasoning": "1-2 sentence explanation",
  "matching_skills": ["skills from the JD that match requirements"],
  "missing_skills": ["required skills not mentioned in JD"],
  "red_flags": ["e.g. 'requires 10+ yrs management', 'internship-level scope'"],
  "salary_range": "extracted salary if present, else null"
}}

Scoring guide:
- 0.9-1.0: Excellent fit — title matches, most skills present, level appropriate
- 0.8-0.9: Good fit — title matches, some skills present, minor gaps
- 0.7-0.8: Possible fit — related title, transferable skills
- Below 0.7: Poor fit — wrong level, missing core skills, wrong domain

Return a JSON array of {count} objects in the same order as the input jobs.
"""


def build_requirements_block(query) -> str:
    """Build a human-readable requirements block from ParsedQuery."""
    lines = []
    if query.titles:
        lines.append(f"Titles sought: {', '.join(query.titles)}")
    if query.title_variations:
        lines.append(f"Also acceptable: {', '.join(query.title_variations)}")
    if query.min_level:
        lines.append(f"Minimum level: {query.min_level}")
    if query.skills_required:
        lines.append(f"Required skills: {', '.join(query.skills_required)}")
    if query.skills_preferred:
        lines.append(f"Preferred skills: {', '.join(query.skills_preferred)}")
    if query.locations:
        lines.append(f"Locations: {', '.join(query.locations)}")
    if query.remote_ok:
        lines.append("Remote: OK")
    if query.exclude_keywords:
        lines.append(f"Must NOT include: {', '.join(query.exclude_keywords)}")
    return "\n".join(lines)


def build_jobs_block(jobs: list) -> str:
    """Format jobs for the scoring prompt."""
    blocks = []
    for i, job in enumerate(jobs):
        parts = [
            f"[Job {i}]",
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location}",
        ]
        if job.salary_text:
            parts.append(f"Salary: {job.salary_text}")
        if job.description:
            parts.append(f"Description: {job.description[:800]}")
        blocks.append("\n".join(parts))
    return "\n\n---\n\n".join(blocks)
