"""Tests for cross-cutting API behaviour: error envelope, headers, liveness.

These run against the ASGI app in-process, so they need no database.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    ConflictError,
    ErrorCode,
    InsufficientDataError,
    NotFoundError,
    RateLimitedError,
)


class TestLiveness:
    async def test_health_reports_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_health_does_not_touch_dependencies(self, client):
        """Liveness must not check Postgres or Redis -- otherwise a database
        outage causes the orchestrator to kill healthy containers."""
        assert (await client.get("/health")).status_code == 200


class TestRequestId:
    async def test_response_carries_a_request_id(self, client):
        response = await client.get("/health")
        assert response.headers.get("X-Request-ID")

    async def test_client_supplied_id_is_echoed(self, client):
        """Lets a trace span the frontend and backend."""
        response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers["X-Request-ID"] == "trace-abc-123"

    async def test_response_time_is_reported(self, client):
        assert float((await client.get("/health")).headers["X-Response-Time-Ms"]) >= 0


class TestErrorEnvelope:
    """Every non-2xx response has the identical shape, so the client needs
    exactly one error path -- including for framework-raised errors, which are
    the ones it can least predict."""

    async def test_unknown_route_uses_the_standard_envelope(self, client):
        response = await client.get("/api/v1/does-not-exist")
        assert response.status_code == 404

        body = response.json()
        assert set(body) == {"error"}, f"leaked a non-envelope shape: {body}"
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["request_id"]
        assert body["error"]["docs_url"].endswith("/NOT_FOUND")

    async def test_wrong_method_uses_the_standard_envelope(self, client):
        response = await client.post("/health")
        assert response.status_code == 405
        assert set(response.json()) == {"error"}

    async def test_envelope_never_uses_fastapis_detail_shape(self, client):
        """Guards the specific regression: Starlette's default handler emits
        {"detail": ...}, which bypasses the contract entirely."""
        body = (await client.get("/api/v1/does-not-exist")).json()
        assert "detail" not in body

    async def test_security_headers_are_present(self, client):
        headers = (await client.get("/health")).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"


class TestDomainErrors:
    """Each error carries its own status and code, so handlers never map
    exceptions to status codes by hand -- that mapping drifts."""

    @pytest.mark.parametrize(
        ("error", "status", "code"),
        [
            (NotFoundError("Transaction"), 404, ErrorCode.NOT_FOUND),
            (ConflictError("Duplicate"), 409, ErrorCode.CONFLICT),
            (RateLimitedError(60), 429, ErrorCode.RATE_LIMITED),
            (InsufficientDataError("Not enough history"), 503, ErrorCode.INSUFFICIENT_DATA),
        ],
    )
    def test_status_and_code_pairing(self, error, status, code):
        assert error.status_code == status
        assert error.code == code

    def test_not_found_hides_whether_the_resource_exists(self):
        """A row owned by another user must be indistinguishable from a missing
        one; 403 would confirm the ID exists."""
        assert NotFoundError("Transaction").message == "Transaction not found"

    def test_rate_limit_includes_retry_after(self):
        assert RateLimitedError(90).headers["Retry-After"] == "90"

    def test_insufficient_data_carries_caveats(self):
        error = InsufficientDataError(
            "Need more history", caveats=["Only 10 days recorded; 14 required."]
        )
        assert error.details[0].issue.startswith("Only 10 days")

    def test_envelope_includes_a_docs_url(self):
        body = NotFoundError("Goal").to_response("req-1").error
        assert body.docs_url == "https://docs.frugal.app/errors/NOT_FOUND"
        assert body.request_id == "req-1"
