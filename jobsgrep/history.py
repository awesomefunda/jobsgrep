"""Search history — persists past queries with timestamps and result counts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("jobsgrep.history")

MAX_HISTORY = 50  # keep the most recent N searches


def _history_path() -> Path:
    from .config import get_settings
    p = get_settings().data_dir / "search_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> list[dict]:
    path = _history_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(entries: list[dict]) -> None:
    _history_path().write_text(json.dumps(entries, indent=2), encoding="utf-8")


def record_search(query: str, jobs_found: int, jobs_scored: int, sources: list[str]) -> None:
    """Append a completed search to history. Trims to MAX_HISTORY."""
    entries = _load()
    entries.insert(0, {
        "query":        query,
        "jobs_found":   jobs_found,
        "jobs_scored":  jobs_scored,
        "sources":      sources,
        "ts":           datetime.now(timezone.utc).isoformat(),
    })
    _save(entries[:MAX_HISTORY])


def get_history() -> list[dict]:
    return _load()


def clear_history() -> None:
    _save([])
