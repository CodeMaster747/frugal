"""Request and response contracts for the auth module."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# --- requests --------------------------------------------------------------

Password = Annotated[str, Field(min_length=12, max_length=128)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    base_currency: Annotated[str, Field(min_length=3, max_length=3)] = "INR"
    timezone: Annotated[str, Field(max_length=64)] = "Asia/Kolkata"

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        """Length first, then variety.

        A 12-character minimum does more for real-world strength than
        composition rules, which mostly produce `Password1!`. The variety check
        is a light backstop against the most obvious cases.
        """
        checks = (
            (re.search(r"[a-z]", value), "a lowercase letter"),
            (re.search(r"[A-Z]", value), "an uppercase letter"),
            (re.search(r"\d", value), "a digit"),
        )
        missing = [label for ok, label in checks if not ok]
        if missing:
            raise ValueError(f"Password must contain {', '.join(missing)}")
        return value

    @field_validator("base_currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("Currency must be a 3-letter ISO 4217 code")
        return value.upper()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UpdateProfileRequest(BaseModel):
    display_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    base_currency: Annotated[str, Field(min_length=3, max_length=3)] | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None
    locale: Annotated[str, Field(max_length=16)] | None = None

    @field_validator("base_currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isalpha():
            raise ValueError("Currency must be a 3-letter ISO 4217 code")
        return value.upper()


# --- responses -------------------------------------------------------------


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str
    base_currency: str
    timezone: str
    locale: str
    is_demo_seeded: bool
    email_verified_at: datetime | None
    created_at: datetime


class TokenResponse(BaseModel):
    """Login/register/refresh payload.

    The refresh token is absent by design -- it travels only in an httpOnly
    cookie, unreadable by JavaScript. The access token is returned in the body
    for the client to hold in memory, where it dies with the tab (NFR-2).
    """

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — the OAuth2 scheme name, not a secret
    expires_in: int
    user: UserResponse
