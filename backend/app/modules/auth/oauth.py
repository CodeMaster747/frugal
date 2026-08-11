"""Google OAuth 2.0 sign-in (FR-1.5).

The routes are registered only when credentials are configured, so a deployment
without them simply has no OAuth endpoints rather than endpoints that fail at
runtime.

The identity resolution -- link to an existing account, or create a new one --
lives in `AuthService.link_or_create_oauth_user` and is unit-tested directly
with a synthetic profile. That keeps the security-relevant decision testable
without live Google credentials.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import UnauthenticatedError, ValidationError
from app.core.logging import get_logger
from app.modules.auth.models import OAuthProvider
from app.modules.auth.router import _context, _set_refresh_cookie
from app.modules.auth.service import AuthService

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/oauth", tags=["auth"])

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
_STATE_COOKIE = "frugal_oauth_state"


def build_oauth_client() -> OAuth:
    settings = get_settings()
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=(
            settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else None
        ),
        server_metadata_url=GOOGLE_METADATA_URL,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


_oauth: OAuth | None = None


def _google() -> Any:
    global _oauth
    if _oauth is None:
        _oauth = build_oauth_client()
    return _oauth.create_client("google")


@router.get("/google", summary="Begin Google sign-in")
async def google_authorize(request: Request) -> RedirectResponse:
    settings = get_settings()

    # CSRF defence for the OAuth flow: a nonce echoed back via the `state`
    # parameter and compared against a cookie the attacker cannot set.
    state = secrets.token_urlsafe(24)
    redirect = await _google().authorize_redirect(
        request, settings.google_redirect_uri, state=state
    )
    redirect.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    return redirect  # type: ignore[no-any-return]


@router.get("/google/callback", summary="Complete Google sign-in")
async def google_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    state: str | None = None,
) -> RedirectResponse:
    settings = get_settings()

    expected = request.cookies.get(_STATE_COOKIE)
    if not expected or not state or not secrets.compare_digest(expected, state):
        raise ValidationError("OAuth state mismatch; please start sign-in again")

    try:
        token = await _google().authorize_access_token(request)
    except OAuthError as exc:
        logger.warning("google oauth exchange failed", extra={"error": exc.error})
        raise UnauthenticatedError("Google sign-in failed") from exc

    profile: dict[str, Any] = token.get("userinfo") or {}
    subject, email = profile.get("sub"), profile.get("email")
    if not subject or not email:
        raise UnauthenticatedError("Google did not return an email address")

    issued = await AuthService(db).link_or_create_oauth_user(
        provider=OAuthProvider.GOOGLE,
        subject=subject,
        email=email,
        display_name=profile.get("name") or email.split("@")[0],
        email_verified=bool(profile.get("email_verified")),
        ctx=_context(request),
    )

    # Redirect to the frontend, which completes sign-in by calling /refresh.
    # The access token is deliberately not put in the URL: query strings land
    # in browser history, server logs, and Referer headers.
    response = RedirectResponse(url=f"{settings.frontend_url}/auth/callback")
    _set_refresh_cookie(response, issued)
    response.delete_cookie(_STATE_COOKIE)
    return response
