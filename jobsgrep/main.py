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
from pydantic import BaseModel
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

# Temp dir for generated Excel files
_REPORTS_DIR = Path.home() / ".jobsgrep" / "reports"


@asynccontextmanager
async def lifespan(app: FastAPI):
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    # Configure logging based on mode
    log_dir = settings.data_dir / "logs"
    setup_logging(
        mode=settings.jobsgrep_mode.value,
        log_dir=log_dir,
        log_level=settings.log_level,
    )

    logger.info("JobsGrep starting in %s mode on %s:%s",
                settings.jobsgrep_mode.value, settings.host, settings.port)

    # Start background prefetch in server modes
    prefetch_task = None
    if not settings.is_local and settings.prefetch_on_startup:
        from .prefetch import start_prefetch_loop
        queries = (
            [q.strip() for q in settings.prefetch_queries.split(",") if q.strip()]
            if settings.prefetch_queries else None
        )
        prefetch_task = asyncio.create_task(
            start_prefetch_loop(queries=queries,
                                interval_hours=settings.prefetch_interval_hours)
        )
        logger.info("prefetch worker started")

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
    )


async def _run_search(task_id: str, query: str, resume_text: str | None) -> None:
    """Background task: parse → search → score → report."""
    from .report.excel import generate_report

    async def update(status: TaskStatus, message: str) -> None:
        task = _tasks[task_id]
        task.status = status
        task.progress_message = message

    task = _tasks[task_id]
    settings = get_settings()

    try:
        # Phase 0: parse query
        await update(TaskStatus.PARSING, "Parsing your query...")
        parsed = await parse_query(query, resume_text)
        task.parsed_query = parsed

        # Phase 1: check cache first
        from .job_cache import cache_key as _cache_key, get as _cache_get, store as _cache_store
        _ck = _cache_key(parsed)
        cached_jobs = _cache_get(_ck)
        if cached_jobs is not None:
            logger.info("cache hit for task %s: %d jobs", task_id, len(cached_jobs))
            task.total_jobs_found = len(cached_jobs)
            task.sources_searched = ["cache"]
            task.jobs_per_source = {"cache": len(cached_jobs)}
            all_jobs = cached_jobs
            # Jump straight to scoring
            await update(TaskStatus.SCORING, f"Scoring {len(all_jobs)} cached jobs...")

            async def progress_cb_cached(msg: str) -> None:
                task.progress_message = msg

            scored = await score_jobs(all_jobs, parsed, progress_cb=progress_cb_cached)
            task.total_jobs_scored = len(scored)

            await update(TaskStatus.REPORTING, "Generating Excel report...")
            task.completed_at = datetime.now(timezone.utc)
            from .report.excel import generate_report
            report_path = generate_report(scored, task, _REPORTS_DIR)
            task.download_url = f"/api/download/{task_id}"

            from .history import record_search
            record_search(query, task.total_jobs_found, len(scored), task.sources_searched)

            task.status = TaskStatus.COMPLETE
            task.progress_message = f"Done! Found {len(scored)} matching jobs (from cache)."
            _tasks[task_id]._report_path = str(report_path)  # type: ignore[attr-defined]
            return

        # Phase 1: search all enabled sources in parallel
        await update(TaskStatus.SEARCHING, "Searching job sources...")
        enabled = get_enabled_sources()

        source_map = {
            "greenhouse": GreenhouseSource(),
            "lever": LeverSource(),
            "ashby": AshbySource(),
            "hn_hiring": HNHiringSource(),
            "yc_companies": YCCompaniesSource(),
            "usajobs": USAJobsSource(),
        }
        if settings.scraping_allowed:
            try:
                from .sources.jobspy_source import JobSpySource
                source_map["jobspy"] = JobSpySource()
            except Exception:
                pass
            try:
                from .sources.levels_fyi import LevelsFYISource
                source_map["levels_fyi"] = LevelsFYISource()
            except Exception:
                pass
            try:
                from .sources.teamblind import TeamBlindSource
                source_map["teamblind"] = TeamBlindSource()
            except Exception:
                pass

        async def run_source(name: str, source) -> tuple[str, list]:
            if name not in enabled:
                return name, []
            try:
                task.progress_message = f"Searching {name}..."
                jobs = await source.fetch_jobs(parsed)
                return name, jobs
            except Exception as e:
                logger.warning("source %s failed: %s", name, e)
                return name, []

        results = await asyncio.gather(*[run_source(n, s) for n, s in source_map.items()])

        # Collect and deduplicate
        all_jobs = []
        seen_ids: set[str] = set()
        for source_name, jobs in results:
            if jobs:
                task.jobs_per_source[source_name] = len(jobs)
                task.sources_searched.append(source_name)
                for j in jobs:
                    if j.id not in seen_ids:
                        seen_ids.add(j.id)
                        all_jobs.append(j)

        task.total_jobs_found = len(all_jobs)
        logger.info("total unique jobs found: %d", len(all_jobs))

        # Close HTTP clients
        for source in source_map.values():
            await source.close()

        # Store in cache for future requests
        if all_jobs:
            _cache_store(_ck, all_jobs, source="live_search", label=query)

        # Phase 2: score
        await update(TaskStatus.SCORING, f"Scoring {len(all_jobs)} jobs...")

        async def progress_cb(msg: str) -> None:
            task.progress_message = msg

        scored = await score_jobs(all_jobs, parsed, progress_cb=progress_cb)
        task.total_jobs_scored = len(scored)

        # Phase 3: generate report
        await update(TaskStatus.REPORTING, "Generating Excel report...")
        task.completed_at = datetime.now(timezone.utc)
        report_path = generate_report(scored, task, _REPORTS_DIR)
        task.download_url = f"/api/download/{task_id}"

        # Record in search history
        from .history import record_search
        record_search(query, task.total_jobs_found, len(scored), task.sources_searched)

        # Store path for download
        task.status = TaskStatus.COMPLETE
        task.progress_message = f"Done! Found {len(scored)} matching jobs."
        _tasks[task_id]._report_path = str(report_path)  # type: ignore[attr-defined]

    except Exception as e:
        logger.exception("search task %s failed", task_id)
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.progress_message = f"Search failed: {e}"


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    index_html = _FRONTEND_DIR / "index.html"
    if index_html.exists():
        return FileResponse(str(index_html))
    return JSONResponse({"status": "JobsGrep running", "docs": "/docs"})


@app.post("/api/search", response_model=SearchResponse)
async def start_search(body: SearchRequest, user: AuthDep, request: Request):
    """Start a job search. Returns task_id for polling."""
    settings = get_settings()

    # Rate limit check
    if not await check_user_rate_limit(user):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings.search_rate_limit} searches/hour",
        )

    task_id = str(uuid.uuid4())
    task = SearchTask(task_id=task_id, query=body.query)

    async with _task_lock:
        _tasks[task_id] = task

    # Run search in background
    asyncio.create_task(_run_search(task_id, body.query, body.resume_text))

    return SearchResponse(task_id=task_id, status=TaskStatus.QUEUED)


@app.get("/api/status/{task_id}", response_model=StatusResponse)
async def get_status(task_id: str, user: AuthDep):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_response(task)


@app.get("/api/stream/{task_id}")
async def stream_progress(task_id: str, request: Request):
    """SSE endpoint: streams progress events until task completes."""

    async def event_generator() -> AsyncIterator[dict]:
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
                                 "total_jobs_scored", "sources_searched", "jobs_per_source"}
                    ),
                }
            if task.status in (TaskStatus.COMPLETE, TaskStatus.FAILED):
                yield {"event": "done", "data": _task_response(task).model_dump_json()}
                break
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


@app.get("/api/download/{task_id}")
async def download_report(task_id: str, user: AuthDep):
    """Download the Excel report."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != TaskStatus.COMPLETE:
        raise HTTPException(status_code=409, detail=f"Task not complete (status: {task.status.value})")

    report_path = getattr(task, "_report_path", None)
    if not report_path or not Path(report_path).exists():
        raise HTTPException(status_code=404, detail="Report file not found")

    settings = get_settings()
    response = FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=Path(report_path).name,
    )

    # PUBLIC mode: delete after download
    if settings.is_public:
        try:
            Path(report_path).unlink()
        except OSError:
            pass

    return response


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


@app.get("/api/cache")
async def list_cache(user: AuthDep):
    """List all cache entries with metadata."""
    from .job_cache import list_entries
    return list_entries()


@app.delete("/api/cache")
async def clear_cache(user: AuthDep):
    """Evict all expired cache entries."""
    from .job_cache import evict_expired
    removed = evict_expired()
    return {"evicted": removed}
