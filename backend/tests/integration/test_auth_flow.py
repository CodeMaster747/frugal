"""End-to-end authentication behaviour against a real database.

Covers the M1 exit criteria: the full session lifecycle, refresh rotation with
family revocation on replay, rate limiting, and account deletion.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from tests.conftest import VALID_PASSWORD, registration_payload

#: Tables carrying a `user_id` that has no foreign key to `users` with
#: ON DELETE CASCADE -- i.e. rows that would outlive the account that owns them.
_MISSING_USER_CASCADE = text("""
    SELECT c.relname
    FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'user_id'
    WHERE c.relkind = 'r'
      AND c.relnamespace = 'public'::regnamespace
      AND NOT EXISTS (
          SELECT 1 FROM pg_constraint con
          WHERE con.conrelid = c.oid
            AND con.contype = 'f'
            AND con.confrelid = 'users'::regclass
            AND con.confdeltype = 'c'
            AND a.attnum = ANY (con.conkey)
      )
""")

#: `audit_log` survives deletion by design; see `AuthService.delete_account`.
_CASCADE_EXEMPT = frozenset({"audit_log"})

#: Tables owned by a user, discovered rather than listed -- a hand-maintained
#: list would be missing exactly the table someone forgot to wire up.
_USER_OWNED_TABLES = text("""
    SELECT c.relname
    FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'user_id'
    WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace
      AND c.relname <> ALL (:exempt)
    ORDER BY c.relname
""")


async def _user_owned_row_counts(session) -> dict[str, int]:
    """Rows that belong to *a user*, per table.

    `user_id IS NOT NULL` is the whole point: the shared category taxonomy lives
    in `categories` with no owner and must survive an account deletion. Counting
    every row instead would demand that deleting an account wipes the reference
    data for everyone else.
    """
    tables = (
        (await session.execute(_USER_OWNED_TABLES, {"exempt": list(_CASCADE_EXEMPT)}))
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for table in tables:
        # Interpolated, but the values come from pg_class -- they are the names
        # of tables that exist, not anything a caller supplied.
        counts[table] = await session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE user_id IS NOT NULL")  # noqa: S608
        )
    return counts


pytestmark = pytest.mark.integration

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"
COOKIE = "frugal_refresh"


class TestRegistration:
    async def test_creates_account_and_signs_in(self, client):
        response = await client.post(REGISTER, json=registration_payload())

        assert response.status_code == 201
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["email"] == "priya@example.com"
        assert body["user"]["base_currency"] == "INR"
        assert body["expires_in"] == 900

    async def test_never_returns_the_refresh_token_in_the_body(self, client):
        """The refresh token exists only in an httpOnly cookie. Returning it in
        the body would put it within reach of any XSS payload."""
        response = await client.post(REGISTER, json=registration_payload())

        assert "refresh_token" not in response.json()
        assert COOKIE in response.cookies

    async def test_refresh_cookie_is_httponly_and_path_scoped(self, client):
        response = await client.post(REGISTER, json=registration_payload())
        header = next(v for k, v in response.headers.items() if k.lower() == "set-cookie")

        assert "HttpOnly" in header
        assert "SameSite=lax" in header.replace("samesite", "SameSite")
        # Scoped to /auth so the cookie never rides along on ordinary API
        # calls, keeping CSRF surface to the three auth endpoints.
        assert "Path=/api/v1/auth" in header

    async def test_duplicate_email_conflicts(self, client):
        await client.post(REGISTER, json=registration_payload())
        response = await client.post(REGISTER, json=registration_payload())

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    async def test_email_is_case_insensitive(self, client):
        """CITEXT enforces this in the database, so no code path can bypass it."""
        await client.post(REGISTER, json=registration_payload(email="Priya@Example.com"))
        response = await client.post(REGISTER, json=registration_payload(email="priya@example.com"))

        assert response.status_code == 409

    @pytest.mark.parametrize(
        "password",
        ["short1A", "alllowercase123", "ALLUPPERCASE123", "NoDigitsInHere"],
    )
    async def test_weak_passwords_are_rejected(self, client, password):
        response = await client.post(REGISTER, json=registration_payload(password=password))

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_password_is_never_stored_or_returned(self, client, db_session):
        response = await client.post(REGISTER, json=registration_payload())
        assert "password" not in response.text

        from app.modules.auth.models import User

        user = (await db_session.execute(select(User))).scalar_one()
        assert user.password_hash != VALID_PASSWORD
        assert user.password_hash.startswith("$argon2id$")


class TestLogin:
    async def test_succeeds_with_correct_credentials(self, client):
        await client.post(REGISTER, json=registration_payload())
        response = await client.post(
            LOGIN, json={"email": "priya@example.com", "password": VALID_PASSWORD}
        )

        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_wrong_password_is_rejected(self, client):
        await client.post(REGISTER, json=registration_payload())
        response = await client.post(
            LOGIN, json={"email": "priya@example.com", "password": "WrongPassword123"}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    async def test_unknown_and_wrong_password_are_indistinguishable(self, client):
        """Different messages here would turn login into an account-enumeration
        oracle."""
        await client.post(REGISTER, json=registration_payload())

        unknown = await client.post(
            LOGIN, json={"email": "nobody@example.com", "password": VALID_PASSWORD}
        )
        wrong = await client.post(
            LOGIN, json={"email": "priya@example.com", "password": "WrongPassword123"}
        )

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


class TestRefreshRotation:
    async def test_rotates_and_issues_a_new_token(self, client, registered):
        response = await client.post(REFRESH)

        assert response.status_code == 200
        assert response.json()["access_token"]
        # A *new* refresh token; the old one is now spent.
        assert client.cookies.get(COOKIE)

    async def test_rotation_invalidates_the_previous_token(self, client, registered):
        first = client.cookies.get(COOKIE)
        await client.post(REFRESH)
        second = client.cookies.get(COOKIE)

        assert first != second

    async def test_replaying_a_used_token_revokes_the_whole_family(
        self, client, registered, db_session
    ):
        """The M1 exit criterion.

        A used token presented again means it was captured and replayed. The
        legitimate holder and the attacker are indistinguishable at that point,
        so every token in the chain is revoked and both are signed out.
        """
        stolen = client.cookies.get(COOKIE)

        # Legitimate rotation; `stolen` is now spent.
        await client.post(REFRESH)
        assert (await client.post(REFRESH)).status_code == 200

        # The attacker replays the captured token.
        client.cookies.set(COOKIE, stolen, path="/api/v1/auth")
        replay = await client.post(REFRESH)

        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"

        # Every token in the family is revoked -- including the one the
        # legitimate user still holds.
        from app.modules.auth.models import RefreshToken

        active = await db_session.scalar(
            select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
        )
        assert active == 0

    async def test_the_legitimate_session_dies_with_the_family(self, client, registered):
        stolen = client.cookies.get(COOKIE)
        await client.post(REFRESH)
        current = client.cookies.get(COOKIE)

        client.cookies.set(COOKIE, stolen, path="/api/v1/auth")
        await client.post(REFRESH)

        # The user's own current token no longer works either. That is the
        # intended, safe outcome.
        client.cookies.set(COOKIE, current, path="/api/v1/auth")
        assert (await client.post(REFRESH)).status_code == 401

    async def test_reuse_is_recorded_in_the_audit_log(self, client, registered, db_session):
        stolen = client.cookies.get(COOKIE)
        await client.post(REFRESH)
        client.cookies.set(COOKIE, stolen, path="/api/v1/auth")
        await client.post(REFRESH)

        from app.core.audit import AuditLog

        actions = (
            (
                await db_session.execute(
                    select(AuditLog.action).where(AuditLog.action.like("token.%"))
                )
            )
            .scalars()
            .all()
        )
        assert "token.reuse_detected" in actions

    async def test_missing_cookie_is_rejected(self, client):
        assert (await client.post(REFRESH)).status_code == 401

    async def test_garbage_token_is_rejected(self, client):
        client.cookies.set(COOKIE, "not-a-real-token", path="/api/v1/auth")
        assert (await client.post(REFRESH)).status_code == 401


class TestLogout:
    async def test_revokes_the_session(self, client, registered):
        assert (await client.post(LOGOUT)).status_code == 204
        assert (await client.post(REFRESH)).status_code == 401

    async def test_is_idempotent(self, client, registered):
        """A logout that errored on an already-invalid token would strand the
        client holding a cookie it cannot clear."""
        await client.post(LOGOUT)
        client.cookies.set(COOKIE, "already-gone", path="/api/v1/auth")
        assert (await client.post(LOGOUT)).status_code == 204


class TestProtectedRoutes:
    async def test_me_requires_a_token(self, client):
        response = await client.get(ME)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    async def test_me_returns_the_profile(self, client, auth_headers):
        response = await client.get(ME, headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["email"] == "priya@example.com"

    async def test_malformed_token_is_rejected(self, client):
        response = await client.get(ME, headers={"Authorization": "Bearer nonsense"})
        assert response.status_code == 401

    async def test_token_signed_with_another_key_is_rejected(self, client, registered):
        import jwt

        forged = jwt.encode(
            {"sub": str(registered["user"]["id"]), "type": "access", "exp": 9999999999},
            "attacker-key",
            "HS256",
        )
        response = await client.get(ME, headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401

    async def test_profile_can_be_updated(self, client, auth_headers):
        response = await client.patch(
            ME, headers=auth_headers, json={"display_name": "Priya S.", "base_currency": "usd"}
        )

        assert response.status_code == 200
        assert response.json()["display_name"] == "Priya S."
        assert response.json()["base_currency"] == "USD"  # normalised


class TestAccountDeletion:
    async def test_removes_the_user_and_their_tokens(self, client, auth_headers, db_session):
        response = await client.delete(ME, headers=auth_headers)
        assert response.status_code == 202

        from app.modules.auth.models import RefreshToken, User

        assert await db_session.scalar(select(func.count()).select_from(User)) == 0
        assert await db_session.scalar(select(func.count()).select_from(RefreshToken)) == 0

    async def test_the_account_can_no_longer_sign_in(self, client, auth_headers):
        await client.delete(ME, headers=auth_headers)
        response = await client.post(
            LOGIN, json={"email": "priya@example.com", "password": VALID_PASSWORD}
        )
        assert response.status_code == 401

    async def test_the_audit_trail_survives_the_deletion(self, client, auth_headers, db_session):
        """The audit row has no foreign key to users precisely so it outlives
        them -- which is what makes the deletion provable."""
        await client.delete(ME, headers=auth_headers)

        from app.core.audit import AuditLog

        actions = (await db_session.execute(select(AuditLog.action))).scalars().all()
        assert "user.deleted" in actions

    async def test_the_email_becomes_available_again(self, client, auth_headers):
        await client.delete(ME, headers=auth_headers)
        response = await client.post(REGISTER, json=registration_payload())
        assert response.status_code == 201

    async def test_it_removes_the_financial_data_too(self, client, auth_headers, db_session):
        """The bug the tests above missed.

        They asserted the user row was gone and that signing in failed -- both
        true, and both true while every transaction, account, budget, and goal
        the user owned stayed in the database forever. `refresh_tokens` was the
        only table that cascaded; nineteen others carried a `user_id` with no
        foreign key at all. A local database with 71 users held 1,416,254
        transactions.

        Seeding demo data first is what gives this test teeth: it populates the
        tables that were actually leaking.
        """
        seeded = await client.post("/api/v1/imports/demo-seed", headers=auth_headers)
        assert seeded.status_code == 201

        before = await _user_owned_row_counts(db_session)
        assert before["transactions"] > 0, "demo seed wrote nothing; the test proves nothing"

        response = await client.delete(ME, headers=auth_headers)
        assert response.status_code == 202

        remaining = {t: n for t, n in (await _user_owned_row_counts(db_session)).items() if n}
        assert remaining == {}, f"survived the deletion: {remaining}"

    async def test_every_user_owned_table_cascades(self, db_session):
        """Structural, so a table added later cannot quietly reintroduce this.

        The behavioural test above only covers tables the demo seeder happens to
        populate. This one asks the database directly, and fails the moment
        someone adds a `user_id` without the constraint that makes it removable.

        `audit_log` is the deliberate exception -- it outlives the account by
        design, holding only an opaque id.
        """
        rows = (await db_session.execute(_MISSING_USER_CASCADE)).all()
        offenders = sorted(table for (table,) in rows if table not in _CASCADE_EXEMPT)
        assert offenders == [], f"user_id with no cascading foreign key: {offenders}"


class TestRateLimiting:
    """Limits are configurable per environment (E2E suites legitimately create
    many accounts from one IP), so these tests pin the values they rely on.
    That way relaxing a limit for local development can never silently disable
    the protection here."""

    @pytest.fixture(autouse=True)
    def strict_limits(self, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "login_attempts_per_account", 5, raising=False)
        monkeypatch.setattr(settings, "login_attempts_per_ip", 10, raising=False)
        monkeypatch.setattr(settings, "registrations_per_ip_per_hour", 5, raising=False)

    async def test_repeated_failed_logins_are_throttled(self, client):
        await client.post(REGISTER, json=registration_payload())

        statuses = [
            (
                await client.post(
                    LOGIN, json={"email": "priya@example.com", "password": "WrongPassword123"}
                )
            ).status_code
            for _ in range(8)
        ]

        assert 429 in statuses, f"never throttled: {statuses}"

    async def test_throttled_response_carries_retry_after(self, client):
        await client.post(REGISTER, json=registration_payload())

        for _ in range(8):
            response = await client.post(
                LOGIN, json={"email": "priya@example.com", "password": "WrongPassword123"}
            )
            if response.status_code == 429:
                assert response.json()["error"]["code"] == "RATE_LIMITED"
                assert int(response.headers["Retry-After"]) > 0
                return
        pytest.fail("rate limit never triggered")

    async def test_a_successful_login_clears_the_account_counter(self, client):
        """A user who finally remembers their password should not still be
        locked out by their own earlier typos."""
        await client.post(REGISTER, json=registration_payload())

        for _ in range(3):
            await client.post(
                LOGIN, json={"email": "priya@example.com", "password": "WrongPassword123"}
            )

        good = await client.post(
            LOGIN, json={"email": "priya@example.com", "password": VALID_PASSWORD}
        )
        assert good.status_code == 200
        assert (
            await client.post(
                LOGIN, json={"email": "priya@example.com", "password": VALID_PASSWORD}
            )
        ).status_code == 200

    async def test_registration_is_throttled_per_ip(self, client):
        statuses = [
            (
                await client.post(REGISTER, json=registration_payload(email=f"u{i}@example.com"))
            ).status_code
            for i in range(8)
        ]
        assert 429 in statuses, f"never throttled: {statuses}"
