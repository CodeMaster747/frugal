"""Idempotency keys for financial mutations (ADR-007).

Complements the database's ``content_hash`` constraint. The two protect against
different failures and neither subsumes the other:

* The **hash** stops duplicate *data* -- re-importing an overlapping statement.
* The **key** stops duplicate *requests* -- a retry after a timeout, or a
  double-click -- and, just as importantly, returns the *original response* so
  the client learns whether its write succeeded. A bare 409 from the hash is
  indistinguishable from a genuine conflict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.errors import ConflictError
from app.core.redis import get_redis

TTL_SECONDS = 86_400
_PREFIX = "idem"


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    body: dict[str, Any]


def _key(user_id: str, endpoint: str, idempotency_key: str) -> str:
    return f"{_PREFIX}:{user_id}:{endpoint}:{idempotency_key}"


def fingerprint(body: Any) -> str:
    """Stable hash of a request body, so a replay can be compared to it."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


async def lookup(
    user_id: str, endpoint: str, idempotency_key: str, body_fingerprint: str
) -> StoredResponse | None:
    """Return the original response for a replayed request.

    Raises ``ConflictError`` when the same key arrives with a *different* body.
    That combination means a client bug, and silently accepting it would let two
    different financial writes share one key -- corrupting exactly the data this
    mechanism exists to protect.
    """
    raw = await get_redis().get(_key(user_id, endpoint, idempotency_key))
    if raw is None:
        return None

    record = json.loads(raw)
    if record["fingerprint"] != body_fingerprint:
        raise ConflictError("This Idempotency-Key was already used with a different request body")
    return StoredResponse(status_code=record["status_code"], body=record["body"])


async def remember(
    user_id: str,
    endpoint: str,
    idempotency_key: str,
    body_fingerprint: str,
    status_code: int,
    body: Any,
) -> None:
    # set(..., ex=) rather than setex: the latter is deprecated in redis-py 8.
    await get_redis().set(
        _key(user_id, endpoint, idempotency_key),
        json.dumps(
            {"fingerprint": body_fingerprint, "status_code": status_code, "body": body},
            default=str,
        ),
        ex=TTL_SECONDS,
    )
