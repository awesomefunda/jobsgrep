"""Mode detection, environment loading, and source registry."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import DataSourceMeta, DataSourceType, DeployMode, RateLimit


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core
    jobsgrep_mode: DeployMode = DeployMode.LOCAL
    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    cerebras_api_key: str = ""
    jobsgrep_contact_email: str = "user@example.com"

    # Auth
    jobsgrep_access_token: str = ""

    # Source control
    allow_scrape: bool = False
    min_fit_score: float = 0.7
    usajobs_api_key: str = ""

    # Rate limits
    search_rate_limit: int = 10      # searches/hour per user
    source_rate_limit: int = 60      # calls/minute per source

    # Infrastructure
    host: str = "0.0.0.0"
    port: int = 8080
    redis_url: str = ""
    cache_ttl: int = 6 * 3600         # raw job cache TTL (6h); 0 = disabled
    scored_cache_ttl: int = 24 * 3600 # scored results TTL (24h) — survives overnight
    public_file_ttl: int = 3600

    # Logging
    log_level: str = ""   # empty = auto (DEBUG in LOCAL, INFO otherwise)

    # Prefetch (server modes only)
    prefetch_queries: str = ""   # comma-separated; empty = use built-in defaults
    prefetch_interval_hours: float = 6.0
    prefetch_on_startup: bool = True

    # Public site URL (used for sitemap / canonical links). No trailing slash.
    site_url: str = "https://jobsgrep.com"

    # Push import auth (same token as jobsgrep_access_token, explicit for clarity)
    push_token: str = ""

    # Data paths
    # Auto-switch to /tmp on Vercel (filesystem is read-only except /tmp)
    data_dir: Path = (
        Path("/tmp/jobsgrep") if os.environ.get("VERCEL") else Path.home() / ".jobsgrep"
    )

    @field_validator("groq_api_key", "gemini_api_key", "jobsgrep_access_token", "usajobs_api_key", "push_token", mode="before")
    @classmethod
    def strip_api_keys(cls, v):
        """Strip whitespace from API keys and tokens to prevent 401 errors."""
        if isinstance(v, str):
            return v.strip()
        return v

    @property
    def is_local(self) -> bool:
        return self.jobsgrep_mode == DeployMode.LOCAL

    @property
    def is_public(self) -> bool:
        return self.jobsgrep_mode == DeployMode.PUBLIC

    @property
    def scraping_allowed(self) -> bool:
        # LOCAL and PRIVATE: scraping on by default (your own server/machine)
        # PUBLIC: only if explicitly forced via ALLOW_SCRAPE=true
        if self.jobsgrep_mode in (DeployMode.LOCAL, DeployMode.PRIVATE):
            return True
        return self.allow_scrape

    @property
    def user_agent(self) -> str:
        return f"JobsGrep/1.0 (personal job search tool; contact: {self.jobsgrep_contact_email})"

    @property
    def effective_cache_ttl(self) -> int:
        """PUBLIC mode never caches raw jobs."""
        if self.jobsgrep_mode == DeployMode.PUBLIC:
            return 0
        return self.cache_ttl

    @property
    def effective_scored_cache_ttl(self) -> int:
        """Scored cache TTL. PUBLIC mode allows reads (seed data) but /tmp is ephemeral."""
        return self.scored_cache_ttl


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ─── Source registry ──────────────────────────────────────────────────────────

_ALL_MODES = [DeployMode.LOCAL, DeployMode.PRIVATE, DeployMode.PUBLIC]
_NON_PUBLIC = [DeployMode.LOCAL, DeployMode.PRIVATE]
_LOCAL_ONLY = [DeployMode.LOCAL]


SOURCE_REGISTRY: dict[str, DataSourceMeta] = {
    "greenhouse": DataSourceMeta(
        name="greenhouse",
        source_type=DataSourceType.PUBLIC_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=200, calls_per_hour=2000),
        tos_url="https://boards-api.greenhouse.io",
        description="Greenhouse ATS public job board API (no auth required)",
    ),
    "lever": DataSourceMeta(
        name="lever",
        source_type=DataSourceType.PUBLIC_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=200, calls_per_hour=2000),
        tos_url="https://hire.lever.co/developer/postings",
        description="Lever ATS public postings API (no auth required)",
    ),
    "ashby": DataSourceMeta(
        name="ashby",
        source_type=DataSourceType.PUBLIC_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=200, calls_per_hour=2000),
        tos_url="https://developers.ashbyhq.com",
        description="Ashby ATS public job board API (no auth required)",
    ),
    "recruitee": DataSourceMeta(
        name="recruitee",
        source_type=DataSourceType.PUBLIC_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=20, calls_per_hour=300),
        tos_url="https://recruitee.com/api",
        description="Recruitee public job board API (no auth required)",
    ),
    "workable": DataSourceMeta(
        name="workable",
        source_type=DataSourceType.PUBLIC_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=20, calls_per_hour=300),
        tos_url="https://workable.com/api",
        description="Workable public widget API (no auth required)",
    ),
    "hn_hiring": DataSourceMeta(
        name="hn_hiring",
        source_type=DataSourceType.OFFICIAL_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=10, calls_per_hour=100),
        tos_url="https://hn.algolia.com/api",
        description="Hacker News Who's Hiring via Algolia + Firebase APIs",
    ),
    "yc_companies": DataSourceMeta(
        name="yc_companies",
        source_type=DataSourceType.COMMUNITY_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=5, calls_per_hour=20),
        tos_url="https://github.com/yc-oss/api",
        description="YC OSS company list (5,690 companies, updated daily)",
    ),
    "usajobs": DataSourceMeta(
        name="usajobs",
        source_type=DataSourceType.OFFICIAL_API,
        enabled_modes=_ALL_MODES,
        rate_limit=RateLimit(calls_per_minute=10, calls_per_hour=500),
        tos_url="https://developer.usajobs.gov/terms",
        description="USAJobs official government job board API",
    ),
    "jobspy": DataSourceMeta(
        name="jobspy",
        source_type=DataSourceType.SCRAPER,
        enabled_modes=_NON_PUBLIC,
        rate_limit=RateLimit(calls_per_minute=5, calls_per_hour=30),
        tos_url="",
        description="JobSpy web scraper (Indeed, LinkedIn, Glassdoor) — LOCAL + PRIVATE modes",
    ),
    "levels_fyi": DataSourceMeta(
        name="levels_fyi",
        source_type=DataSourceType.SCRAPER,
        enabled_modes=_NON_PUBLIC,
        rate_limit=RateLimit(calls_per_minute=10, calls_per_hour=60),
        tos_url="https://www.levels.fyi",
        description="Levels.fyi job listings via encrypted REST API — LOCAL + PRIVATE modes",
    ),
    "teamblind": DataSourceMeta(
        name="teamblind",
        source_type=DataSourceType.SCRAPER,
        enabled_modes=_NON_PUBLIC,
        rate_limit=RateLimit(calls_per_minute=6, calls_per_hour=30),
        tos_url="https://www.teamblind.com",
        description="TeamBlind job board via encrypted REST API — LOCAL + PRIVATE modes",
    ),
}


def get_enabled_sources(mode: DeployMode | None = None) -> dict[str, DataSourceMeta]:
    """Return sources enabled for the given mode (defaults to current settings)."""
    settings = get_settings()
    m = mode or settings.jobsgrep_mode
    enabled = {k: v for k, v in SOURCE_REGISTRY.items() if m in v.enabled_modes}
    # Scraper sources additionally require scraping_allowed
    if not settings.scraping_allowed:
        enabled = {k: v for k, v in enabled.items()
                   if v.source_type != DataSourceType.SCRAPER}
    return enabled
