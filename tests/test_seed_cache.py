"""Regression tests: seed cache must return results for key city+role queries.

These tests guard against:
- Cities missing from _fallback_parse location aliases
- Duplicate/stale seeds shadowing the larger one
- Fuzzy matcher rejecting valid city→remote fallback for rare titles
- get_scored_fuzzy return-type changes breaking callers

No LLM calls, no network. Uses local seed files only.
"""
import pytest
from jobsgrep.nlp.parser import _fallback_parse
from jobsgrep.job_cache import get_scored_fuzzy, prime_label_index


@pytest.fixture(scope="module", autouse=True)
def load_seeds():
    """Populate the in-memory label index from seed files before any test runs."""
    prime_label_index()


# ── Parser: location extraction ──────────────────────────────────────────────

@pytest.mark.parametrize("query,expected_loc", [
    ("Software engineer job in bay area",     "San Francisco Bay Area"),
    ("Software engineer job in seattle",      "Seattle"),
    ("Software development job in austin",    "Austin, Texas"),
    ("Software engineer in san diego",        "San Diego"),
    ("Backend engineer new york",             "New York City"),
    ("ML engineer remote",                    "Remote"),
    ("Staff engineer in chicago",             "Chicago"),
    ("Data engineer in boston",               "Boston"),
])
def test_fallback_parse_location(query, expected_loc):
    parsed = _fallback_parse(query)
    assert expected_loc in parsed.locations, (
        f"Query {query!r}: expected location {expected_loc!r}, got {parsed.locations}"
    )


@pytest.mark.parametrize("query,expected_title_fragment", [
    ("Software development manager job in bay area", "Manager"),
    ("Software development manager job in seattle",  "Manager"),
    ("Software engineer job in bay area",            "Engineer"),
    ("Software development job in austin",           "Engineer"),
])
def test_fallback_parse_title(query, expected_title_fragment):
    parsed = _fallback_parse(query)
    assert any(expected_title_fragment in t for t in parsed.titles), (
        f"Query {query!r}: expected title containing {expected_title_fragment!r}, got {parsed.titles}"
    )


# ── Seed cache: non-zero results for each key query ──────────────────────────

@pytest.mark.parametrize("query,min_jobs", [
    ("Software engineer job in bay area",   50),
    ("Software engineer job in seattle",     5),
    ("Software development job in austin",  10),
])
def test_seed_cache_returns_results(query, min_jobs):
    parsed = _fallback_parse(query)
    result = get_scored_fuzzy(parsed)
    assert result is not None, (
        f"Query {query!r}: get_scored_fuzzy returned None (no seed matched)"
    )
    jobs, hot_skills = result
    assert len(jobs) >= min_jobs, (
        f"Query {query!r}: expected >= {min_jobs} jobs, got {len(jobs)}"
    )


# EM queries have no valid city seeds yet — marked xfail until proper seeds are generated.
# To fix: run `python scripts/seed_from_cache.py` after fetching EM jobs locally,
# then commit the new seed files under data/seed/ and jobsgrep/seed_data/.
@pytest.mark.xfail(reason="No Engineering Manager seeds for city queries yet", strict=False)
@pytest.mark.parametrize("query", [
    "Software development manager job in bay area",
    "Software development manager job in seattle",
])
def test_seed_cache_em_city_queries(query):
    parsed = _fallback_parse(query)
    result = get_scored_fuzzy(parsed)
    assert result is not None, f"No seed for {query!r}"
    jobs, _ = result
    assert len(jobs) >= 1


@pytest.mark.parametrize("query,forbidden_title_fragments", [
    # Manager queries must NOT return generic SWE jobs
    ("Software development manager job in bay area",  ["software engineer", "backend engineer", "frontend engineer"]),
    ("Software development manager job in seattle",   ["software engineer", "backend engineer", "frontend engineer"]),
])
def test_seed_cache_title_relevance(query, forbidden_title_fragments):
    """Jobs returned must match the intent of the query (no wrong-role seeds)."""
    parsed = _fallback_parse(query)
    result = get_scored_fuzzy(parsed)
    if result is None:
        pytest.skip("No seed for this query yet — add a seed to enable this check")
    jobs, _ = result
    wrong = [
        j.job.title for j in jobs
        if any(f in j.job.title.lower() for f in forbidden_title_fragments)
    ]
    majority_wrong = len(wrong) > len(jobs) * 0.5
    assert not majority_wrong, (
        f"Query {query!r}: >50% of returned jobs are wrong-role titles.\n"
        f"Wrong titles ({len(wrong)}/{len(jobs)}): {wrong[:5]}"
    )


def test_seed_cache_returns_tuple():
    """get_scored_fuzzy must return (list, list) — not a plain list or None for known queries."""
    parsed = _fallback_parse("Software engineer job in bay area")
    result = get_scored_fuzzy(parsed)
    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    assert len(result) == 2, f"Expected 2-tuple, got length {len(result)}"
    jobs, hot_skills = result
    assert isinstance(jobs, list)
    assert isinstance(hot_skills, list)


def test_hot_skills_are_dicts():
    """hot_skills entries must be dicts with 'skill' and 'count' keys (not tuples)."""
    parsed = _fallback_parse("Software engineer job in bay area")
    result = get_scored_fuzzy(parsed)
    assert result is not None
    _, hot_skills = result
    if hot_skills:
        first = hot_skills[0]
        assert isinstance(first, dict), f"Expected dict, got {type(first)}: {first!r}"
        assert "skill" in first and "count" in first, (
            f"hot_skills entry missing keys: {first!r}"
        )


# ── Live API smoke test (skipped unless JOBSGREP_LIVE_TEST=1) ─────────────────

import os
import time

pytestmark_live = pytest.mark.skipif(
    os.environ.get("JOBSGREP_LIVE_TEST") != "1",
    reason="Set JOBSGREP_LIVE_TEST=1 to run live API tests against jobsgrep.com",
)


@pytest.mark.parametrize("query,min_jobs", [
    ("Software engineer job in bay area",            50),
    ("Software engineer job in seattle",              5),
    ("Software development manager job in bay area",  1),
    ("Software development manager job in seattle",   1),
    ("Software development job in austin",           10),
])
@pytestmark_live
def test_live_api(query, min_jobs):
    """End-to-end: POST /api/search → stream → assert scored_jobs >= min_jobs."""
    import urllib.request, urllib.parse, json

    base = "https://jobsgrep.com"

    # POST /api/search
    payload = json.dumps({"query": query}).encode()
    req = urllib.request.Request(f"{base}/api/search", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        task = json.loads(resp.read())
    task_id = task["task_id"]

    # GET /api/stream (read until done event)
    stream_url = f"{base}/api/stream/{task_id}?query={urllib.parse.quote(query)}"
    req2 = urllib.request.Request(stream_url, headers={"Accept": "text/event-stream"})
    scored_jobs = None
    with urllib.request.urlopen(req2, timeout=30) as resp:
        for line in resp:
            line = line.decode().strip()
            if line.startswith("event: done"):
                pass
            elif line.startswith("data:") and scored_jobs is None:
                try:
                    data = json.loads(line[5:].strip())
                    if data.get("status") in ("complete", "failed"):
                        scored_jobs = data.get("scored_jobs", 0)
                        break
                except Exception:
                    pass

    assert scored_jobs is not None, f"No done event received for {query!r}"
    assert scored_jobs >= min_jobs, (
        f"Live API {query!r}: expected >= {min_jobs} scored jobs, got {scored_jobs}"
    )
