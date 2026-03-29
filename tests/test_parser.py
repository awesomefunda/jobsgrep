"""Tests for NL query parser fallback (no LLM required)."""
import pytest
from jobsgrep.nlp.parser import _fallback_parse


def test_fallback_extracts_remote():
    q = _fallback_parse("Staff SWE, remote, Python and Go")
    assert q.remote_ok is True
    assert "Remote" in q.locations


def test_fallback_extracts_title():
    q = _fallback_parse("Senior software engineer role in NYC")
    assert len(q.titles) > 0
    assert any("engineer" in t.lower() for t in q.titles)


def test_fallback_no_crash_on_empty():
    q = _fallback_parse("")
    assert q.titles == ["Software Engineer"]


def test_fallback_default_title():
    q = _fallback_parse("Looking for a job in tech")
    assert q.titles == ["Software Engineer"]


def test_fallback_preserves_raw_query():
    raw = "Staff ML Engineer remote Bay Area"
    q = _fallback_parse(raw)
    assert q.raw_query == raw
