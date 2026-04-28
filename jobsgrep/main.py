"""FastAPI application — search endpoint, SSE progress, download, health."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from sse_starlette.sse import EventSourceResponse

from .auth.middleware import AuthDep
from .config import get_enabled_sources, get_settings
from .legal.rate_limiter import check_user_rate_limit
from .logging_config import setup_logging
from .models import (
    DeployMode,
    SearchRequest,
    SearchResponse,
    SourceInfo,
    StatusResponse,
    TaskStatus,
    SearchTask,
)
from .nlp.parser import parse_query
from .scoring.engine import score_jobs
from .sources.ashby import AshbySource
from .sources.greenhouse import GreenhouseSource
from .sources.hn_hiring import HNHiringSource
from .sources.lever import LeverSource
from .sources.usajobs import USAJobsSource
from .sources.yc_companies import YCCompaniesSource

logger = logging.getLogger("jobsgrep")


# ─── Request logging middleware ───────────────────────────────────────────────

class _RequestLogMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status, and duration."""

    _access_log = logging.getLogger("jobsgrep.access")

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "-")
        self._access_log.info(
            "%s %s %d %.0fms ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            ip,
        )
        return response

# ─── In-memory task store ────────────────────────────────────────────────────
_tasks: dict[str, SearchTask] = {}
_task_lock = asyncio.Lock()

# Temp dir for generated Excel files — use /tmp on Vercel, home dir otherwise
import os as _os
_REPORTS_DIR = (
    Path("/tmp/jobsgrep/reports") if _os.environ.get("VERCEL")
    else Path.home() / ".jobsgrep" / "reports"
)


def _load_seed_cache() -> None:
    """Copy bundled seed data (data/seed/) into the active cache directories.

    On Vercel every cold start begins with an empty /tmp. This function ensures
    pre-scored job data is immediately available without any API calls.
    On non-Vercel deployments it also seeds an empty cache on first run.
    """
    import shutil
    # Prefer jobsgrep/seed_data/ (always bundled by @vercel/python as part of the package).
    # Fall back to data/seed/ for local development.
    pkg_seed = Path(__file__).parent / "seed_data"
    legacy_seed = Path(__file__).parent.parent / "data" / "seed"
    seed_dir = pkg_seed if pkg_seed.exists() else legacy_seed
    if not seed_dir.exists():
        return

    from .job_cache import _cache_dir, _scored_dir
    scored = _scored_dir()
    raw    = _cache_dir()

    seeded = 0
    now = time.time()
    for src in seed_dir.glob("scored__*.json"):
        dst = scored / src.name.replace("scored__", "")
        if not dst.exists():
            # Re-stamp stored_at and force source=seed so entries never expire via TTL
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                data["stored_at"] = now
                data["source"] = "seed"
                dst.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                shutil.copy(src, dst)
            seeded += 1

    for src in seed_dir.glob("raw__*.json"):
        dst = raw / src.name.replace("raw__", "")
        if not dst.exists():
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                data["stored_at"] = now
                dst.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                shutil.copy(src, dst)
            seeded += 1

    if seeded:
        logger.info("seeded %d cache file(s) from data/seed/", seeded)

    # Prime the in-memory label index AND master job list
    from .job_cache import prime_label_index
    indexed = prime_label_index()
    logger.info("label index and master list primed: %d entries indexed", indexed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    settings = get_settings()

    # Configure logging based on mode
    try:
        log_dir = settings.data_dir / "logs"
        setup_logging(
            mode=settings.jobsgrep_mode.value,
            log_dir=log_dir,
            log_level=settings.log_level,
        )
    except Exception:
        pass  # logging failures must not prevent startup

    logger.info("JobsGrep starting in %s mode on %s:%s",
                settings.jobsgrep_mode.value, settings.host, settings.port)

    # Load bundled seed data into cache (Vercel cold start or any empty cache)
    try:
        _load_seed_cache()
    except Exception as e:
        logger.warning("seed load failed: %s", e)

    # Start background prefetch (all modes except Vercel serverless).
    # skip_scoring=True: raw job fetch only, no LLM calls required.
    import os as _os
    prefetch_task = None
    if settings.prefetch_on_startup and not _os.environ.get("VERCEL"):
        from .prefetch import start_prefetch_loop
        queries = (
            [q.strip() for q in settings.prefetch_queries.split(",") if q.strip()]
            if settings.prefetch_queries else None
        )
        prefetch_task = asyncio.create_task(
            start_prefetch_loop(queries=queries,
                                interval_hours=settings.prefetch_interval_hours,
                                skip_scoring=True)
        )
        logger.info("prefetch worker started (skip_scoring=True)")

    yield

    if prefetch_task:
        prefetch_task.cancel()
        try:
            await prefetch_task
        except asyncio.CancelledError:
            pass

    # Cleanup temp files on shutdown
    for f in _REPORTS_DIR.glob("*.xlsx"):
        try:
            f.unlink()
        except OSError:
            pass


app = FastAPI(
    title="JobsGrep",
    description="Legal-first job search aggregator",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(_RequestLogMiddleware)

# Serve frontend static files if the directory exists
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIR)), name="static")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _task_response(task: SearchTask) -> StatusResponse:
    return StatusResponse(
        task_id=task.task_id,
        status=task.status,
        progress_message=task.progress_message,
        total_jobs=task.total_jobs_found,
        scored_jobs=task.total_jobs_scored,
        download_url=task.download_url,
        error=task.error,
        sources_searched=task.sources_searched,
        jobs_per_source=task.jobs_per_source,
        hot_skills=task.hot_skills,
        preview_jobs=getattr(task, "_preview_jobs", []),
    )


async def _run_search(task_id: str, query: str, resume_text: str | None, skip_scoring: bool = False) -> None:
    """Background task: parse → search → score → report."""
    import os as _os
    from urllib.parse import quote as _quote
    from .report.excel import generate_report

    def _download_url() -> str:
        """Build download URL; on Vercel include query so any instance can regenerate."""
        base = f"/api/download/{task_id}"
        if _os.environ.get("VERCEL") and query:
            return f"{base}?query={_quote(query)}"
        return base

    async def update(status: TaskStatus, message: str) -> None:
        task = _tasks[task_id]
        task.status = status
        task.progress_message = message

    task = _tasks[task_id]
    settings = get_settings()

    try:
        # Phase 0: guardrail — reject non-tech queries immediately
        from .nlp.parser import is_out_of_scope, OUT_OF_SCOPE_MESSAGE
        if is_out_of_scope(query):
            task.status = TaskStatus.COMPLETE
            task.progress_message = OUT_OF_SCOPE_MESSAGE
            task.total_jobs_found = 0
            return

        # Phase 1: parse query
        from .job_cache import (
            cache_key as _cache_key,
            get as _cache_get,
            store as _cache_store,
            get_scored as _get_scored,
            get_scored_fuzzy as _get_scored_fuzzy,
            store_scored as _store_scored,
            _compute_hot_skills_from_jobs,
            get_all_cached_jobs,
        )
        from .nlp.parser import _fallback_parse

        # ── Fast path: try regex parser first, check scored cache ────────────
        # Avoids LLM call (~1-3s) for queries that hit the seed cache.
        fast_parsed = _fallback_parse(query)
        cache_result = _get_scored_fuzzy(fast_parsed)

        if cache_result is None:
            # Cache miss with fallback — now run the full LLM parser for better accuracy
            await update(TaskStatus.PARSING, "Parsing your query...")
            parsed = await parse_query(query, resume_text)
            task.parsed_query = parsed
            _ck = _cache_key(parsed)
            cache_result = _get_scored_fuzzy(parsed)
        else:
            # Cache hit without LLM — use fast_parsed directly
            parsed = fast_parsed
            task.parsed_query = parsed
            _ck = _cache_key(parsed)

        # ── Phase 1: Filter from Master Cache (Global Search) ───────────────
        await update(TaskStatus.SEARCHING, "Searching in-memory index...")

        from .job_cache import search_index
        from .scoring.engine import create_unscored_results, filter_jobs

        # Search by parsed title words, not raw query text.
        # Raw query contains location/filler words ("in usa", "jobs near me")
        # that don't appear in job content and cause the AND to return far too
        # few results (e.g. "software engineer in usa" → AND on "usa" = 18 hits).
        index_query = " ".join(parsed.titles + parsed.title_variations) if parsed.titles else query
        filtered = search_index(index_query)
        master_count = len(get_all_cached_jobs())

        # Strip country-level location terms before filtering — job records store
        # cities ("San Francisco, CA"), never countries, so "United States" / "USA"
        # as a location would eliminate every result.
        _BROAD_GEOS = frozenset({"united states", "usa", "us", "america", "united states of america"})
        effective_locations = [l for l in parsed.locations if l.lower() not in _BROAD_GEOS]

        # Apply semantic title/location filtering so keyword overlap doesn't
        # return wrong-role jobs (e.g. "software director" should not yield
        # Software Engineer results just because both share "software").
        if filtered and (parsed.titles or effective_locations or parsed.exclude_keywords):
            from dataclasses import replace as _dc_replace
            import copy as _copy
            _parsed_copy = parsed.model_copy(update={"locations": effective_locations})
            filtered = filter_jobs(filtered, _parsed_copy)

        if filtered:
            logger.info("index hit for task %s: %d jobs found from %d total", 
                        task_id, len(filtered), master_count)
            task.total_jobs_found = len(filtered)
            task.sources_searched = ["master_cache"]
            task.jobs_per_source = {"master_cache": len(filtered)}

            # Store small preview for UI
            task._preview_jobs = [ # type: ignore
                {"company": j.company, "title": j.title, "url": j.url, "source": j.source}
                for j in filtered[:20]
            ]
            await update(TaskStatus.REPORTING, "Building report from filtered results...")
            task.completed_at = datetime.now(timezone.utc)
            unscored = create_unscored_results(filtered)
            report_path = generate_report(unscored, task, _REPORTS_DIR)
            task.download_url = _download_url()
            task._report_path = str(report_path) # type: ignore
            
            task.status = TaskStatus.COMPLETE
            task.progress_message = f"Done! Found {len(filtered)} matching jobs in cache."
            
            from .history import record_search
            record_search(query, task.total_jobs_found, task.total_jobs_found, task.sources_searched)
            return
        else:
            logger.info("master cache: no jobs matched filters for '%s' (out of %d total cached)",
                        query, master_count)
            task.total_jobs_found = 0
            task.status = TaskStatus.COMPLETE
            task.progress_message = "No matching jobs found in the local index. Try a broader search terms."
            from .history import record_search
            record_search(query, 0, 0, ["master_cache"])
            return

    except Exception as e:
        logger.exception("search task %s failed", task_id)
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.progress_message = f"Search failed: {e}"


# ─── SEO keyword pages ───────────────────────────────────────────────────────

_KEYWORD_PAGES: dict[str, str] = {
    "software-engineer": "Software Engineer",
    "senior-software-engineer": "Senior Software Engineer",
    "staff-software-engineer": "Staff Software Engineer",
    "backend-engineer": "Backend Engineer",
    "frontend-engineer": "Frontend Engineer",
    "full-stack-engineer": "Full Stack Engineer",
    "machine-learning-engineer": "Machine Learning Engineer",
    "data-engineer": "Data Engineer",
    "engineering-manager": "Engineering Manager",
    "software-development-manager": "Software Development Manager",
    "director-of-engineering": "Director of Engineering",
    "vp-of-engineering": "VP of Engineering",
    "product-manager": "Product Manager",
    "senior-product-manager": "Senior Product Manager",
    "technical-program-manager": "Technical Program Manager",
}


def _build_job_landing_page(slug: str, role: str, base_url: str) -> str:
    """Render a server-side HTML landing page for a job role keyword."""
    import html as _html
    from .job_cache import search_index

    jobs = search_index(role)[:60]

    # Build job list items
    items_html = ""
    for job in jobs:
        title = _html.escape(job.title)
        company = _html.escape(job.company)
        loc = _html.escape(job.location or "Remote")
        url = _html.escape(job.url or "#")
        remote_badge = (
            '<span class="remote-badge">Remote</span>' if job.remote else ""
        )
        items_html += f"""
        <li class="job-item">
          <a href="{url}" target="_blank" rel="noopener noreferrer">
            <span class="job-title">{title}</span>
            <span class="job-meta">{company} &mdash; {loc} {remote_badge}</span>
          </a>
        </li>"""

    if not items_html:
        items_html = (
            '<li class="no-jobs">No cached listings right now. '
            '<a href="/">Search live →</a></li>'
        )

    count = len(jobs)
    count_label = f"{count} active " if count else ""
    desc = (
        f"Browse {count_label}{role} openings aggregated from Greenhouse, Lever, "
        f"Ashby, YC companies, HN Hiring, and more. Updated daily. "
        f"Download a free Excel tracker."
    )

    # JSON-LD: ItemList of job postings
    import json as _json
    ld_items = []
    for i, job in enumerate(jobs[:20], 1):
        ld_items.append({
            "@type": "ListItem",
            "position": i,
            "name": _html.escape(job.title),
            "url": job.url or base_url,
        })
    ld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"{role} Jobs",
        "description": desc,
        "url": f"{base_url}/jobs/{slug}",
        "numberOfItems": count,
        "itemListElement": ld_items,
    }
    ld_json = _json.dumps(ld, ensure_ascii=False)

    other_roles = [
        (s, r) for s, r in _KEYWORD_PAGES.items() if s != slug
    ][:8]
    related_links = " &bull; ".join(
        f'<a href="/jobs/{s}">{r}</a>' for s, r in other_roles
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{role} Jobs — JobsGrep</title>
  <meta name="description" content="{desc}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{base_url}/jobs/{slug}">

  <meta property="og:type" content="website">
  <meta property="og:url" content="{base_url}/jobs/{slug}">
  <meta property="og:title" content="{role} Jobs — JobsGrep">
  <meta property="og:description" content="{desc}">
  <meta property="og:image" content="{base_url}/favicon.png">

  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{role} Jobs — JobsGrep">
  <meta name="twitter:description" content="{desc}">

  <link rel="icon" type="image/svg+xml" href="/favicon.ico">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/styles.css">

  <script type="application/ld+json">{ld_json}</script>

  <style>
    .landing-hero {{
      width: 100%;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 2rem;
    }}
    .landing-hero h1 {{
      font-size: 1.7rem;
      font-weight: 700;
      margin-bottom: 0.5rem;
    }}
    .landing-hero .subtitle {{
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.6;
      margin-bottom: 1.25rem;
    }}
    .search-cta {{
      display: inline-block;
      background: var(--accent);
      color: #fff;
      font-weight: 600;
      font-size: 0.9rem;
      padding: 0.55rem 1.25rem;
      border-radius: 6px;
      text-decoration: none;
      transition: background 0.15s;
    }}
    .search-cta:hover {{ background: var(--accent-hover); }}
    .jobs-section {{
      width: 100%;
    }}
    .jobs-section h2 {{
      font-size: 1.1rem;
      font-weight: 600;
      margin-bottom: 1rem;
      color: var(--text);
    }}
    .jobs-section .count-badge {{
      font-size: 0.78rem;
      color: var(--muted);
      font-weight: 400;
      margin-left: 0.5rem;
    }}
    .job-list {{
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }}
    .job-item a {{
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
      padding: 0.85rem 1rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      text-decoration: none;
      transition: border-color 0.15s;
    }}
    .job-item a:hover {{ border-color: var(--accent); }}
    .job-title {{
      font-weight: 500;
      font-size: 0.93rem;
      color: var(--text);
    }}
    .job-meta {{
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .remote-badge {{
      display: inline-block;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 1px 6px;
      border-radius: 4px;
      background: rgba(91,141,238,0.15);
      color: var(--accent);
      margin-left: 4px;
      vertical-align: middle;
    }}
    .no-jobs {{
      color: var(--muted);
      font-size: 0.9rem;
      padding: 1rem;
    }}
    .no-jobs a {{ color: var(--accent); }}
    .related-section {{
      width: 100%;
      font-size: 0.82rem;
      color: var(--muted);
      line-height: 2;
    }}
    .related-section a {{ color: var(--accent); text-decoration: none; }}
    .related-section a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>

<header>
  <a href="/" style="text-decoration:none;">
    <span class="logo">⚡ JobsGrep</span>
  </a>
</header>

<main>

  <div class="landing-hero">
    <h1>{role} Jobs</h1>
    <p class="subtitle">
      {count_label.strip() or "Active"} {role} openings aggregated live from Greenhouse, Lever,
      Ashby, YC companies, Hacker News Hiring, and more. Search and download a clean
      Excel tracker with every lead — instantly.
    </p>
    <a class="search-cta" href="/?q={slug}">Search {role} Jobs →</a>
  </div>

  <div class="jobs-section">
    <h2>
      Current Openings
      <span class="count-badge">{count} listed</span>
    </h2>
    <ul class="job-list">
      {items_html}
    </ul>
  </div>

  <div class="related-section">
    <strong style="color:var(--text);">Browse other roles:</strong><br>
    {related_links}
  </div>

</main>

<footer>
  Data sourced from public APIs: Greenhouse, Lever, Ashby, Recruitee, Workable, HN Hiring, YC, USAJobs.
  &nbsp;|&nbsp;<a href="/">Search</a>
  &nbsp;|&nbsp;<a href="/docs">API Docs</a>
  &nbsp;|&nbsp;<a href="/api/health">Health</a>
</footer>

</body>
</html>"""


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    index_html = _FRONTEND_DIR / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html), headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": "JobsGrep running", "docs": "/docs"})


@app.get("/jobs/{slug}", include_in_schema=False)
async def job_landing(slug: str):
    """Server-rendered SEO landing page for a job role keyword."""
    from fastapi.responses import HTMLResponse
    role = _KEYWORD_PAGES.get(slug)
    if not role:
        raise HTTPException(status_code=404, detail="Page not found")
    settings = get_settings()
    html = _build_job_landing_page(slug, role, settings.site_url)
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/search", response_model=SearchResponse)
async def start_search(body: SearchRequest, user: AuthDep, request: Request):
    """Start a job search. Returns task_id for polling/streaming."""
    settings = get_settings()

    # Rate limit check
    if not await check_user_rate_limit(user):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings.search_rate_limit} searches/hour",
        )

    task_id = str(uuid.uuid4())
    task = SearchTask(task_id=task_id, query=body.query, skip_scoring=body.skip_scoring)

    async with _task_lock:
        _tasks[task_id] = task

    import os as _os
    if not _os.environ.get("VERCEL"):
        # Local/PRIVATE: run in background, client polls or streams
        asyncio.create_task(_run_search(task_id, body.query, body.resume_text, body.skip_scoring))
    # On Vercel: search is driven by the SSE stream connection (avoids cross-instance state)

    return SearchResponse(task_id=task_id, status=TaskStatus.QUEUED)


@app.get("/api/status/{task_id}", response_model=StatusResponse)
async def get_status(task_id: str, user: AuthDep):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_response(task)


@app.get("/api/stream/{task_id}")
async def stream_progress(task_id: str, request: Request,
                          query: str = "", resume_text: str = ""):
    """SSE endpoint: streams progress events until task completes.

    On Vercel, POST /api/search doesn't run a background task (cross-instance
    state is unreliable). Instead this SSE connection drives the search directly,
    keeping everything in one persistent function invocation.
    """
    import os as _os

    async def event_generator() -> AsyncIterator[dict]:
        task = _tasks.get(task_id)

        # Cross-instance case on Vercel: task not found here, but query was passed
        if not task and query:
            task = SearchTask(task_id=task_id, query=query)
            async with _task_lock:
                _tasks[task_id] = task

        if not task:
            yield {"event": "error", "data": "task not found"}
            return

        # On Vercel: kick off the search inside this SSE connection
        if _os.environ.get("VERCEL") and task.status == TaskStatus.QUEUED:
            asyncio.create_task(_run_search(task_id, task.query,
                                            resume_text or None))

        last_message = ""
        for _ in range(600):  # max 10 min
            if await request.is_disconnected():
                break
            task = _tasks.get(task_id)
            if not task:
                yield {"event": "error", "data": "task not found"}
                break
            if task.progress_message != last_message:
                last_message = task.progress_message
                yield {
                    "event": "progress",
                    "data": task.model_dump_json(
                        include={"status", "progress_message", "total_jobs_found",
                                 "sources_searched", "jobs_per_source"}
                    ),
                }
            if task.status in (TaskStatus.COMPLETE, TaskStatus.FAILED):
                yield {"event": "done", "data": _task_response(task).model_dump_json()}
                break
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


@app.get("/api/download/{task_id}")
async def download_report(task_id: str, user: AuthDep, query: str = ""):
    """Download the Excel report.

    On Vercel, /tmp is per-instance so a different instance won't find the file.
    The download URL includes ?query=... so this endpoint can regenerate the
    report from the scored cache (seeds are loaded at every cold start).
    """
    report_path: str | None = None

    # 1. Try in-memory task (local dev / same Vercel instance)
    task = _tasks.get(task_id)
    if task:
        if task.status != TaskStatus.COMPLETE:
            raise HTTPException(status_code=409, detail=f"Task not complete (status: {task.status.value})")
        report_path = getattr(task, "_report_path", None)

    # 2. Try filesystem scan (same instance, different task object)
    if not report_path or not Path(report_path).exists():
        matches = list(_REPORTS_DIR.glob(f"*_{task_id}.xlsx"))
        if matches:
            report_path = str(sorted(matches)[-1])

    # 3. Vercel cross-instance fallback: regenerate from scored cache using query
    if (not report_path or not Path(report_path).exists()) and query:
        try:
            from .nlp.parser import parse_query
            from .job_cache import get_scored_fuzzy
            from .report.excel import generate_report
            parsed = await parse_query(query, None)
            cache_result = get_scored_fuzzy(parsed)
            if cache_result:
                scored, _ = cache_result
                fake_task = SearchTask(task_id=task_id, query=query)
                fake_task.status = TaskStatus.COMPLETE
                fake_task.total_jobs_found = len(scored)
                fake_task.total_jobs_scored = len(scored)
                fake_task.sources_searched = ["scored_cache"]
                _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                rp = generate_report(scored, fake_task, _REPORTS_DIR)
                report_path = str(rp)
        except Exception as e:
            logger.warning("report regeneration failed: %s", e)

    if not report_path or not Path(report_path).exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    return FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=Path(report_path).name,
    )


@app.get("/favicon.ico")
@app.get("/favicon.png")
async def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#2563eb"/>'
        '<text x="16" y="23" font-family="monospace" font-size="20" font-weight="bold" '
        'fill="white" text-anchor="middle">J</text>'
        '</svg>'
    )
    from fastapi.responses import Response
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    from fastapi.responses import PlainTextResponse
    settings = get_settings()
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        f"Sitemap: {settings.site_url}/sitemap.xml\n"
    )
    return PlainTextResponse(content, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    from fastapi.responses import Response
    from datetime import date
    settings = get_settings()
    base = settings.site_url
    today = date.today().isoformat()

    urls = [
        f"""  <url>
    <loc>{base}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>"""
    ]
    for slug in _KEYWORD_PAGES:
        urls.append(
            f"""  <url>
    <loc>{base}/jobs/{slug}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/sources")
async def list_sources(user: AuthDep):
    """List enabled data sources for current mode with legal classification."""
    enabled = get_enabled_sources()
    return [
        SourceInfo(
            name=meta.name,
            source_type=meta.source_type,
            enabled=True,
            description=meta.description,
            tos_url=meta.tos_url,
        )
        for meta in enabled.values()
    ]


@app.get("/api/trending-skills")
async def trending_skills():
    """Return trending skills for the landing page.

    Uses precomputed constant from seed_skills.py (always bundled),
    supplemented by any live scored cache entries with hot_skills.
    """
    from .seed_skills import TRENDING_SKILLS
    from collections import Counter
    from .job_cache import _scored_dir

    counts: Counter = Counter({item["skill"]: item["count"] for item in TRENDING_SKILLS})

    # Supplement with live scored cache (may have new searches cached)
    try:
        for path in _scored_dir().glob("*.json"):
            entry = json.loads(path.read_text(encoding="utf-8"))
            for item in entry.get("hot_skills", []):
                counts[item["skill"]] += item["count"]
    except Exception:
        pass

    return [{"skill": s, "count": c} for s, c in counts.most_common(20)]


@app.get("/api/history")
async def get_search_history(user: AuthDep):
    """Return past search queries with result counts."""
    from .history import get_history
    return get_history()


@app.delete("/api/history")
async def clear_search_history(user: AuthDep):
    from .history import clear_history
    clear_history()
    return {"status": "cleared"}


@app.get("/api/health")
async def health_check():
    """Check all source APIs are responding."""
    import httpx
    settings = get_settings()
    probes = {
        "greenhouse": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
        "lever": "https://api.lever.co/v0/postings/linear?mode=json",
        "ashby": None,  # POST only — skip GET health check
        "hn_algolia": "https://hn.algolia.com/api/v1/search_by_date?tags=ask_hn&query=who+is+hiring&hitsPerPage=1",
        "yc_oss": "https://yc-oss.github.io/api/companies/all.json",
    }

    results = {}
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=10) as client:
        for name, url in probes.items():
            if url is None:
                results[name] = "skipped"
                continue
            try:
                r = await client.get(url)
                results[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
            except Exception as e:
                results[name] = f"error: {e}"

    all_ok = all(v in ("ok", "skipped") for v in results.values())
    return {"status": "healthy" if all_ok else "degraded", "sources": results}


# ─── Cache & import endpoints ────────────────────────────────────────────────

class ImportRequest(BaseModel):
    key: str = Field(..., description="Cache key (from jobsgrep push)")
    label: str = Field("", description="Human-readable label (e.g. query string)")
    jobs: list[dict] = Field(..., description="List of RawJob dicts")
    token: str = Field("", description="Push authentication token")


@app.post("/api/import")
async def import_jobs(body: ImportRequest, request: Request):
    """Receive job data pushed from a local run and store in cache.

    The push token must match PUSH_TOKEN (or JOBSGREP_ACCESS_TOKEN) in settings.
    """
    from .job_cache import store_raw
    settings = get_settings()

    # Validate push token
    expected = settings.push_token or settings.jobsgrep_access_token
    if expected and body.token != expected:
        raise HTTPException(status_code=403, detail="Invalid push token")

    count = store_raw(body.key, body.jobs, source="pushed", label=body.label)
    logger.info("import: received %d/%d valid jobs for key=%s label=%s",
                count, len(body.jobs), body.key, body.label)
    return {"stored": count, "key": body.key, "label": body.label}


class PushScoredRequest(BaseModel):
    key: str = Field(..., description="Cache key")
    label: str = Field("", description="Human-readable query label")
    jobs: list[dict] = Field(..., description="List of {job: RawJob, score: JobScore} dicts")
    token: str = Field("", description="Push authentication token")


@app.post("/api/push-scored")
async def push_scored(body: PushScoredRequest, request: Request):
    """Receive pre-scored jobs from a local run and store in the scored cache.

    Jobs scored locally (with jobspy / LinkedIn / Indeed data) can be pushed
    here so Vercel serves them instantly without re-scoring.

    Auth: token must match PUSH_TOKEN env var (or JOBSGREP_ACCESS_TOKEN).
    """
    from .job_cache import store_scored as _store_scored, _deserialize_scored, prime_label_index
    settings = get_settings()

    expected = settings.push_token or settings.jobsgrep_access_token
    if expected and body.token != expected:
        raise HTTPException(status_code=403, detail="Invalid push token")

    if not body.jobs:
        raise HTTPException(status_code=400, detail="No jobs provided")

    # Restamp stored_at so TTL clock starts fresh on Vercel
    import time as _time
    stamped = []
    for item in body.jobs:
        stamped.append(item)

    jobs = _deserialize_scored(stamped)
    if not jobs:
        raise HTTPException(status_code=400, detail="No valid scored jobs could be parsed")

    _store_scored(body.key, jobs, source="pushed", label=body.label)
    prime_label_index()
    logger.info("push-scored: stored %d jobs key=%s label=%s", len(jobs), body.key, body.label)
    return {"stored": len(jobs), "key": body.key, "label": body.label}


@app.get("/api/cache")
async def list_cache(user: AuthDep):
    """List raw and scored cache entries with metadata."""
    from .job_cache import list_entries, _scored_dir
    import json, time as _time
    raw = list_entries()
    scored = []
    for path in _scored_dir().glob("*.json"):
        try:
            e = json.loads(path.read_text(encoding="utf-8"))
            scored.append({
                "key":       e.get("key", path.stem),
                "label":     e.get("label", ""),
                "source":    e.get("source", ""),
                "job_count": e.get("job_count", 0),
                "stored_at": e.get("stored_at", 0),
                "age_hours": round((_time.time() - e.get("stored_at", 0)) / 3600, 1),
            })
        except Exception:
            pass
    scored.sort(key=lambda x: x["stored_at"], reverse=True)
    return {"raw": raw, "scored": scored}


@app.delete("/api/cache")
async def clear_cache(user: AuthDep):
    """Evict all expired cache entries (raw + scored)."""
    from .job_cache import evict_expired
    removed = evict_expired()
    return {"evicted": removed}


@app.post("/api/prefetch")
async def trigger_prefetch(user: AuthDep):
    """Manually trigger a prefetch cycle (runs in background)."""
    from .config import get_settings
    settings = get_settings()
    from .prefetch import run_prefetch_cycle, _DEFAULT_QUERIES
    queries = (
        [q.strip() for q in settings.prefetch_queries.split(",") if q.strip()]
        if settings.prefetch_queries else _DEFAULT_QUERIES
    )
    asyncio.create_task(run_prefetch_cycle(queries, stagger_seconds=20.0))
    return {"status": "started", "queries": queries}
