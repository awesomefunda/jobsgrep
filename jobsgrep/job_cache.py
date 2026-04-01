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
    """Stable cache key from canonical titles + location + remote.

    Uses only `titles` (not `title_variations`) so the same semantic query
    always produces the same key regardless of which title expansions the LLM
    happened to generate this run.
    """
    terms = sorted(t.lower().strip() for t in query.titles)
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
        logger.info("cached %d jobs -> %s (source=%s)", len(jobs), key, source)
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


def get_scored_fuzzy(query: "ParsedQuery") -> "list | None":
    """Fuzzy scored-cache lookup: try exact key first, then title-overlap scan.

    Handles two cases where exact key would miss:
      1. User writes "software manager remote" but seed is "Engineering Manager remote"
      2. Slight location phrasing differences ("Bay Area" vs "San Francisco Bay Area")

    Returns the best-matching cached result, or None if nothing close enough.
    """
    import re as _re

    exact = get_scored(cache_key(query))
    if exact is not None:
        return exact

    # Build word sets for matching
    def _words(s: str) -> set[str]:
        return set(_re.findall(r"[a-z]+", s.lower())) - {"the", "a", "an", "of", "and", "or", "for"}

    query_title_words = set()
    for t in query.titles:
        query_title_words |= _words(t)

    query_loc_words = set()
    for l in query.locations:
        query_loc_words |= _words(l)

    best_key: str | None = None
    best_score = 0

    scored_dir = _scored_dir()
    for path in scored_dir.glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            label = entry.get("label", "")
            if not label:
                continue

            # Remote compatibility: must agree
            stored_remote = "remote" in label.lower()
            if query.remote_ok != stored_remote:
                continue

            label_words = _words(label)
            title_overlap = len(query_title_words & label_words)
            loc_overlap = len(query_loc_words & label_words) if query_loc_words else 1

            # Penalize if the seed has words the query clearly lacks
            # (e.g. query is "manager", seed has "engineer" but not "manager" → bad match)
            seed_title_only = label_words - _words("remote san francisco bay area california new york")
            false_match_penalty = len(seed_title_only - query_title_words) * 0.5

            # At least one title word must overlap (e.g. "manager" matches both
            # "Engineering Manager" and "Software Manager")
            if title_overlap >= 1:
                score = title_overlap * 3 + loc_overlap - false_match_penalty
                if score > best_score:
                    best_score = score
                    best_key = path.stem
        except Exception:
            continue

    if best_key and best_score >= 2:
        result = get_scored(best_key)
        if result is not None:
            logger.info("fuzzy scored cache hit: %s (score=%d, query titles=%s)",
                        best_key, best_score, query.titles)
            return result

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
        logger.info("scored cache stored: %d jobs -> %s (source=%s)", len(jobs), key, source)
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
