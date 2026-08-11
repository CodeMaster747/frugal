"""OAuth identity resolution (FR-1.5).

The Google redirect flow itself needs live credentials and is not exercised
here. What *is* security-relevant -- deciding whether an OAuth profile links to
an existing account or creates a new one -- lives in the service and is tested
directly with a synthetic profile. That keeps the decision covered without
depending on an external provider.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.modules.auth.models import OAuthProvider, User
from app.modules.auth.service import AuthService, RequestContext
from tests.conftest import VALID_PASSWORD, registration_payload

pytestmark = pytest.mark.integration

CTX = RequestContext(ip_address="203.0.113.7", user_agent="pytest")


async def link(service: AuthService, **overrides):
    payload = {
        "provider": OAuthProvider.GOOGLE,
        "subject": "google-subject-123",
        "email": "priya@example.com",
        "display_name": "Priya",
        "email_verified": True,
        "ctx": CTX,
    } | overrides
    return await service.link_or_create_oauth_user(**payload)


class TestNewUser:
    async def test_creates_an_account_with_no_password(self, db_session):
        service = AuthService(db_session)
        issued = await link(service)
        await db_session.commit()

        assert issued.user.email == "priya@example.com"
        # The check constraint permits a null password only because an OAuth
        # subject is present.
        assert issued.user.password_hash is None
        assert issued.user.oauth_subject == "google-subject-123"

    async def test_marks_the_email_verified_when_the_provider_does(self, db_session):
        issued = await link(AuthService(db_session))
        assert issued.user.email_verified_at is not None

    async def test_leaves_email_unverified_when_the_provider_does_not(self, db_session):
        issued = await link(AuthService(db_session), email_verified=False)
        assert issued.user.email_verified_at is None


class TestReturningUser:
    async def test_the_same_subject_reuses_the_account(self, db_session):
        service = AuthService(db_session)
        first = await link(service)
        await db_session.commit()

        second = await link(service)
        await db_session.commit()

        assert first.user.id == second.user.id
        assert await db_session.scalar(select(func.count()).select_from(User)) == 1

    async def test_each_sign_in_issues_a_new_session(self, db_session):
        service = AuthService(db_session)
        first = await link(service)
        await db_session.commit()
        second = await link(service)
        await db_session.commit()

        assert first.refresh_token != second.refresh_token


class TestLinkingToAnExistingAccount:
    async def test_links_when_the_provider_verified_the_email(self, client, db_session):
        await client.post("/api/v1/auth/register", json=registration_payload())

        issued = await link(AuthService(db_session))
        await db_session.commit()

        assert issued.user.oauth_subject == "google-subject-123"
        # Linked, not duplicated -- and the password still works.
        assert issued.user.password_hash is not None
        assert await db_session.scalar(select(func.count()).select_from(User)) == 1

    async def test_refuses_to_link_an_unverified_email(self, client, db_session):
        """The takeover this prevents: anyone able to set an arbitrary
        unverified address at the provider could otherwise claim a local
        account by signing in with it."""
        await client.post("/api/v1/auth/register", json=registration_payload())

        with pytest.raises(ConflictError, match="Sign in with your password"):
            await link(AuthService(db_session), email_verified=False)

    async def test_the_password_still_works_after_linking(self, client, db_session):
        await client.post("/api/v1/auth/register", json=registration_payload())
        await link(AuthService(db_session))
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "priya@example.com", "password": VALID_PASSWORD},
        )
        assert response.status_code == 200

    async def test_linking_is_audited(self, client, db_session):
        await client.post("/api/v1/auth/register", json=registration_payload())
        await link(AuthService(db_session))
        await db_session.commit()

        from app.core.audit import AuditLog

        actions = (await db_session.execute(select(AuditLog.action))).scalars().all()
        assert "oauth.linked" in actions
