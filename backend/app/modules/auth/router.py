"""Auth HTTP layer.

Thin by design: parse, delegate to AuthService, shape the response. The only
logic here is cookie handling, which is genuinely a transport concern.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import CurrentUserDep, client_ip
from app.core.rate_limit import rate_limiter, refresh_per_user
from app.core.security import create_access_token, hash_refresh_token
from app.modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.modules.auth.service import AuthService, IssuedSession, RequestContext

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "frugal_refresh"
# Scoped to the auth path so the cookie is never sent on ordinary API calls.
# That keeps CSRF surface to these three endpoints instead of the whole API.
COOKIE_PATH = "/api/v1/auth"


def _context(request: Request) -> RequestContext:
    return RequestContext(
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )


def _set_refresh_cookie(response: Response, session: IssuedSession) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=session.refresh_token,
        max_age=settings.refresh_token_ttl_days * 86400,
        path=COOKIE_PATH,
        # httpOnly is the whole point: unreadable by JavaScript, so an XSS
        # payload cannot exfiltrate it (NFR-2).
        httponly=True,
        # Secure is disabled locally only because http://localhost has no TLS;
        # production is always HTTPS.
        secure=settings.is_production,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE, path=COOKIE_PATH, httponly=True)


def _token_response(session: IssuedSession) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(session.user.id),
        expires_in=settings.access_token_ttl_seconds,
        user=UserResponse.model_validate(session.user),
    )


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AuthService:
    return AuthService(db)


ServiceDep = Annotated[AuthService, Depends(get_service)]


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest, request: Request, response: Response, service: ServiceDep
) -> TokenResponse:
    """Create an account and sign in."""
    issued = await service.register(data, _context(request))
    _set_refresh_cookie(response, issued)
    return _token_response(issued)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest, request: Request, response: Response, service: ServiceDep
) -> TokenResponse:
    """Exchange credentials for a session."""
    issued = await service.login(data.email, data.password, _context(request))
    _set_refresh_cookie(response, issued)
    return _token_response(issued)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    service: ServiceDep,
    frugal_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Rotate the refresh token and issue a new access token.

    Replaying a token that was already exchanged revokes the entire family and
    returns `TOKEN_REUSE_DETECTED`, so the client stops retrying and forces a
    fresh sign-in.
    """
    ctx = _context(request)

    # Keyed on the session, not the IP. A per-IP limit here would make users
    # behind one NAT -- an office, a campus, a mobile carrier -- throttle each
    # other, and refresh volume scales with sessions rather than addresses.
    # The token hash is already how the session is identified server-side.
    session_key = hash_refresh_token(frugal_refresh) if frugal_refresh else ctx.ip_address
    await rate_limiter.check("refresh:session", session_key, refresh_per_user())

    issued = await service.refresh(frugal_refresh or "", ctx)
    _set_refresh_cookie(response, issued)
    return _token_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    service: ServiceDep,
    frugal_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    """Revoke the current session. Always succeeds."""
    await service.logout(frugal_refresh, _context(request))
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserResponse)
async def me(current: CurrentUserDep, service: ServiceDep) -> UserResponse:
    return UserResponse.model_validate(await service.get_user(current.id))


@router.patch("/me", response_model=UserResponse)
async def update_me(
    data: UpdateProfileRequest, request: Request, current: CurrentUserDep, service: ServiceDep
) -> UserResponse:
    user = await service.update_profile(current.id, data, _context(request))
    return UserResponse.model_validate(user)


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_me(
    request: Request, response: Response, current: CurrentUserDep, service: ServiceDep
) -> dict[str, str]:
    """Delete the account and all data owned by it.

    202 rather than 204: from M4 this also enqueues removal of the user's S3
    objects, which completes asynchronously.
    """
    await service.delete_account(current.id, _context(request))
    _clear_refresh_cookie(response)
    return {"status": "accepted"}
