"""Title → role-family and seniority-level classification.

Pure, dependency-free string heuristics. Used by the segmented exporter to bucket
the job corpus into role-family sheets with a Level column, so users can grab the
slice they care about and feed it to their own LLM for ranking.

Two axes:
  - role family: the discipline (Software Engineering, Engineering Management, ...)
  - level:       the seniority rung (Intern → Junior → Mid → Senior → Staff →
                 Principal → Manager → Director → VP → Executive)

Matching uses word boundaries (so "cto" doesn't match inside "director"), and the
check order matters: management titles contain "engineer"/"product", so they must
be tested before the generic IC families.
"""
from __future__ import annotations

import re

ROLE_OTHER = "Other"

# Role families in priority order. First matching family wins.
_ROLE_FAMILIES: list[tuple[str, list[str]]] = [
    ("Engineering Management", [
        "engineering manager", "eng manager", "software development manager",
        "development manager", "director of engineering", "engineering director",
        "vp of engineering", "vp engineering", "head of engineering",
    ]),
    ("Product Management", [
        "product manager", "product management", "product owner",
        "head of product", "director of product", "vp of product",
        "group product manager", "chief product officer",
    ]),
    ("Program & Project Management", [
        "technical program manager", "program manager", "project manager",
        "tpm", "scrum master", "delivery manager", "release manager",
    ]),
    ("Data & ML", [
        "machine learning", "ml engineer", "mlops", "data scientist",
        "data science", "data engineer", "data analyst", "analytics engineer",
        "ai engineer", "applied scientist", "research scientist", "deep learning",
        "nlp", "computer vision",
    ]),
    ("Infrastructure & DevOps", [
        "devops", "site reliability", "sre", "platform engineer",
        "infrastructure", "cloud engineer", "systems engineer", "network engineer",
        "release engineer", "build engineer",
    ]),
    ("Security", [
        "security engineer", "security analyst", "infosec", "appsec",
        "application security", "cybersecurity", "soc analyst",
        "penetration tester", "pentester", "security architect",
    ]),
    ("Design", [
        "ux designer", "ui designer", "product designer", "ux/ui",
        "ui/ux", "design lead", "interaction designer", "visual designer",
        "ux researcher", "design manager",
    ]),
    ("QA & Test", [
        "qa engineer", "quality assurance", "test engineer", "sdet",
        "automation engineer", "quality engineer",
    ]),
    ("Software Engineering", [
        "software engineer", "software developer", "software engineering", "swe",
        "backend", "back end", "back-end", "frontend", "front end", "front-end",
        "full stack", "full-stack", "fullstack", "mobile engineer",
        "ios engineer", "ios developer", "android engineer", "android developer",
        "web developer", "developer", "programmer", "engineer", "engineering",
    ]),
]

# Level rungs in priority order. First matching rung wins.
_LEVELS: list[tuple[str, list[str]]] = [
    ("Intern", ["intern", "internship", "co-op"]),
    ("Executive", [
        "chief", "cto", "ceo", "cfo", "coo", "cpo", "ciso", "c-level", "founder",
    ]),
    ("VP", ["vp", "vice president", "svp", "evp", "head of"]),
    ("Director", ["director", "dir"]),
    ("Principal", ["principal", "distinguished", "fellow"]),
    ("Staff", ["staff", "architect"]),
    ("Senior", ["senior", "sr", "lead", "tech lead", "iii"]),
    ("Manager", ["manager", "mgr", "supervisor"]),
    ("Junior", [
        "junior", "jr", "associate", "entry level", "entry-level",
        "new grad", "new-grad", "graduate", "apprentice", "trainee",
    ]),
]

# Sort/display order for levels (least → most senior).
LEVEL_ORDER: list[str] = [
    "Intern", "Junior", "Mid", "Senior", "Staff", "Principal",
    "Manager", "Director", "VP", "Executive",
]

# Sort/display order for role families.
ROLE_ORDER: list[str] = [
    "Software Engineering", "Data & ML", "Infrastructure & DevOps", "Security",
    "QA & Test", "Design", "Engineering Management", "Product Management",
    "Program & Project Management", ROLE_OTHER,
]


def _compile(groups: list[tuple[str, list[str]]]) -> list[tuple[str, re.Pattern]]:
    """Pre-compile each group's keywords into one word-boundary alternation regex."""
    out = []
    for name, keywords in groups:
        alt = "|".join(re.escape(kw) for kw in keywords)
        out.append((name, re.compile(rf"\b(?:{alt})\b")))
    return out


_ROLE_PATTERNS = _compile(_ROLE_FAMILIES)
_LEVEL_PATTERNS = _compile(_LEVELS)


def classify_role_family(title: str) -> str:
    """Return the role family for a job title (e.g. 'Software Engineering')."""
    t = title.lower()
    for family, pat in _ROLE_PATTERNS:
        if pat.search(t):
            return family
    return ROLE_OTHER


def classify_level(title: str) -> str:
    """Return the seniority level for a job title (defaults to 'Mid')."""
    t = title.lower()
    for level, pat in _LEVEL_PATTERNS:
        if pat.search(t):
            return level
    return "Mid"


def classify(title: str) -> tuple[str, str]:
    """Return (role_family, level) for a job title."""
    return classify_role_family(title), classify_level(title)
