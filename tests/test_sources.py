"""Tests for source utilities (no network calls)."""
import pytest
from jobsgrep.sources.base import job_id
from jobsgrep.discovery.ats_prober import derive_slug_variants


def test_job_id_deterministic():
    a = job_id("Stripe", "Software Engineer", "San Francisco")
    b = job_id("Stripe", "Software Engineer", "San Francisco")
    assert a == b
    assert len(a) == 12


def test_job_id_case_insensitive():
    a = job_id("Stripe", "Software Engineer", "San Francisco")
    b = job_id("stripe", "software engineer", "san francisco")
    assert a == b


def test_job_id_differentiates_companies():
    a = job_id("Stripe", "SWE", "SF")
    b = job_id("Airbnb", "SWE", "SF")
    assert a != b


def test_derive_slug_variants_basic():
    slugs = derive_slug_variants("Stripe")
    assert "stripe" in slugs


def test_derive_slug_variants_multiword():
    slugs = derive_slug_variants("Weights & Biases")
    assert any("-" in s or s.replace("-", "") for s in slugs)


def test_derive_slug_variants_strips_inc():
    slugs = derive_slug_variants("Acme Corp Inc.")
    for s in slugs:
        assert "inc" not in s.lower() or "acme" in s.lower()


def test_derive_slug_variants_no_duplicates():
    slugs = derive_slug_variants("OpenAI")
    assert len(slugs) == len(set(slugs))


def test_keyword_match_excludes():
    from jobsgrep.models import DataSourceType, ParsedQuery, RawJob
    from jobsgrep.sources.base import BaseSource

    class _FakeSource(BaseSource):
        source_name = "greenhouse"
        async def fetch_jobs(self, query): return []

    src = _FakeSource()
    query = ParsedQuery(titles=["Engineer"], exclude_keywords=["manager"])
    job_ok = RawJob(id="a", title="Software Engineer", company="X", source_type=DataSourceType.PUBLIC_API)
    job_bad = RawJob(id="b", title="Engineering Manager", company="X", source_type=DataSourceType.PUBLIC_API)

    assert src._keyword_match(job_ok, query) is True
    assert src._keyword_match(job_bad, query) is False
