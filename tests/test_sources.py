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


def test_jsearch_prefetch_queries_override():
    from jobsgrep.sources.jsearch import JSearchSource
    from jobsgrep.models import ParsedQuery
    from unittest.mock import patch, MagicMock, AsyncMock

    source = JSearchSource()
    source.settings = MagicMock()
    source.settings.jsearch_api_key = "test_key"
    source.settings.jsearch_country = "us"
    source.settings.jsearch_num_pages = 1
    source.settings.prefetch_queries = "Test Query 1, Test Query 2"

    with patch.object(source, "_fetch_term", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []
        import asyncio
        asyncio.run(source.fetch_jobs(ParsedQuery()))
        
        called_terms = [call.args[0] for call in mock_fetch.call_args_list]
        assert "Test Query 1" in called_terms
        assert "Test Query 2" in called_terms


def test_jobspy_prefetch_queries_override():
    from jobsgrep.sources.jobspy_source import JobSpySource
    from jobsgrep.models import ParsedQuery
    from unittest.mock import patch, MagicMock, AsyncMock

    source = JobSpySource()
    source.settings = MagicMock()
    source.settings.jobspy_sites = "indeed,google"
    source.settings.jobspy_results_per_term = 10
    source.settings.jobspy_hours_old = 24
    source.settings.jobspy_location = "United States"
    source.settings.prefetch_queries = "Test Query 3, Test Query 4"

    # We patch jobspy scrape_jobs to not import jobspy
    with patch("jobspy.scrape_jobs") as mock_scrape:
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock()
            mock_loop.return_value.run_in_executor = mock_executor
            
            import asyncio
            asyncio.run(source.fetch_jobs(ParsedQuery()))
            
            # Verify the call was made for each term
            assert mock_executor.call_count == 2
            called_terms = [call.args[1].__defaults__[0] for call in mock_executor.call_args_list]
            assert "Test Query 3" in called_terms
            assert "Test Query 4" in called_terms

