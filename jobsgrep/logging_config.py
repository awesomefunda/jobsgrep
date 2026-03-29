"""Logging configuration for JobsGrep.

LOCAL mode  → colored console output, DEBUG level by default
PRIVATE/PUBLIC → JSON lines to stdout + rotating file, INFO level by default

Call setup_logging() once at startup (cli.py serve, main.py lifespan).
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DeployMode

# ─── ANSI colors (local console) ─────────────────────────────────────────────

_RESET  = "\033[0m"
_GREY   = "\033[38;5;245m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_BOLD_RED = "\033[1;31m"
_GREEN  = "\033[32m"
_BLUE   = "\033[34m"

_LEVEL_COLORS = {
    "DEBUG":    _GREY,
    "INFO":     _GREEN,
    "WARNING":  _YELLOW,
    "ERROR":    _RED,
    "CRITICAL": _BOLD_RED,
}

_LOGGER_COLORS = {
    "jobsgrep.cache":    _CYAN,
    "jobsgrep.prefetch": _BLUE,
    "jobsgrep":          _GREEN,
    "uvicorn":          _GREY,
    "uvicorn.access":   _GREY,
    "httpx":            _GREY,
}


class _ColorFormatter(logging.Formatter):
    """Human-readable colored output for LOCAL mode."""

    def format(self, record: logging.LogRecord) -> str:
        level_color = _LEVEL_COLORS.get(record.levelname, "")
        logger_color = next(
            (v for k, v in _LOGGER_COLORS.items() if record.name.startswith(k)), _RESET
        )
        ts = self.formatTime(record, "%H:%M:%S")
        name = record.name.replace("jobsgrep.", "")[:20]
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return (
            f"{_GREY}{ts}{_RESET} "
            f"{level_color}{record.levelname[0]}{_RESET} "
            f"{logger_color}{name:<20}{_RESET} "
            f"{msg}"
        )


class _JsonFormatter(logging.Formatter):
    """Structured JSON lines for PRIVATE/PUBLIC modes (compatible with log aggregators)."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict = {
            "ts":      self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        # Attach any extra fields passed via logging.extra
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                if key not in ("msg", "args", "levelname", "levelno", "pathname",
                               "filename", "module", "exc_info", "exc_text",
                               "stack_info", "lineno", "funcName", "created",
                               "msecs", "relativeCreated", "thread", "threadName",
                               "processName", "process", "name", "message"):
                    data[key] = val
        return json.dumps(data, default=str)


# ─── Request access log ───────────────────────────────────────────────────────

class _UvicornAccessFilter(logging.Filter):
    """Suppress uvicorn's default access log in favour of our middleware."""
    def filter(self, record: logging.LogRecord) -> bool:
        return False


# ─── Public API ──────────────────────────────────────────────────────────────

def setup_logging(
    mode: str = "LOCAL",
    log_dir: Path | None = None,
    log_level: str = "",
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 5,
) -> None:
    """Configure root + jobsgrep loggers.

    Args:
        mode: DeployMode value ("LOCAL" / "PRIVATE" / "PUBLIC")
        log_dir: Directory for rotating log files. None = no file logging.
        log_level: Override level string (DEBUG/INFO/WARNING/ERROR).
                   Defaults to DEBUG in LOCAL, INFO otherwise.
        max_bytes: Max size per log file before rotation.
        backup_count: Number of rotated files to keep.
    """
    is_local = mode.upper() == "LOCAL"
    default_level = "DEBUG" if is_local else "INFO"
    level = getattr(logging, (log_level or default_level).upper(), logging.INFO)

    # Formatter choice
    console_fmt: logging.Formatter
    if is_local:
        console_fmt = _ColorFormatter()
    else:
        console_fmt = _JsonFormatter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_fmt)
    console_handler.setLevel(level)

    handlers: list[logging.Handler] = [console_handler]

    # Rotating file handler (always JSON regardless of mode, for parseability)
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "jobsgrep.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(_JsonFormatter())
        file_handler.setLevel(logging.DEBUG if is_local else logging.INFO)
        handlers.append(file_handler)

    # Root configuration
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)   # let handlers filter
    # Remove existing handlers to avoid duplicate output when called multiple times
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)

    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "asyncio", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    # Suppress uvicorn access log — our middleware handles it
    logging.getLogger("uvicorn.access").addFilter(_UvicornAccessFilter())

    logging.getLogger("jobsgrep").info(
        "logging configured: mode=%s level=%s file=%s",
        mode,
        logging.getLevelName(level),
        str(log_dir / "jobsgrep.log") if log_dir else "none",
    )
