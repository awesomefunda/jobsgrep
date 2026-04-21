"""Prompt templates for NL query parsing."""

PARSE_QUERY_SYSTEM = """\
You are a job search query parser. Extract structured job search intent from natural language.
Return ONLY valid JSON, no explanation, no markdown fences.
"""

PARSE_QUERY_TEMPLATE = """\
Parse this job search query into structured parameters.

Query: {query}

Return JSON matching this exact schema:
{{
  "titles": ["list of exact job titles to search for"],
  "title_variations": ["additional title variants and equivalents the LLM infers"],
  "locations": ["list of cities/regions — empty if not mentioned"],
  "remote_ok": true or false,
  "skills_required": ["skills explicitly mentioned or strongly implied"],
  "skills_preferred": ["nice-to-have skills inferred from context"],
  "min_level": "one of: junior, mid, senior, staff, principal, director, vp, or empty",
  "exclude_keywords": ["titles/roles the user clearly does NOT want, e.g. manager, director, intern"],
  "target_companies": ["specific companies if mentioned, otherwise empty"]
}}

Rules:
- "locations" must be specific cities or metro areas (e.g. "San Francisco Bay Area", "New York City", "Seattle"). If the query mentions only a country ("usa", "united states", "us", "america") or no location at all, return locations as []. Never put country names in locations.
- Normalize titles to canonical engineering forms before putting them in "titles":
  - "SWE", "software dev", "software developer" → "Software Engineer"
  - "dev manager", "software manager", "engineering lead", "software development manager", "SDM", "manager role", "manager position" → "Engineering Manager"
  - "Staff SWE", "staff engineer" → "Staff Software Engineer"
  - "Senior SWE", "Sr. Engineer", "senior engineer" → "Senior Software Engineer"
  - "MLE", "ML engineer", "machine learning engineer" → "Machine Learning Engineer"
  - "Dir of Eng", "engineering director", "director engineering" → "Director of Engineering"
  - "VP Eng", "VP of Eng" → "VP of Engineering"
  - "EM" (alone) → "Engineering Manager"
  - "PM", "product manager", "APM", "associate PM" → "Product Manager"
  - "Senior PM", "Sr. PM", "senior product manager" → "Senior Product Manager"
  - "Principal PM", "principal product manager" → "Principal Product Manager"
  - "GPM", "group product manager" → "Group Product Manager"
  - "TPM", "technical program manager" → "Technical Program Manager"
  - "program manager" (without "technical") → "Program Manager"
- IMPORTANT: For management titles (Engineering Manager, Director, VP), skills_required MUST be empty [] unless specific technical skills are explicitly stated in the query. Do NOT invent skills like "software management", "team management", "leadership" as skills_required.
- If the query says "Staff Engineer" also include "Staff Software Engineer", "Senior Staff Engineer", "Principal Engineer" in title_variations
- If the query is for an IC (individual contributor) role, add management titles to exclude_keywords
- If the query says "remote", set remote_ok=true and add "Remote" to locations
- Infer reasonable skills from the role if not specified (e.g. "distributed systems" → Python, Go, Java, Kafka, Kubernetes)
- Return empty lists for fields with no information, never null
"""
