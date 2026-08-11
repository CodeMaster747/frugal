"""The task queue, as infrastructure.

The Celery app lives in `core` for the same reason the database engine does: it
is a connection to something external, not domain logic. Putting it in
`app.workers` forced every dispatcher to import a task module, which meant
`modules -> workers` -- backwards through the layering, and caught by the
import-linter contract rather than by review.

Dispatch is **by task name**, so a caller never imports the task it is
triggering. That keeps the dependency pointing inward and means a module can be
extracted later without dragging the worker's dependencies along.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "frugal",
    broker=str(settings.redis_url),
    backend=str(settings.redis_url),
    # Task modules are registered by the worker entrypoint, not here -- the API
    # process must never import OpenCV or Tesseract.
    include=[],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Acknowledge only after completion, so a worker killed mid-task requeues
    # it rather than silently dropping a user's receipt.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=3600,
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.receipts.*": {"queue": "ocr"},
        "app.workers.tasks.categorization.*": {"queue": "ml"},
        "app.workers.tasks.forecasting.*": {"queue": "ml"},
        "app.workers.tasks.market.*": {"queue": "default"},
        "app.workers.tasks.notifications.*": {"queue": "default"},
    },
    beat_schedule={
        # Daily price refresh for tracked products (M9). Early morning UTC is
        # mid-morning in India, after overnight sale prices have settled and
        # before most people shop.
        "refresh-tracked-prices": {
            "task": "app.workers.tasks.market.refresh_prices",
            "schedule": crontab(hour="3", minute="30"),
        },
        # Hourly, which sounds frequent for a daily digest and is not: users
        # pick their own digest hour, so the task must wake often enough to
        # catch each of them. A run with nothing to do costs one query per user.
        "run-notifications": {
            "task": "app.workers.tasks.notifications.run_notifications",
            "schedule": crontab(minute="15"),
        },
    },
)


# Task names, declared here so a dispatcher never imports the task module.
PROCESS_RECEIPT = "app.workers.tasks.receipts.process_receipt"
#: Tier-3 forecasting. Runs in the worker because the API image does not
#: install Prophet -- see `app.modules.forecasting.tiers`.
GENERATE_FORECAST = "app.workers.tasks.forecasting.generate_forecast"
#: Daily price refresh and drop detection (M9).
REFRESH_PRICES = "app.workers.tasks.market.refresh_prices"
#: Hourly notification generation and delivery (M10).
RUN_NOTIFICATIONS = "app.workers.tasks.notifications.run_notifications"


def dispatch(task_name: str, *, countdown: int = 0, **kwargs: Any) -> str:
    """Queue a task by name and return its id.

    `countdown` gives the API's transaction time to commit before a worker
    picks the job up -- otherwise the worker can read a row that does not exist
    yet.
    """
    result = celery_app.send_task(task_name, kwargs=kwargs, countdown=countdown)
    return str(result.id)
