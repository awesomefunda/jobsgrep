"""Tests for NL query parser: guardrails, fallback title/location extraction.

Covers three role families × multiple locations/remote so any regression in
title mapping or location aliases is caught immediately.  No LLM, no network.
"""
import pytest
from jobsgrep.nlp.parser import _fallback_parse, is_out_of_scope


# ─── Guardrails ───────────────────────────────────────────────────────────────

class TestGuardrails:
    """is_out_of_scope must block non-tech queries and pass all tech queries."""

    @pytest.mark.parametrize("query", [
        "gardener jobs in bay area",
        "looking for a gardener in seattle",
        "nurse position in NYC",
        "registered nurse remote",
        "chef wanted in Chicago",
        "plumber in Austin",
        "electrician jobs near me",
        "carpenter remote",
        "truck driver in Los Angeles",
        "mechanic in Denver",
        "bartender jobs downtown",
        "lawyer position in Washington DC",
        "farmer in California",
        "firefighter jobs in Boston",
        "cashier at retail store",
    ])
    def test_rejects_non_tech(self, query):
        assert is_out_of_scope(query), f"Should reject non-tech query: {query!r}"

    @pytest.mark.parametrize("query", [
        # Software Engineer family
        "software engineer in bay area",
        "senior software engineer remote",
        "SWE NYC",
        "staff engineer Seattle",
        "backend engineer Chicago",
        "frontend engineer Austin",
        "fullstack developer remote",
        "ML engineer bay area",
        "data engineer NYC",
        # Director family
        "software director in bay area",
        "director of engineering remote",
        "engineering director NYC",
        "technical director Seattle",
        "senior director of engineering bay area",
        # Manager family
        "engineering manager bay area",
        "software development manager remote",
        "EM NYC",
        "SDM in Seattle",
        "dev manager Chicago",
        # Edge cases that must NOT be blocked
        "security engineer remote",    # "security" is tech
        "technical writer bay area",   # "technical" is tech signal
        "product manager NYC",         # "product" is tech signal
        "data scientist remote",
        "cloud architect bay area",
        "devops engineer remote",
        "platform engineer NYC",
    ])
    def test_passes_tech_queries(self, query):
        assert not is_out_of_scope(query), f"Should NOT reject tech query: {query!r}"


# ─── Software Engineer family ─────────────────────────────────────────────────

class TestSoftwareEngineerParsing:
    """_fallback_parse must extract the right title for SWE variants."""

    @pytest.mark.parametrize("query,expected_title", [
        ("software engineer in bay area",          "Software Engineer"),
        ("software engineer NYC",                  "Software Engineer"),
        ("software engineer in seattle",           "Software Engineer"),
        ("software engineer remote",               "Software Engineer"),
        ("SWE bay area",                           "Software Engineer"),
        ("SDE in austin",                          "Software Engineer"),
        ("senior software engineer bay area",      "Senior Software Engineer"),
        ("senior SWE remote",                      "Senior Software Engineer"),
        ("Sr. software engineer NYC",              "Senior Software Engineer"),
        ("staff software engineer remote",         "Staff Software Engineer"),
        ("staff SWE bay area",                     "Staff Software Engineer"),
        ("staff engineer seattle",                 "Staff Software Engineer"),
        ("principal software engineer NYC",        "Principal Software Engineer"),
        ("principal engineer remote",              "Principal Software Engineer"),
        ("backend engineer bay area",              "Backend Engineer"),
        ("back-end engineer remote",               "Backend Engineer"),
        ("frontend engineer NYC",                  "Frontend Engineer"),
        ("front-end engineer seattle",             "Frontend Engineer"),
        ("ML engineer bay area",                   "Machine Learning Engineer"),
        ("machine learning engineer remote",       "Machine Learning Engineer"),
        ("MLE NYC",                                "Machine Learning Engineer"),
        ("data engineer remote",                   "Data Engineer"),
        ("data scientist bay area",                "Data Scientist"),
        ("devops engineer remote",                 "DevOps Engineer"),
        ("SRE NYC",                                "DevOps Engineer"),
        ("platform engineer seattle",              "DevOps Engineer"),
    ])
    def test_title(self, query, expected_title):
        parsed = _fallback_parse(query)
        assert expected_title in parsed.titles, (
            f"Query {query!r}: expected {expected_title!r} in titles, got {parsed.titles}"
        )

    @pytest.mark.parametrize("query,expected_loc", [
        ("software engineer in bay area",   "San Francisco Bay Area"),
        ("software engineer NYC",           "New York City"),
        ("software engineer in seattle",    "Seattle"),
        ("software engineer remote",        "Remote"),
        ("SWE in austin",                   "Austin, Texas"),
        ("backend engineer chicago",        "Chicago"),
        ("data engineer in boston",         "Boston"),
        ("ML engineer in san diego",        "San Diego"),
        ("platform engineer in denver",     "Denver"),
        ("frontend engineer in miami",      "Miami"),
    ])
    def test_location(self, query, expected_loc):
        parsed = _fallback_parse(query)
        assert expected_loc in parsed.locations, (
            f"Query {query!r}: expected location {expected_loc!r}, got {parsed.locations}"
        )

    def test_remote_flag(self):
        parsed = _fallback_parse("senior software engineer remote")
        assert parsed.remote_ok is True
        assert "Remote" in parsed.locations

    def test_non_remote_flag(self):
        parsed = _fallback_parse("software engineer in bay area")
        assert parsed.remote_ok is False


# ─── Director family ──────────────────────────────────────────────────────────

class TestDirectorParsing:
    """Director-level titles must all map to Director of Engineering or Senior variant."""

    @pytest.mark.parametrize("query,expected_title", [
        ("software director in bay area",              "Director of Engineering"),
        ("software director NYC",                      "Director of Engineering"),
        ("software director remote",                   "Director of Engineering"),
        ("software director in seattle",               "Director of Engineering"),
        ("director of engineering bay area",           "Director of Engineering"),
        ("director of engineering remote",             "Director of Engineering"),
        ("engineering director NYC",                   "Director of Engineering"),
        ("dir of eng seattle",                         "Director of Engineering"),
        ("director of software engineering remote",    "Director of Engineering"),
        ("technical director bay area",                "Director of Engineering"),
        ("tech director NYC",                          "Director of Engineering"),
        ("senior director of engineering bay area",    "Senior Director of Engineering"),
        ("senior director of engineering remote",      "Senior Director of Engineering"),
    ])
    def test_title(self, query, expected_title):
        parsed = _fallback_parse(query)
        assert expected_title in parsed.titles, (
            f"Query {query!r}: expected {expected_title!r} in titles, got {parsed.titles}"
        )

    @pytest.mark.parametrize("query,expected_loc", [
        ("software director in bay area",       "San Francisco Bay Area"),
        ("director of engineering NYC",         "New York City"),
        ("engineering director in seattle",     "Seattle"),
        ("technical director remote",           "Remote"),
        ("software director in austin",         "Austin, Texas"),
        ("director of engineering in chicago",  "Chicago"),
    ])
    def test_location(self, query, expected_loc):
        parsed = _fallback_parse(query)
        assert expected_loc in parsed.locations, (
            f"Query {query!r}: expected location {expected_loc!r}, got {parsed.locations}"
        )

    def test_remote_flag(self):
        parsed = _fallback_parse("director of engineering remote")
        assert parsed.remote_ok is True
        assert "Remote" in parsed.locations

    def test_not_classified_as_swe(self):
        """Director queries must never produce a Software Engineer title."""
        director_queries = [
            "software director in bay area",
            "director of engineering NYC",
            "technical director remote",
        ]
        for query in director_queries:
            parsed = _fallback_parse(query)
            swe_titles = [t for t in parsed.titles if t == "Software Engineer"]
            assert not swe_titles, (
                f"Query {query!r}: should not produce 'Software Engineer' title, got {parsed.titles}"
            )


# ─── Engineering Manager family ──────────────────────────────────────────────

class TestManagerParsing:
    """Manager-level titles must all map to Engineering Manager or Senior variant."""

    @pytest.mark.parametrize("query,expected_title", [
        ("engineering manager bay area",               "Engineering Manager"),
        ("engineering manager NYC",                    "Engineering Manager"),
        ("engineering manager remote",                 "Engineering Manager"),
        ("engineering manager in seattle",             "Engineering Manager"),
        ("software manager remote",                    "Engineering Manager"),
        ("dev manager bay area",                       "Engineering Manager"),
        ("software development manager NYC",           "Engineering Manager"),
        ("SDM in seattle",                             "Engineering Manager"),
        ("tech lead manager remote",                   "Engineering Manager"),
        ("EM bay area",                                "Engineering Manager"),
        ("senior engineering manager remote",          "Senior Engineering Manager"),
        ("senior engineering manager bay area",        "Senior Engineering Manager"),
    ])
    def test_title(self, query, expected_title):
        parsed = _fallback_parse(query)
        assert expected_title in parsed.titles, (
            f"Query {query!r}: expected {expected_title!r} in titles, got {parsed.titles}"
        )

    @pytest.mark.parametrize("query,expected_loc", [
        ("engineering manager in bay area",     "San Francisco Bay Area"),
        ("engineering manager NYC",             "New York City"),
        ("software manager in seattle",         "Seattle"),
        ("dev manager remote",                  "Remote"),
        ("SDM in austin",                       "Austin, Texas"),
        ("engineering manager in chicago",      "Chicago"),
        ("EM in boston",                        "Boston"),
    ])
    def test_location(self, query, expected_loc):
        parsed = _fallback_parse(query)
        assert expected_loc in parsed.locations, (
            f"Query {query!r}: expected location {expected_loc!r}, got {parsed.locations}"
        )

    def test_remote_flag(self):
        parsed = _fallback_parse("engineering manager remote")
        assert parsed.remote_ok is True
        assert "Remote" in parsed.locations

    def test_not_classified_as_swe(self):
        """Manager queries must never produce a Software Engineer title."""
        manager_queries = [
            "engineering manager in bay area",
            "SDM NYC",
            "dev manager remote",
        ]
        for query in manager_queries:
            parsed = _fallback_parse(query)
            swe_titles = [t for t in parsed.titles if t == "Software Engineer"]
            assert not swe_titles, (
                f"Query {query!r}: should not produce 'Software Engineer' title, got {parsed.titles}"
            )


# ─── Product Manager / TPM family ────────────────────────────────────────────

class TestProductManagerParsing:
    """PM, TPM, and program manager titles must all parse correctly."""

    @pytest.mark.parametrize("query,expected_title", [
        ("product manager bay area",               "Product Manager"),
        ("product manager NYC",                    "Product Manager"),
        ("product manager remote",                 "Product Manager"),
        ("PM in seattle",                          "Product Manager"),
        ("senior product manager bay area",        "Senior Product Manager"),
        ("senior PM remote",                       "Senior Product Manager"),
        ("Sr. product manager NYC",                "Senior Product Manager"),
        ("principal product manager remote",       "Principal Product Manager"),
        ("principal PM bay area",                  "Principal Product Manager"),
        ("group product manager NYC",              "Group Product Manager"),
        ("GPM remote",                             "Group Product Manager"),
        ("technical program manager bay area",     "Technical Program Manager"),
        ("TPM remote",                             "Technical Program Manager"),
        ("TPM NYC",                                "Technical Program Manager"),
        ("program manager seattle",                "Program Manager"),
        ("director of product bay area",           "Director of Product"),
        ("product director remote",                "Director of Product"),
        ("vp of product NYC",                      "VP of Product"),
        ("VP product remote",                      "VP of Product"),
    ])
    def test_title(self, query, expected_title):
        parsed = _fallback_parse(query)
        assert expected_title in parsed.titles, (
            f"Query {query!r}: expected {expected_title!r} in titles, got {parsed.titles}"
        )

    @pytest.mark.parametrize("query,expected_loc", [
        ("product manager in bay area",    "San Francisco Bay Area"),
        ("PM in NYC",                      "New York City"),
        ("senior PM in seattle",           "Seattle"),
        ("TPM remote",                     "Remote"),
        ("product manager in austin",      "Austin, Texas"),
        ("product manager in chicago",     "Chicago"),
    ])
    def test_location(self, query, expected_loc):
        parsed = _fallback_parse(query)
        assert expected_loc in parsed.locations, (
            f"Query {query!r}: expected location {expected_loc!r}, got {parsed.locations}"
        )

    def test_pm_not_classified_as_swe(self):
        for query in ["product manager bay area", "TPM remote", "senior PM NYC"]:
            parsed = _fallback_parse(query)
            assert "Software Engineer" not in parsed.titles, (
                f"Query {query!r}: should not produce 'Software Engineer', got {parsed.titles}"
            )

    def test_pm_not_blocked_by_guardrail(self):
        for query in ["PM remote", "product manager NYC", "TPM bay area", "senior PM seattle"]:
            assert not is_out_of_scope(query), f"Guardrail wrongly blocked: {query!r}"


# ─── VP / Senior leadership ───────────────────────────────────────────────────

class TestVPParsing:
    @pytest.mark.parametrize("query,expected_title", [
        ("VP of engineering bay area",  "VP of Engineering"),
        ("VP eng remote",               "VP of Engineering"),
        ("vp of engineering NYC",       "VP of Engineering"),
    ])
    def test_title(self, query, expected_title):
        parsed = _fallback_parse(query)
        assert expected_title in parsed.titles, (
            f"Query {query!r}: expected {expected_title!r} in titles, got {parsed.titles}"
        )


# ─── Location aliases ─────────────────────────────────────────────────────────

class TestLocationAliases:
    """All supported city aliases must resolve to canonical form."""

    @pytest.mark.parametrize("query_fragment,expected_loc", [
        ("in bay area",       "San Francisco Bay Area"),
        ("in sf bay area",    "San Francisco Bay Area"),
        ("in san francisco",  "San Francisco Bay Area"),
        ("in sf",             "San Francisco Bay Area"),
        ("in nyc",            "New York City"),
        ("in new york",       "New York City"),
        ("in seattle",        "Seattle"),
        ("in austin",         "Austin, Texas"),
        ("in la",             "Los Angeles"),
        ("in los angeles",    "Los Angeles"),
        ("in boston",         "Boston"),
        ("in chicago",        "Chicago"),
        ("in denver",         "Denver"),
        ("in atlanta",        "Atlanta"),
        ("in san diego",      "San Diego"),
        ("in miami",          "Miami"),
        ("in phoenix",        "Phoenix"),
        ("in portland",       "Portland"),
        ("in washington dc",  "Washington DC"),
        ("in dc",             "Washington DC"),
    ])
    def test_alias_resolves(self, query_fragment, expected_loc):
        query = f"software engineer {query_fragment}"
        parsed = _fallback_parse(query)
        assert expected_loc in parsed.locations, (
            f"Query {query!r}: expected {expected_loc!r}, got {parsed.locations}"
        )


# ─── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_raw_query_preserved(self):
        raw = "Staff ML Engineer remote Bay Area"
        parsed = _fallback_parse(raw)
        assert parsed.raw_query == raw

    def test_no_crash_on_empty(self):
        parsed = _fallback_parse("")
        assert isinstance(parsed.titles, list)
        assert isinstance(parsed.locations, list)

    def test_no_crash_on_gibberish(self):
        parsed = _fallback_parse("xyzzy foo bar 12345")
        assert isinstance(parsed.titles, list)

    def test_remote_only_no_city(self):
        parsed = _fallback_parse("software engineer remote only")
        assert parsed.remote_ok is True
        assert "Remote" in parsed.locations

    def test_city_and_remote_both_captured(self):
        parsed = _fallback_parse("senior engineer bay area or remote")
        assert "San Francisco Bay Area" in parsed.locations
        assert "Remote" in parsed.locations
        assert parsed.remote_ok is True

    def test_senior_director_beats_director(self):
        """'Senior Director of Engineering' must not be downgraded to 'Director of Engineering'."""
        parsed = _fallback_parse("senior director of engineering remote")
        assert "Senior Director of Engineering" in parsed.titles
        assert "Director of Engineering" not in parsed.titles

    def test_senior_manager_beats_manager(self):
        parsed = _fallback_parse("senior engineering manager bay area")
        assert "Senior Engineering Manager" in parsed.titles
        assert "Engineering Manager" not in parsed.titles
