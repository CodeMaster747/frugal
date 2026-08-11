"""Notification channels.

Two ship in v1.1:

* `InAppNotifier` — the row is the delivery. Writing it *is* the notification,
  so `send` only marks it delivered.
* `NullNotifier` — accepts and discards, for tests and for a deployment with no
  channel configured.

Email is deliberately absent. SES needs a verified domain and a production
account, both M11 concerns, and the `Notifier` port means that milestone adds an
adapter rather than reworking the engine. Building an unusable email path now
would be code that has never run pretending to be a feature.
"""

from __future__ import annotations

import uuid

from app.adapters.ports import Message
from app.core.logging import get_logger

logger = get_logger(__name__)


class InAppNotifier:
    """In-app delivery: the persisted notification is what the user sees."""

    name = "in_app"

    async def is_available(self, user_id: uuid.UUID) -> bool:
        del user_id
        # Always. A signed-in user can always read their own feed.
        return True

    async def send(self, user_id: uuid.UUID, message: Message) -> bool:
        # The row already exists; the service marks it delivered. Nothing to do
        # here, and saying so is better than an empty method that looks unfinished.
        del user_id, message
        return True


class NullNotifier:
    """Accepts everything, delivers nothing."""

    name = "null"

    async def is_available(self, user_id: uuid.UUID) -> bool:
        del user_id
        return False

    async def send(self, user_id: uuid.UUID, message: Message) -> bool:
        logger.debug(
            "notification discarded (null channel)",
            extra={"user": str(user_id), "subject": message.subject},
        )
        return False
