"""Password hashing, access tokens, and opaque refresh tokens.

Lives in core rather than the auth module because authenticating a request must
not require importing a domain module -- the access token is self-contained, so
verifying it needs no database query and no user table (ADR-001).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import UnauthenticatedError

# OWASP-recommended baseline: 19 MiB memory, 2 passes, 1 lane. Memory cost is
# what makes GPU cracking expensive, and 19 MiB stays comfortable alongside a
# 250 MB API process on a 1 GB instance.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

TokenType = Literal["access"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification. Returns False rather than raising."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was produced with weaker parameters than current.

    Lets cost parameters be raised over time and applied transparently on the
    user's next successful login.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


# --- access tokens ---------------------------------------------------------


def create_access_token(user_id: uuid.UUID, *, expires_in: timedelta | None = None) -> str:
    """Short-lived JWT held in memory by the client, never in storage.

    Self-contained by design: the API authenticates a request by verifying the
    signature, with no database round trip on the hot path.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = expires_in or timedelta(seconds=settings.access_token_ttl_seconds)

    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_access_token(token: str) -> uuid.UUID:
    """Verify an access token and return its subject.

    Raises UnauthenticatedError on any failure -- expiry, bad signature, wrong
    token type, malformed subject. The caller never distinguishes between them,
    because telling an attacker *why* a token failed is free information.
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthenticatedError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthenticatedError("Invalid access token") from exc

    if payload.get("type") != "access":
        # Blocks presenting some other signed token where an access token is
        # expected.
        raise UnauthenticatedError("Invalid access token")

    try:
        return uuid.UUID(payload["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise UnauthenticatedError("Invalid access token") from exc


# --- refresh tokens --------------------------------------------------------


def generate_refresh_token() -> str:
    """Opaque, high-entropy token.

    Not a JWT: refresh tokens must be revocable, and a self-contained token
    cannot be revoked before it expires. The database row is the authority.
    """
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 of the raw token; only this is stored.

    A database leak therefore yields no usable tokens. SHA-256 rather than
    Argon2 is correct here -- the input is 384 bits of entropy, so there is no
    dictionary to attack, and refresh happens often enough that a deliberately
    slow hash would be a self-inflicted latency cost.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)
