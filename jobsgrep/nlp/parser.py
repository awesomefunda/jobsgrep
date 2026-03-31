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

    if not settings.groq_api_key and not settings.gemini_api_key:
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

def _fallback_parse(query: str) -> ParsedQuery:
    """Minimal regex-based fallback when LLM is unavailable."""
    query_lower = query.lower()
    remote_ok = "remote" in query_lower
    locations = ["Remote"] if remote_ok else []

    title_patterns = [
        r"(staff|senior|principal|lead|junior|mid-level)?\s*(software engineer|sde|swe|backend engineer|frontend engineer|fullstack engineer|data engineer|ml engineer|data scientist|product manager|pm|devops engineer|sre|platform engineer|infra engineer)",
        r"(vp|director|head)\s+of\s+\w+",
    ]
    titles = []
    for pat in title_patterns:
        m = re.search(pat, query_lower)
        if m:
            titles.append(m.group(0).strip().title())
    if not titles:
        titles = ["Software Engineer"]

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
