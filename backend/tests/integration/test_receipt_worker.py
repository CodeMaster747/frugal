"""The receipt worker's failure handling.

Covers the M4 exit criterion that a failed job lands in the dead-letter state
with its exception preserved. The task is invoked directly rather than through
a broker: what is under test is the state machine, not Celery's delivery.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.jobs import Job, JobStatus
from app.modules.receipts.models import Receipt, ReceiptStatus
from app.workers.tasks import receipts as task

pytestmark = pytest.mark.integration


@pytest.fixture
async def queued_job(client, auth_headers, app):
    """A receipt with bytes in the store and a queued job pointing at it."""
    store = app.state.object_store

    created = await client.post(
        "/api/v1/receipts/upload-url",
        headers=auth_headers,
        json={"content_type": "image/jpeg", "size_bytes": 2048},
    )
    receipt_id = uuid.UUID(created.json()["receipt_id"])

    from app.core.database import get_async_session_factory

    async with get_async_session_factory()() as session:
        receipt = await session.get(Receipt, receipt_id)
        assert receipt is not None
        await store.put_bytes(receipt.s3_key, b"not-a-real-image", "image/jpeg")

        job = Job(
            user_id=receipt.user_id,
            task_name="process_receipt",
            status=JobStatus.QUEUED.value,
            payload={"receipt_id": str(receipt_id)},
            request_id="test-request",
        )
        session.add(job)
        await session.commit()
        return job.id, receipt_id


def _sync_get(model, pk):
    from app.core.database import sync_session

    with sync_session() as session:
        return session.get(model, pk)


class TestDeadLettering:
    async def test_an_undecodable_image_is_dead_lettered_not_retried(self, queued_job):
        """The M4 exit criterion.

        A malformed image fails identically forever, so retrying it wastes a
        worker three times and delays the user's answer. It is dead-lettered on
        the first attempt instead.
        """
        job_id, _receipt_id = queued_job

        task._record_failure(str(job_id), ValueError("Could not decode the image"), retries=0)

        job = _sync_get(Job, job_id)
        assert job.status == JobStatus.DEAD_LETTERED.value
        assert job.error_type == "ValueError"
        # The message, not just the type: "could not decode" and "connection
        # refused" need different responses from whoever reads this.
        assert "decode" in job.error_message
        assert job.finished_at is not None

    async def test_a_transient_failure_stays_retryable(self, queued_job):
        """A storage blip should be retried, not abandoned."""
        job_id, _ = queued_job

        task._record_failure(str(job_id), OSError("connection reset"), retries=0)

        job = _sync_get(Job, job_id)
        assert job.status == JobStatus.FAILED.value
        assert job.finished_at is None  # not terminal yet

    async def test_exhausting_retries_dead_letters(self, queued_job):
        job_id, _ = queued_job

        task._record_failure(str(job_id), OSError("still down"), retries=task.MAX_RETRIES)

        job = _sync_get(Job, job_id)
        assert job.status == JobStatus.DEAD_LETTERED.value
        assert job.finished_at is not None

    async def test_the_receipt_is_marked_failed_with_a_readable_reason(self, queued_job):
        """The user needs to know why, not just that the spinner stopped."""
        job_id, receipt_id = queued_job

        task._record_failure(str(job_id), ValueError("Could not decode the image"), retries=0)

        receipt = _sync_get(Receipt, receipt_id)
        assert receipt.status == ReceiptStatus.FAILED.value
        assert "decode" in receipt.error_message


class TestProgress:
    async def test_progress_is_recorded_in_postgres(self, queued_job):
        """Job state lives in Postgres, not Redis: an eviction must not lose a
        user's receipt, and the client always has something to poll."""
        job_id, _ = queued_job

        task._progress(str(job_id), "ocr", 60)

        job = _sync_get(Job, job_id)
        assert job.progress == {"stage": "ocr", "pct": 60}


class TestIdempotency:
    async def test_a_redelivered_task_does_not_reprocess(self, queued_job, db_session):
        """`acks_late` means a task can be redelivered after it succeeded.
        Re-running OCR would cost a second run and overwrite human corrections.
        """
        job_id, _ = queued_job

        from app.core.database import sync_session

        with sync_session() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.SUCCEEDED.value
            session.commit()

        result = task.process_receipt.run(job_id=str(job_id))
        assert result == {"status": JobStatus.SUCCEEDED.value}

    async def test_one_in_flight_job_per_receipt(self, client, auth_headers, app):
        """A double-click must not run OCR twice and pay for it twice."""
        store = app.state.object_store
        created = await client.post(
            "/api/v1/receipts/upload-url",
            headers=auth_headers,
            json={"content_type": "image/jpeg", "size_bytes": 2048},
        )
        receipt_id = created.json()["receipt_id"]

        from app.core.database import get_async_session_factory

        async with get_async_session_factory()() as session:
            key = (
                await session.execute(
                    select(Receipt.s3_key).where(Receipt.id == uuid.UUID(receipt_id))
                )
            ).scalar_one()
        await store.put_bytes(key, b"bytes", "image/jpeg")

        first = await client.post(f"/api/v1/receipts/{receipt_id}/process", headers=auth_headers)
        assert first.status_code == 202

        # The second call is refused because the receipt is no longer pending.
        second = await client.post(f"/api/v1/receipts/{receipt_id}/process", headers=auth_headers)
        assert second.status_code == 422


class TestJobPolling:
    async def test_a_job_can_be_polled_by_its_owner(self, client, auth_headers, queued_job):
        job_id, _ = queued_job

        response = await client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["task_name"] == "process_receipt"
        assert response.json()["status"] == JobStatus.QUEUED.value

    async def test_a_job_id_does_not_expose_another_users_work(self, client, queued_job):
        job_id, _ = queued_job
        assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 401
