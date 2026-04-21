"""All Pydantic models for JobsGrep."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


# ─── Mode & Source registry ──────────────────────────────────────────────────

class DeployMode(str, Enum):
    LOCAL = "LOCAL"
    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"


class DataSourceType(str, Enum):
    PUBLIC_API = "public_api"       # Official public API, no auth (Greenhouse, Lever, Ashby)
    OFFICIAL_API = "official_api"   # Official API with API key (USAJobs, HN Algolia)
    COMMUNITY_API = "community_api" # Community-maintained (YC OSS API)
    SCRAPER = "scraper"             # Web scraping — LOCAL mode only by default


class RateLimit(BaseModel):
    calls_per_minute: int = 30
    calls_per_hour: int = 1000
    burst: int = 10


class DataSourceMeta(BaseModel):
    name: str
    source_type: DataSourceType
    enabled_modes: list[DeployMode]
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    tos_url: str = ""
    description: str = ""


# ─── NL Query parsing ────────────────────────────────────────────────────────

class ParsedQuery(BaseModel):
    titles: list[str] = Field(default_factory=list)
    title_variations: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = False
    skills_required: list[str] = Field(default_factory=list)
    skills_preferred: list[str] = Field(default_factory=list)
    min_level: str = ""
    exclude_keywords: list[str] = Field(default_factory=list)
    target_companies: list[str] = Field(default_factory=list)
    raw_query: str = ""


# ─── Job ─────────────────────────────────────────────────────────────────────

class RawJob(BaseModel):
    id: str                         # MD5 of company|title|location
    title: str
    company: str
    location: str = ""
    remote: bool = False
    url: str = ""
    description: str = ""
    salary_text: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    date_posted: str = ""
    source: str = ""                # which DataSource produced this
    source_type: DataSourceType = DataSourceType.PUBLIC_API
    raw: dict[str, Any] = Field(default_factory=dict)


class JobScore(BaseModel):
    fit_score: float = 0.0
    reasoning: str = ""
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    salary_range: str | None = None
    role_type: str = ""         # e.g. "AI Platform", "Agentic AI", "ML Engineer", "Solutions Architect"
    seniority_level: str = ""   # e.g. "Junior", "Mid", "Senior", "Staff", "Principal", "Director"


class ScoredJob(BaseModel):
    job: RawJob
    score: JobScore


# ─── Task tracking ───────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    SEARCHING = "searching"
    SEARCH_COMPLETE = "search_complete"
    SCORING = "scoring"
    REPORTING = "reporting"
    COMPLETE = "complete"
    FAILED = "failed"


class SearchTask(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.QUEUED
    query: str
    parsed_query: ParsedQuery | None = None
    skip_scoring: bool = False
    progress_message: str = ""
    total_jobs_found: int = 0
    total_jobs_scored: int = 0
    download_url: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    sources_searched: list[str] = Field(default_factory=list)
    jobs_per_source: dict[str, int] = Field(default_factory=dict)
    hot_skills: list[dict] = Field(default_factory=list)  # [{"skill": str, "count": int}, ...]


# ─── API request/response ─────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=5, max_length=1000,
                       description="Natural language job search query")
    resume_text: str | None = Field(None, description="Optional resume text for better scoring")
    skip_scoring: bool = False


class SearchResponse(BaseModel):
    task_id: str
    status: TaskStatus


class StatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    progress_message: str = ""
    total_jobs: int = 0
    scored_jobs: int = 0
    download_url: str | None = None
    error: str | None = None
    sources_searched: list[str] = Field(default_factory=list)
    jobs_per_source: dict[str, int] = Field(default_factory=dict)
    hot_skills: list[dict] = Field(default_factory=list)
    preview_jobs: list[dict] = Field(default_factory=list)


class SourceInfo(BaseModel):
    name: str
    source_type: DataSourceType
    enabled: bool
    description: str
    tos_url: str


# ─── Company ATS mapping ──────────────────────────────────────────────────────

class ATSMapping(BaseModel):
    company: str
    greenhouse_slug: str | None = None
    lever_slug: str | None = None
    ashby_slug: str | None = None
    recruitee_slug: str | None = None
    workable_slug: str | None = None
    website: str | None = None
    is_yc: bool = False
    team_size: str | None = None
    discovered_at: str = ""
