"""Disk-backed job cache with TTL.

Two separate caches:
  raw/      — RawJob lists (from live search, prefetch, push)
  scored/   — ScoredJob lists (LLM-scored, highest value to preserve)

Cache key is derived from normalized query terms + location + remote flag.
Used by:
  - Search pipeline: check scored cache → raw cache → live search
  - Prefetch worker: fetch + score and populate both caches
  - Import endpoint: store jobs pushed from a local run
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from .models import ParsedQuery, RawJob

logger = logging.getLogger("jobsgrep.cache")

_DEFAULT_TTL = 6 * 3600   # 6 hours
_mem: dict[str, dict] = {}          # raw jobs in-memory overlay
_scored_mem: dict[str, dict] = {}   # scored jobs in-memory overlay


def _cache_dir() -> Path:
    from .config import get_settings
    p = get_settings().data_dir / "job_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _scored_dir() -> Path:
    from .config import get_settings
    p = get_settings().data_dir / "scored_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_key(query: ParsedQuery) -> str:
    """Stable cache key from the parts of a query that affect job results."""
    terms = sorted(t.lower().strip() for t in (query.titles + query.title_variations))
    locs   = sorted(l.lower().strip() for l in query.locations)
    raw    = f"{','.join(terms)}|{','.join(locs)}|remote={query.remote_ok}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def cache_key_from_terms(titles: list[str], locations: list[str], remote: bool) -> str:
    terms = sorted(t.lower().strip() for t in titles)
    locs  = sorted(l.lower().strip() for l in locations)
    raw   = f"{','.join(terms)}|{','.join(locs)}|remote={remote}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _entry_path(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


def get(key: str) -> list[RawJob] | None:
    """Return cached jobs if still fresh, else None."""
    from .config import get_settings
    ttl = get_settings().effective_cache_ttl
    if ttl == 0:
        return None  # caching disabled (PUBLIC mode)

    # Check in-memory first
    if key in _mem:
        entry = _mem[key]
        if time.time() - entry["stored_at"] < ttl:
            logger.debug("cache hit (memory): %s (%d jobs)", key, len(entry["jobs"]))
            return [RawJob(**j) for j in entry["jobs"]]
        del _mem[key]

    path = _entry_path(key)
    if not path.exists():
        return None

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        age   = time.time() - entry.get("stored_at", 0)
        if age > ttl:
            path.unlink(missing_ok=True)
            return None
        jobs = [RawJob(**j) for j in entry["jobs"]]
        _mem[key] = entry   # promote to memory
        logger.info("cache hit (disk): %s — %d jobs, %.0fm old, source=%s",
                    key, len(jobs), age / 60, entry.get("source", "?"))
        return jobs
    except Exception as e:
        logger.warning("cache read error for %s: %s", key, e)
        return None


def store(key: str, jobs: list[RawJob], source: str = "live_search", label: str = "") -> None:
    """Write jobs to cache (memory + disk)."""
    from .config import get_settings
    if get_settings().effective_cache_ttl == 0:
        return  # PUBLIC mode: never cache

    entry: dict[str, Any] = {
        "key":        key,
        "label":      label,
        "source":     source,
        "stored_at":  time.time(),
        "job_count":  len(jobs),
        "jobs":       [j.model_dump() for j in jobs],
    }
    _mem[key] = entry
    try:
        _entry_path(key).write_text(json.dumps(entry), encoding="utf-8")
        logger.info("cached %d jobs → %s (source=%s)", len(jobs), key, source)
    except OSError as e:
        logger.warning("cache write failed: %s", e)


def store_raw(key: str, jobs_raw: list[dict], source: str, label: str = "") -> int:
    """Import raw job dicts (e.g. from a push upload). Returns count stored."""
    jobs = []
    for raw in jobs_raw:
        try:
            jobs.append(RawJob(**raw))
        except Exception:
            pass
    if jobs:
        store(key, jobs, source=source, label=label)
    return len(jobs)


def list_entries() -> list[dict]:
    """List all cache entries with metadata (no job payloads)."""
    entries = []
    for path in _cache_dir().glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            entries.append({
                "key":       entry.get("key", path.stem),
                "label":     entry.get("label", ""),
                "source":    entry.get("source", ""),
                "job_count": entry.get("job_count", 0),
                "stored_at": entry.get("stored_at", 0),
                "age_hours": round((time.time() - entry.get("stored_at", 0)) / 3600, 1),
            })
        except Exception:
            pass
    return sorted(entries, key=lambda x: x["stored_at"], reverse=True)


def evict_expired() -> int:
    """Delete stale cache files from both raw and scored caches. Returns count removed."""
    from .config import get_settings
    settings = get_settings()
    removed = 0
    for directory, ttl in (
        (_cache_dir(),  settings.effective_cache_ttl),
        (_scored_dir(), settings.effective_scored_cache_ttl),
    ):
        for path in directory.glob("*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - entry.get("stored_at", 0) > ttl:
                    path.unlink()
                    removed += 1
            except Exception:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


# ─── Scored results cache ────────────────────────────────────────────────────

def get_scored(key: str) -> "list | None":
    """Return pre-scored ScoredJob list if cached and fresh, else None."""
    from .config import get_settings
    from .models import RawJob, JobScore, ScoredJob
    ttl = get_settings().effective_scored_cache_ttl
    if ttl == 0:
        return None

    if key in _scored_mem:
        entry = _scored_mem[key]
        if time.time() - entry["stored_at"] < ttl:
            logger.debug("scored cache hit (memory): %s (%d jobs)", key, entry["job_count"])
            return _deserialize_scored(entry["jobs"])
        del _scored_mem[key]

    path = _scored_dir() / f"{key}.json"
    if not path.exists():
        return None

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - entry.get("stored_at", 0)
        if age > ttl:
            path.unlink(missing_ok=True)
            logger.debug("scored cache expired: %s (%.1fh old, ttl=%.1fh)",
                         key, age / 3600, ttl / 3600)
            return None
        jobs = _deserialize_scored(entry["jobs"])
        _scored_mem[key] = entry
        logger.info("scored cache hit (disk): %s — %d jobs, %.0fh old, source=%s",
                    key, len(jobs), age / 3600, entry.get("source", "?"))
        return jobs
    except Exception as e:
        logger.warning("scored cache read error for %s: %s", key, e)
        return None


def store_scored(key: str, jobs: "list", source: str = "prefetch", label: str = "") -> None:
    """Write ScoredJob list to scored cache (memory + disk)."""
    from .config import get_settings
    if get_settings().effective_scored_cache_ttl == 0:
        return

    entry: dict[str, Any] = {
        "key":       key,
        "label":     label,
        "source":    source,
        "stored_at": time.time(),
        "job_count": len(jobs),
        "jobs":      [{"job": j.job.model_dump(), "score": j.score.model_dump()} for j in jobs],
    }
    _scored_mem[key] = entry
    try:
        path = _scored_dir() / f"{key}.json"
        path.write_text(json.dumps(entry), encoding="utf-8")
        logger.info("scored cache stored: %d jobs → %s (source=%s)", len(jobs), key, source)
    except OSError as e:
        logger.warning("scored cache write failed: %s", e)


def _deserialize_scored(raw_list: list) -> list:
    from .models import RawJob, JobScore, ScoredJob
    out = []
    for item in raw_list:
        try:
            out.append(ScoredJob(job=RawJob(**item["job"]), score=JobScore(**item["score"])))
        except Exception:
            pass
    return out
