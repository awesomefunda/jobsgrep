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
# Lightweight label index: key → {label, hot_skills} — avoids reading full files during fuzzy scan
_label_index: dict[str, dict] = {}


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
                if entry.get("source") == "seed":
                    continue  # seeds never expire
                if time.time() - entry.get("stored_at", 0) > ttl:
                    path.unlink()
                    removed += 1
            except Exception:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


# ─── Scored results cache ────────────────────────────────────────────────────

def get_scored(key: str) -> "tuple[list, list] | None":
    """Return (jobs, hot_skills) if cached and fresh, else None."""
    from .config import get_settings
    ttl = get_settings().effective_scored_cache_ttl
    if ttl == 0:
        return None

    if key in _scored_mem:
        entry = _scored_mem[key]
        is_seed = entry.get("source") == "seed"
        if is_seed or time.time() - entry["stored_at"] < ttl:
            logger.debug("scored cache hit (memory): %s (%d jobs)", key, entry["job_count"])
            return _deserialize_scored(entry["jobs"]), entry.get("hot_skills", [])
        del _scored_mem[key]

    path = _scored_dir() / f"{key}.json"
    if not path.exists():
        return None

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - entry.get("stored_at", 0)
        # Seeds are static committed data — never expire them via TTL
        is_seed = entry.get("source") == "seed"
        if not is_seed and age > ttl:
            path.unlink(missing_ok=True)
            logger.debug("scored cache expired: %s (%.1fh old, ttl=%.1fh)",
                         key, age / 3600, ttl / 3600)
            return None
        jobs = _deserialize_scored(entry["jobs"])
        _scored_mem[key] = entry
        logger.info("scored cache hit (disk): %s — %d jobs, %.0fh old, source=%s",
                    key, len(jobs), age / 3600, entry.get("source", "?"))
        return jobs, entry.get("hot_skills", [])
    except Exception as e:
        logger.warning("scored cache read error for %s: %s", key, e)
        return None


def get_scored_fuzzy(query: "ParsedQuery") -> "tuple[list, list] | None":
    """Fuzzy scored-cache lookup: try exact key first, then title-overlap scan.

    Returns (jobs, hot_skills) tuple, or None if nothing close enough.
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

    # Build candidates from in-memory label index first (no disk I/O).
    # Fall back to scanning disk when index is empty (first request on cold start
    # before _load_seed_cache has populated the index via store_scored).
    if _label_index:
        candidates = [(k, v["label"]) for k, v in _label_index.items()]
    else:
        candidates = []
        scored_dir = _scored_dir()
        for path in scored_dir.glob("*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                label = entry.get("label", "")
                if label:
                    _label_index[path.stem] = {
                        "label": label,
                        "hot_skills": entry.get("hot_skills", []),
                    }
                    candidates.append((path.stem, label))
            except Exception:
                continue

    for key, label in candidates:
        try:
            if not label:
                continue

            label_words = _words(label)
            stored_remote = "remote" in label.lower()

            # Remote + location guard:
            # - If query is remote-only → only match remote seeds
            # - If query has specific city → only match seeds for that city
            # - If query is "city OR remote" (remote_ok=True + city words) →
            #   match either remote seeds OR seeds for that city
            NON_LOC = _words("remote")
            query_city_words = query_loc_words - NON_LOC
            query_is_remote_only = query.remote_ok and not query_city_words
            query_is_city_or_remote = query.remote_ok and query_city_words
            query_is_city_only = not query.remote_ok and query_city_words

            if query_is_remote_only:
                if not stored_remote:
                    continue
                loc_overlap = 1
            elif query_is_city_only:
                # Must match the specific city; remote seeds don't count
                loc_overlap = len(query_city_words & label_words)
                if loc_overlap == 0:
                    continue
            elif query_is_city_or_remote:
                # Accept remote seeds OR seeds whose location overlaps the city
                loc_overlap = len(query_city_words & label_words)
                if not stored_remote and loc_overlap == 0:
                    continue  # different city and not remote → skip
                loc_overlap = max(loc_overlap, 1)
            else:
                loc_overlap = 1

            title_overlap = len(query_title_words & label_words)

            # Penalize if the seed has words the query clearly lacks
            seed_title_only = label_words - _words("remote san francisco bay area california new york austin texas")
            false_match_penalty = len(seed_title_only - query_title_words) * 0.5

            # At least one title word must overlap
            if title_overlap >= 1:
                score = title_overlap * 3 + loc_overlap - false_match_penalty
                if score > best_score:
                    best_score = score
                    best_key = key
        except Exception:
            continue

    if best_key and best_score >= 2:
        result = get_scored(best_key)
        if result is not None:
            logger.info("fuzzy scored cache hit: %s (score=%d, query titles=%s)",
                        best_key, best_score, query.titles)
            return result

    # Second pass: city query with no city-specific seed → fall back to best remote seed
    # Useful for rare titles (e.g. Engineering Manager) that only have remote seeds.
    if query_city_words and best_score < 2:
        remote_best_key: str | None = None
        remote_best_score = 0
        for key, label in candidates:
            try:
                if not label or "remote" not in label.lower():
                    continue
                label_words = _words(label)
                title_overlap = len(query_title_words & label_words)
                seed_title_only = label_words - _words("remote san francisco bay area california new york austin texas seattle chicago boston")
                false_match_penalty = len(seed_title_only - query_title_words) * 0.5
                if title_overlap >= 1:
                    score = title_overlap * 3 - false_match_penalty
                    if score > remote_best_score:
                        remote_best_score = score
                        remote_best_key = key
            except Exception:
                continue
        if remote_best_key and remote_best_score >= 2:
            result = get_scored(remote_best_key)
            if result is not None:
                # Sanity check: reject the seed if its actual jobs don't match the
                # query title intent (e.g. an EM seed full of SWE jobs).
                jobs_sample, _ = result
                sample = jobs_sample[:20]
                if sample:
                    matching = sum(
                        1 for j in sample
                        if any(tw in j.job.title.lower() for tw in query_title_words)
                    )
                    if matching / len(sample) < 0.3:
                        logger.info("remote seed %s rejected: title mismatch (%.0f%% match for %s)",
                                    remote_best_key, matching / len(sample) * 100, query.titles)
                        return None
                logger.info("fuzzy scored cache fallback to remote seed: %s (score=%d, query titles=%s)",
                            remote_best_key, remote_best_score, query.titles)
                return result

    return None


def _compute_hot_skills_from_jobs(jobs: "list", top_n: int = 15) -> "list[dict]":
    """Compute top skills from a list of ScoredJob objects."""
    from collections import Counter
    counts: Counter = Counter()
    for sj in jobs:
        for skill in sj.score.matching_skills + sj.score.missing_skills:
            if skill.strip():
                counts[skill.strip()] += 1
    return [{"skill": s, "count": c} for s, c in counts.most_common(top_n)]


def store_scored(key: str, jobs: "list", source: str = "prefetch", label: str = "") -> None:
    """Write ScoredJob list to scored cache (memory + disk), with precomputed hot_skills."""
    from .config import get_settings
    if get_settings().effective_scored_cache_ttl == 0:
        return

    hot_skills = _compute_hot_skills_from_jobs(jobs)
    entry: dict[str, Any] = {
        "key":        key,
        "label":      label,
        "source":     source,
        "stored_at":  time.time(),
        "job_count":  len(jobs),
        "hot_skills": hot_skills,
        "jobs":       [{"job": j.job.model_dump(), "score": j.score.model_dump()} for j in jobs],
    }
    _scored_mem[key] = entry
    _label_index[key] = {"label": label, "hot_skills": hot_skills}
    try:
        path = _scored_dir() / f"{key}.json"
        path.write_text(json.dumps(entry), encoding="utf-8")
        logger.info("scored cache stored: %d jobs -> %s (source=%s)", len(jobs), key, source)
    except OSError as e:
        logger.warning("scored cache write failed: %s", e)


def prime_label_index() -> int:
    """Read only label+hot_skills from scored cache files into _label_index.

    Called at startup after seeds are copied to /tmp so that the first
    get_scored_fuzzy() call uses memory instead of reading full JSON files.
    Returns number of entries indexed.
    """
    count = 0
    for path in _scored_dir().glob("*.json"):
        key = path.stem
        if key in _label_index:
            continue
        try:
            # Read only first 512 bytes to get label — avoids loading full file
            with path.open(encoding="utf-8") as f:
                head = f.read(512)
            import re as _re
            m = _re.search(r'"label"\s*:\s*"([^"]*)"', head)
            m2 = _re.search(r'"hot_skills"\s*:', head)
            if m:
                label = m.group(1)
                # hot_skills may not fit in 512 bytes — do a full read only if needed
                if m2:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                    _label_index[key] = {"label": label, "hot_skills": entry.get("hot_skills", [])}
                else:
                    _label_index[key] = {"label": label, "hot_skills": []}
                count += 1
        except Exception:
            continue
    return count


def _deserialize_scored(raw_list: list) -> list:
    from .models import RawJob, JobScore, ScoredJob
    out = []
    for item in raw_list:
        try:
            out.append(ScoredJob(job=RawJob(**item["job"]), score=JobScore(**item["score"])))
        except Exception:
            pass
    return out
