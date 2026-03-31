"""Vercel entrypoint — exposes the FastAPI ASGI app."""
from jobsgrep.main import app

# Vercel's Python runtime detects `app` as the ASGI handler
__all__ = ["app"]
