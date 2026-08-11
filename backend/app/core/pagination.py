"""Cursor (keyset) pagination.

Offset pagination skips or duplicates rows when data is inserted mid-scroll,
which is unacceptable on a transaction ledger the user is actively adding to.
The cursor encodes ``(occurred_on, id)`` -- exactly the columns of
``ix_transactions_user_id_occurred_on`` -- so the predicate is an index range
scan no matter how deep the user scrolls.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.errors import ValidationError

T = TypeVar("T")

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class Cursor:
    """Position in a date-ordered ledger."""

    occurred_on: date
    entity_id: uuid.UUID

    def encode(self) -> str:
        payload = {"d": self.occurred_on.isoformat(), "i": str(self.entity_id)}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            return cls(
                occurred_on=date.fromisoformat(payload["d"]), entity_id=uuid.UUID(payload["i"])
            )
        except Exception as exc:
            # An opaque cursor is client-supplied, so a malformed one is a bad
            # request rather than a server fault.
            raise ValidationError("Invalid pagination cursor") from exc


class PageMeta(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False
    limit: int = DEFAULT_PAGE_SIZE


class Page(BaseModel, Generic[T]):
    data: list[T]
    pagination: PageMeta


class PaginationParams(BaseModel):
    cursor: str | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    def decoded(self) -> Cursor | None:
        return Cursor.decode(self.cursor) if self.cursor else None
