"""Bearer token auth for PRIVATE mode; optional OAuth stub for PUBLIC mode."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import get_settings
from ..models import DeployMode

logger = logging.getLogger("jobsgrep.auth")

_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> str:
    """
    Returns an identity string for the caller:
    - LOCAL mode: returns client IP (no auth needed).
    - PRIVATE mode: validates bearer token, returns token prefix.
    - PUBLIC mode: returns client IP (rate limited by IP).
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"

    if settings.jobsgrep_mode == DeployMode.LOCAL:
        return client_ip

    if settings.jobsgrep_mode == DeployMode.PRIVATE:
        if not settings.jobsgrep_access_token:
            logger.warning("PRIVATE mode but JOBSGREP_ACCESS_TOKEN not set — allowing all requests")
            return client_ip
        if credentials is None or credentials.credentials != settings.jobsgrep_access_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return f"token:{credentials.credentials[:8]}"

    # PUBLIC mode — return IP for rate limiting
    return client_ip


# Convenience dependency alias
AuthDep = Annotated[str, Depends(require_auth)]
