"""Natural language query → ParsedQuery via LLM."""
from __future__ import annotations

import json
import logging

from ..llm import complete, strip_fences
from ..models import ParsedQuery
from .prompts import PARSE_QUERY_SYSTEM, PARSE_QUERY_TEMPLATE

logger = logging.getLogger("jobsgrep.nlp")

# In-memory cache: (normalized_query, resume_prefix) → ParsedQuery
# Avoids re-parsing the same query string — 1 LLM call saved per repeated search.
_parse_cache: dict[tuple[str, str], ParsedQuery] = {}


async def parse_query(query: str, resume_text: str | None = None) -> ParsedQuery:
    """Parse a natural language job search query into structured ParsedQuery."""
    from ..config import get_settings
    settings = get_settings()

    # Cache lookup — same query + resume prefix → skip LLM entirely
    cache_key = (query.strip().lower(), (resume_text or "")[:200])
    if cache_key in _parse_cache:
        logger.debug("parse cache hit: %s", query[:60])
        return _parse_cache[cache_key]

    # Run fallback parser first — if it finds a confident non-generic title
    # (e.g. Engineering Manager, Director, VP, Staff SWE), trust it over the LLM.
    # Weaker LLMs (Groq/Cerebras) frequently misclassify management queries as SWE.
    _GENERIC_TITLES = {"Software Engineer", "Engineer"}
    fallback = _fallback_parse(query)
    if fallback.titles and not all(t in _GENERIC_TITLES for t in fallback.titles):
        logger.info("fallback parser matched specific title %s — skipping LLM parse", fallback.titles)
        _parse_cache[cache_key] = fallback
        return fallback

    if not settings.groq_api_key and not settings.gemini_api_key and not settings.cerebras_api_key:
        logger.warning("no LLM API key set — using basic fallback parser")
        result = _fallback_parse(query)
        _parse_cache[cache_key] = result
        return result

    prompt = PARSE_QUERY_TEMPLATE.format(query=query)
    if resume_text:
        prompt += f"\n\nAdditional context from user's resume:\n{resume_text[:1000]}"

    raw = await complete(prompt=prompt, system=PARSE_QUERY_SYSTEM, temperature=0.1, max_tokens=600)
    if not raw:
        result = _fallback_parse(query)
        _parse_cache[cache_key] = result
        return result

    try:
        data = json.loads(strip_fences(raw))
        parsed = ParsedQuery(**data, raw_query=query)
        logger.info(
            "parsed query: titles=%s locations=%s remote=%s skills=%d",
            parsed.titles[:2], parsed.locations[:2], parsed.remote_ok, len(parsed.skills_required),
        )
        _parse_cache[cache_key] = parsed
        return parsed
    except Exception as e:
        logger.warning("query parse failed (%s), using fallback", e)
        result = _fallback_parse(query)
        _parse_cache[cache_key] = result
        return result


import re

# ─── Out-of-scope guardrail ───────────────────────────────────────────────────

# Role words that are unambiguously non-tech. Only checked when NO tech signal
# exists in the query, so "security engineer" or "technical writer" pass fine.
_NON_TECH_ROLE_WORDS = frozenset({
    # Outdoor / trades
    "gardener", "landscaper", "plumber", "electrician", "carpenter",
    "welder", "roofer", "hvac", "pipefitter", "mason", "painter", "tiler",
    # Healthcare
    "nurse", "nursing", "physician", "surgeon", "dentist", "pharmacist",
    "paramedic", "radiologist", "optometrist", "midwife",
    # Food service
    "chef", "cook", "waiter", "waitress", "bartender", "barista",
    # Agriculture
    "farmer", "rancher", "horticulturist",
    # Legal (non-tech)
    "lawyer", "attorney", "paralegal",
    # Transportation
    "trucker", "driver", "pilot", "sailor",
    # Service / facility
    "cashier", "janitor", "custodian", "housekeeper", "cleaner",
    # Entertainment / arts
    "actor", "actress", "musician", "singer", "dancer",
    # Automotive
    "mechanic",
    # Emergency services
    "firefighter",
})

# Any of these words signals a tech query — overrides non-tech detection.
_TECH_SIGNAL_WORDS = frozenset({
    "software", "engineer", "developer", "programmer", "coder",
    "data", "ml", "ai", "backend", "frontend", "fullstack",
    "devops", "sre", "platform", "infrastructure", "cloud",
    "security",  # "security engineer" is tech
    "tech", "technical", "technology",
    "product", "engineering", "architect", "analyst", "pm", "tpm", "apm",
    "director", "manager", "vp", "cto", "ciso", "cpo",
    "python", "java", "javascript", "typescript", "golang",
    "kubernetes", "docker", "aws", "gcp", "azure",
    "mobile", "ios", "android", "api", "database", "sql",
    "startup", "saas", "fintech",
})


def is_out_of_scope(query: str) -> bool:
    """Return True if the query is clearly not a tech job search.

    Conservative: only fires when a non-tech role word is present AND no tech
    signal word overrides it. Ambiguous queries always pass through.
    """
    words = frozenset(re.findall(r"[a-z]+", query.lower()))
    if words & _TECH_SIGNAL_WORDS:
        return False
    return bool(words & _NON_TECH_ROLE_WORDS)


OUT_OF_SCOPE_MESSAGE = (
    "JobsGrep only covers tech roles in the USA right now. "
    "Try searches like 'Software Engineer Bay Area', "
    "'Engineering Manager remote', or 'Director of Engineering NYC'."
)


_LOCATION_ALIASES = {
    "bay area": "San Francisco Bay Area",
    "sf bay area": "San Francisco Bay Area",
    "san francisco": "San Francisco Bay Area",
    "sf": "San Francisco Bay Area",
    "nyc": "New York City",
    "new york": "New York City",
    "seattle": "Seattle",
    "austin": "Austin, Texas",
    "la": "Los Angeles",
    "los angeles": "Los Angeles",
    "boston": "Boston",
    "chicago": "Chicago",
    "denver": "Denver",
    "atlanta": "Atlanta",
    "san diego": "San Diego",
    "miami": "Miami",
    "phoenix": "Phoenix",
    "portland": "Portland",
    "washington dc": "Washington DC",
    "dc": "Washington DC",
}

_TITLE_CANONICAL = [
    # Order matters: more specific first
    (r"senior\s+director\s+of\s+engineering",           "Senior Director of Engineering"),
    (r"director\s+of\s+engineering|engineering\s+director|dir\s+of\s+eng", "Director of Engineering"),
    (r"software\s+director|director\s+of\s+software(?:\s+engineering)?|tech(?:nology)?\s+director|technical\s+director", "Director of Engineering"),
    (r"vp\s+of\s+engineering|vp\s+eng",                 "VP of Engineering"),
    (r"vp\s+of\s+product|vp\s+product",                 "VP of Product"),
    (r"director\s+of\s+product|product\s+director",     "Director of Product"),
    (r"senior\s+engineering\s+manager",                 "Senior Engineering Manager"),
    (r"engineering\s+manager|dev\s+manager|software\s+development\s+manager|sdm\b|software\s+manager|tech\s+lead\s+manager|\bem\b", "Engineering Manager"),
    (r"technical\s+program\s+manager|tpm\b",            "Technical Program Manager"),
    (r"program\s+manager",                              "Program Manager"),
    (r"principal\s+product\s+manager|principal\s+pm\b", "Principal Product Manager"),
    (r"group\s+product\s+manager|gpm\b",                "Group Product Manager"),
    (r"senior\s+product\s+manager|sr\.?\s+product\s+manager|senior\s+pm\b|sr\.?\s+pm\b", "Senior Product Manager"),
    (r"product\s+manager|\bpm\b",                       "Product Manager"),
    (r"staff\s+software\s+engineer|staff\s+swe|staff\s+engineer",          "Staff Software Engineer"),
    (r"senior\s+software\s+engineer|senior\s+swe|sr\.?\s+software\s+engineer", "Senior Software Engineer"),
    (r"principal\s+software\s+engineer|principal\s+engineer",              "Principal Software Engineer"),
    (r"machine\s+learning\s+engineer|ml\s+engineer|mle\b",                 "Machine Learning Engineer"),
    (r"backend\s+engineer|back[\s-]?end\s+engineer",                       "Backend Engineer"),
    (r"frontend\s+engineer|front[\s-]?end\s+engineer",                     "Frontend Engineer"),
    (r"data\s+engineer",                                                    "Data Engineer"),
    (r"data\s+scientist",                                                   "Data Scientist"),
    (r"devops\s+engineer|sre\b|platform\s+engineer|infra\s+engineer",      "DevOps Engineer"),
    (r"software\s+engineer|swe\b|sde\b",                                   "Software Engineer"),
]


def _fallback_parse(query: str) -> ParsedQuery:
    """Regex-based fallback parser when LLM is unavailable.

    Handles common aliases: SWE, EM, SDM, staff/senior/director titles,
    and location shorthands like 'bay area', 'NYC'.
    """
    query_lower = query.lower()
    remote_ok = bool(re.search(r"\bremote\b", query_lower))

    # Extract locations
    locations: list[str] = []
    if remote_ok:
        locations.append("Remote")
    for alias, canonical in _LOCATION_ALIASES.items():
        if alias in query_lower and canonical not in locations:
            locations.append(canonical)

    # Extract canonical title
    titles: list[str] = []
    for pattern, canonical in _TITLE_CANONICAL:
        if re.search(pattern, query_lower):
            titles.append(canonical)
            break  # first match wins

    # If no title matched, we leave it empty to indicate broad search
    return ParsedQuery(
        titles=titles,
        title_variations=[],
        locations=locations,
        remote_ok=remote_ok,
        skills_required=[],
        skills_preferred=[],
        min_level="",
        exclude_keywords=[],
        target_companies=[],
        raw_query=query,
    )
