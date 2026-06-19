"""Aggregate insights over the cached job corpus.

Pure, LLM-free counting used by the homepage dashboard: how many jobs per role
family / seniority level / location, the remote split, and the top hiring
companies. Everything is derived from the in-memory corpus + the taxonomy
classifier, so it's cheap to recompute on each request.
"""
from __future__ import annotations

from collections import Counter

from .models import RawJob
from .taxonomy import (
    LEVEL_ORDER,
    ROLE_ORDER,
    classify_level,
    classify_role_family,
)

# Family display name → URL slug (kept in sync with export.ROLE_SLUGS).
ROLE_SLUGS: dict[str, str] = {
    "Software Engineering": "software-engineering",
    "Data & ML": "data-ml",
    "Infrastructure & DevOps": "infrastructure-devops",
    "Security": "security",
    "QA & Test": "qa-test",
    "Design": "design",
    "Engineering Management": "engineering-management",
    "Product Management": "product-management",
    "Program & Project Management": "program-project-management",
    "Other": "other",
}
SLUG_TO_ROLE: dict[str, str] = {v: k for k, v in ROLE_SLUGS.items()}


def _city(location: str) -> str:
    """Normalize a job location to a coarse bucket for charting."""
    if not location:
        return "Unspecified"
    loc = location.strip()
    low = loc.lower()
    if "remote" in low:
        return "Remote"
    # Take the part before the first comma (city), title-cased.
    head = loc.split(",")[0].strip()
    return head.title() if head else "Unspecified"


def compute_stats(jobs: list[RawJob], top_n: int = 12) -> dict:
    """Return a JSON-serializable bundle of corpus aggregates."""
    fam_counts: Counter = Counter()
    level_counts: Counter = Counter()
    city_counts: Counter = Counter()
    company_counts: Counter = Counter()
    source_counts: Counter = Counter()
    remote = 0

    for j in jobs:
        fam_counts[classify_role_family(j.title)] += 1
        level_counts[classify_level(j.title)] += 1
        city_counts[_city(j.location)] += 1
        if j.company:
            company_counts[j.company] += 1
        if j.source:
            source_counts[j.source] += 1
        if j.remote or "remote" in (j.location or "").lower():
            remote += 1

    by_role = [
        {"label": fam, "slug": ROLE_SLUGS.get(fam, "other"), "count": fam_counts[fam]}
        for fam in ROLE_ORDER if fam_counts.get(fam)
    ]
    by_level = [
        {"label": lvl, "count": level_counts[lvl]}
        for lvl in LEVEL_ORDER if level_counts.get(lvl)
    ]
    by_location = [
        {"label": c, "count": n}
        for c, n in city_counts.most_common(top_n) if c != "Unspecified"
    ]
    top_companies = [
        {"label": c, "count": n} for c, n in company_counts.most_common(top_n)
    ]
    by_source = [
        {"label": s, "count": n} for s, n in source_counts.most_common()
    ]

    total = len(jobs)
    return {
        "total": total,
        "remote": {"remote": remote, "onsite": total - remote},
        "by_role_family": by_role,
        "by_level": by_level,
        "by_location": by_location,
        "top_companies": top_companies,
        "by_source": by_source,
    }
