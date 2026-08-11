"""Liveness and readiness endpoints.

Deliberately unauthenticated and outside the versioned prefix: load balancers
and container orchestrators need them before any application concern applies.

Named ``system`` rather than ``health`` to avoid colliding with the financial
health module (app/modules/health), which is a different thing entirely.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.database import check_database
from app.core.redis import check_redis

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str


class DependencyStatus(BaseModel):
    database: bool
    redis: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: DependencyStatus


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Is the process up? Checks nothing external, so a database outage does not
    cause the orchestrator to kill an otherwise-healthy container."""
    settings = get_settings()
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(response: Response) -> ReadinessResponse:
    """Can the process serve traffic? Checks Postgres and Redis concurrently.

    Returns 503 when degraded so a load balancer stops routing to it, while the
    body still reports which dependency is at fault.
    """
    database_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    ready = database_ok and redis_ok

    if not ready:
        response.status_code = 503

    return ReadinessResponse(
        status="ready" if ready else "degraded",
        dependencies=DependencyStatus(database=database_ok, redis=redis_ok),
    )
