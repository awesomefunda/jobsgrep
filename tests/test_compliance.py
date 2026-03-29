"""Verify mode enforcement: scrapers cannot be called in PRIVATE/PUBLIC mode."""
import os
import pytest
from unittest.mock import patch


def _set_mode(mode: str):
    """Patch settings to use a specific mode."""
    from jobsgrep.config import get_settings
    get_settings.cache_clear()
    return patch.dict(os.environ, {"JOBSGREP_MODE": mode, "ALLOW_SCRAPE": "false"})


def test_scraper_blocked_in_public_mode():
    with _set_mode("PUBLIC"):
        from jobsgrep.config import get_settings
        get_settings.cache_clear()
        from jobsgrep.config import SOURCE_REGISTRY
        from jobsgrep.legal.compliance import SourceNotAllowedError, assert_source_allowed

        jobspy_meta = SOURCE_REGISTRY["jobspy"]
        with pytest.raises(SourceNotAllowedError):
            assert_source_allowed(jobspy_meta)


def test_scraper_allowed_in_private_mode():
    with _set_mode("PRIVATE"):
        from jobsgrep.config import get_settings
        get_settings.cache_clear()
        from jobsgrep.config import SOURCE_REGISTRY
        from jobsgrep.legal.compliance import assert_source_allowed

        # PRIVATE = your own server; scrapers are fine
        assert_source_allowed(SOURCE_REGISTRY["jobspy"])


def test_scraper_allowed_in_local_mode():
    with _set_mode("LOCAL"):
        from jobsgrep.config import get_settings
        get_settings.cache_clear()
        from jobsgrep.config import SOURCE_REGISTRY
        from jobsgrep.legal.compliance import assert_source_allowed

        jobspy_meta = SOURCE_REGISTRY["jobspy"]
        # Should not raise
        assert_source_allowed(jobspy_meta)


def test_scrapers_allowed_in_private_mode():
    """Scrapers are allowed in PRIVATE mode — it's still your own server."""
    with patch.dict(os.environ, {"JOBSGREP_MODE": "PRIVATE", "ALLOW_SCRAPE": "false"}):
        from jobsgrep.config import get_settings
        get_settings.cache_clear()
        from jobsgrep.config import SOURCE_REGISTRY
        from jobsgrep.legal.compliance import assert_source_allowed

        for name in ("jobspy", "levels_fyi", "teamblind"):
            assert_source_allowed(SOURCE_REGISTRY[name])  # should not raise


def test_scrapers_blocked_in_public_mode():
    """Scrapers must not run in PUBLIC mode — IP ban risk from concurrent users + no caching."""
    with patch.dict(os.environ, {"JOBSGREP_MODE": "PUBLIC", "ALLOW_SCRAPE": "false"}):
        from jobsgrep.config import get_settings
        get_settings.cache_clear()
        from jobsgrep.config import SOURCE_REGISTRY
        from jobsgrep.legal.compliance import assert_source_allowed, SourceNotAllowedError

        for name in ("jobspy", "levels_fyi", "teamblind"):
            with pytest.raises(SourceNotAllowedError):
                assert_source_allowed(SOURCE_REGISTRY[name])


def test_public_apis_allowed_in_all_modes():
    for mode in ("LOCAL", "PRIVATE", "PUBLIC"):
        with _set_mode(mode):
            from jobsgrep.config import get_settings
            get_settings.cache_clear()
            from jobsgrep.config import SOURCE_REGISTRY
            from jobsgrep.legal.compliance import assert_source_allowed

            for name in ("greenhouse", "lever", "ashby", "hn_hiring"):
                assert_source_allowed(SOURCE_REGISTRY[name])


def test_enabled_sources_public_excludes_scrapers():
    with _set_mode("PUBLIC"):
        from jobsgrep.config import get_settings, get_enabled_sources
        get_settings.cache_clear()
        enabled = get_enabled_sources()
        assert "jobspy" not in enabled
        assert "greenhouse" in enabled
        assert "lever" in enabled


def test_enabled_sources_local_includes_scrapers():
    with _set_mode("LOCAL"):
        from jobsgrep.config import get_settings, get_enabled_sources
        get_settings.cache_clear()
        enabled = get_enabled_sources()
        assert "jobspy" in enabled
        assert "greenhouse" in enabled
