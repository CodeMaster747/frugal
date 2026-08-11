"""Readiness against live Postgres and Redis.

Marked `integration` because it needs the compose stack (or CI services).
Run the unit suite alone with: pytest -m "not integration"
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestReadiness:
    async def test_reports_ready_when_dependencies_are_up(self, client):
        response = await client.get("/health/ready")
        body = response.json()

        assert response.status_code == 200, f"not ready: {body}"
        assert body["status"] == "ready"
        assert body["dependencies"] == {"database": True, "redis": True}

    async def test_names_each_dependency_individually(self, client):
        """A single boolean would say the service is unhealthy without saying
        which dependency to look at."""
        deps = (await client.get("/health/ready")).json()["dependencies"]
        assert set(deps) == {"database", "redis"}


class TestDatabase:
    async def test_extensions_from_the_baseline_migration_are_installed(self):
        from sqlalchemy import text

        from app.core.database import get_async_engine

        async with get_async_engine().connect() as conn:
            result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('citext','pg_trgm')")
            )
            installed = {row[0] for row in result}

        assert installed == {"citext", "pg_trgm"}, (
            f"baseline migration 0001 has not been applied; found {installed}"
        )

    async def test_numeric_arithmetic_is_exact(self):
        """Confirms the database agrees with ADR-003 -- NUMERIC, not float."""
        from decimal import Decimal

        from sqlalchemy import text

        from app.core.database import get_async_engine

        async with get_async_engine().connect() as conn:
            value = (
                await conn.execute(text("SELECT (0.1::numeric + 0.2::numeric) AS total"))
            ).scalar_one()

        assert value == Decimal("0.3")
