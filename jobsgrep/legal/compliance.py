"""Mode enforcement and source gating — compile-time safe checks."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from ..config import get_settings
from ..models import DataSourceMeta, DataSourceType, DeployMode


class SourceNotAllowedError(RuntimeError):
    """Raised when a source is used in a mode that doesn't permit it."""


def assert_source_allowed(source: DataSourceMeta) -> None:
    """Raise SourceNotAllowedError before any network call if the source is gated."""
    settings = get_settings()
    mode = settings.jobsgrep_mode

    if mode not in source.enabled_modes:
        raise SourceNotAllowedError(
            f"Source '{source.name}' (type={source.source_type.value}) is not allowed "
            f"in {mode.value} mode. Allowed modes: {[m.value for m in source.enabled_modes]}"
        )

    if source.source_type == DataSourceType.SCRAPER and not settings.scraping_allowed:
        raise SourceNotAllowedError(
            f"Source '{source.name}' is a scraper. "
            f"Set ALLOW_SCRAPE=true to enable in {mode.value} mode (not recommended). "
            f"In LOCAL mode scraping is always allowed."
        )


def mode_gate(source_name: str):
    """Decorator that enforces mode compliance before calling a source fetch function."""
    from ..config import SOURCE_REGISTRY

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            meta = SOURCE_REGISTRY.get(source_name)
            if meta is None:
                raise SourceNotAllowedError(f"Unknown source '{source_name}'")
            assert_source_allowed(meta)
            return await fn(*args, **kwargs)
        return wrapper
    return decorator


def require_mode(*modes: DeployMode):
    """Decorator that restricts an endpoint/function to specific modes."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            settings = get_settings()
            if settings.jobsgrep_mode not in modes:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=403,
                    detail=f"This feature is only available in {[m.value for m in modes]} mode(s). "
                           f"Current mode: {settings.jobsgrep_mode.value}",
                )
            return await fn(*args, **kwargs)
        return wrapper
    return decorator
