"""Append-only audit log — every API call with timestamp, source, endpoint, response code."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("jobsgrep.audit")


def _audit_path() -> Path:
    from ..config import get_settings
    p = get_settings().data_dir / "audit.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_write_lock = asyncio.Lock()


async def log_api_call(
    source: str,
    endpoint: str,
    status_code: int,
    task_id: str = "",
    extra: dict | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "endpoint": endpoint,
        "status": status_code,
        "task_id": task_id,
        **(extra or {}),
    }
    line = json.dumps(entry) + "\n"
    async with _write_lock:
        try:
            with open(_audit_path(), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning("audit log write failed: %s", e)


def log_api_call_sync(source: str, endpoint: str, status_code: int, **extra) -> None:
    """Synchronous version for use outside async context."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "endpoint": endpoint,
        "status": status_code,
        **extra,
    }
    try:
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.warning("audit log write failed: %s", e)
