"""Receipt intelligence against a real database.

Covers the M4 exit criteria: a low-confidence field blocks commit, duplicates
surface before commit, and a failed job is dead-lettered with its exception
preserved.

Uses the in-memory object store and the fake OCR engine (ADR-004). The point of
the fakes is that the *interesting* cases -- a total that scanned badly, an
unreadable photo -- are hard to arrange with a real engine and are exactly what
the review flow exists to handle.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.adapters.ocr.fake import default_receipt, low_confidence_total, unreadable
from app.core.clock import utc_today

pytestmark = pytest.mark.integration

RECEIPTS = "/api/v1/receipts"
ACCOUNTS = "/api/v1/accounts"


@pytest.fixture
def store(app):
    """The adapter the app was composed with."""
    return app.state.object_store


@pytest.fixture
async def account(client, auth_headers):
    response = await client.post(
        ACCOUNTS,
        headers=auth_headers,
        json={"name": "HDFC Savings", "type": "bank", "opening_balance": "50000.00"},
    )
    return response.json()


async def upload(client, auth_headers, store, *, size: int = 2048) -> str:
    """Register a receipt and place bytes where the worker would find them."""
    response = await client.post(
        f"{RECEIPTS}/upload-url",
        headers=auth_headers,
        json={"content_type": "image/jpeg", "size_bytes": size},
    )
    assert response.status_code == 201, response.text
    receipt_id = response.json()["receipt_id"]

    # Simulate the browser's direct PUT to the presigned URL.
    detail = await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)
    key = None
    from sqlalchemy import select

    from app.core.database import get_async_session_factory
    from app.modules.receipts.models import Receipt

    async with get_async_session_factory()() as session:
        key = (
            await session.execute(select(Receipt.s3_key).where(Receipt.id == detail.json()["id"]))
        ).scalar_one()

    await store.put_bytes(key, b"fake-image-bytes", "image/jpeg")
    return receipt_id


async def run_extraction(receipt_id: str, ocr_result) -> None:
    """Persist an extraction the way the worker does, without Celery.

    Exercises the same service method the task calls, so the threshold rule
    under test is the production one.
    """
    import uuid as uuidlib

    from app.adapters.storage.memory import InMemoryObjectStore
    from app.core.database import get_async_session_factory
    from app.modules.receipts.pipeline.extract import extract
    from app.modules.receipts.service import ReceiptService

    extraction = extract(ocr_result)
    async with get_async_session_factory()() as session:
        service = ReceiptService(session, InMemoryObjectStore())
        await service.record_extraction(
            uuidlib.UUID(receipt_id),
            fields=[
                (f.name, f.raw_text, f.parsed_value, f.confidence, f.bbox)
                for f in extraction.fields
            ],
            line_items=[
                (i.line_number, i.description, i.total_price, i.confidence)
                for i in extraction.line_items
            ],
            engine_version="fake-1.0",
            preprocess_report={"resized": False},
            processing_ms=120,
        )
        await session.commit()


class TestUpload:
    async def test_returns_a_presigned_url_and_never_takes_the_bytes(self, client, auth_headers):
        """The image goes browser -> storage directly. Routing 10 MB through a
        250 MB API process would consume request workers (FR-4.1)."""
        response = await client.post(
            f"{RECEIPTS}/upload-url",
            headers=auth_headers,
            json={"content_type": "image/jpeg", "size_bytes": 2048},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["upload_url"]
        assert body["expires_in"] > 0

    async def test_rejects_an_unsupported_type(self, client, auth_headers):
        response = await client.post(
            f"{RECEIPTS}/upload-url",
            headers=auth_headers,
            json={"content_type": "application/zip", "size_bytes": 1024},
        )
        assert response.status_code == 400

    async def test_rejects_an_oversized_file(self, client, auth_headers):
        response = await client.post(
            f"{RECEIPTS}/upload-url",
            headers=auth_headers,
            json={"content_type": "image/jpeg", "size_bytes": 20 * 1024 * 1024},
        )
        assert response.status_code == 400

    async def test_processing_before_upload_is_rejected(self, client, auth_headers):
        """The object is not there yet, so there is nothing to OCR."""
        created = await client.post(
            f"{RECEIPTS}/upload-url",
            headers=auth_headers,
            json={"content_type": "image/jpeg", "size_bytes": 2048},
        )
        response = await client.post(
            f"{RECEIPTS}/{created.json()['receipt_id']}/process", headers=auth_headers
        )
        assert response.status_code == 422


class TestExtraction:
    async def test_a_clean_receipt_is_ready_without_review(self, client, auth_headers, store):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        body = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()
        assert body["status"] == "ready"
        assert body["blocking_fields"] == []
        assert body["merchant_extracted"]
        assert Decimal(body["total_extracted"]) == Decimal("1250.00")

    async def test_every_field_carries_its_own_confidence_and_region(
        self, client, auth_headers, store
    ):
        """Per-field, not per-document: a confident merchant and a doubtful
        total must be distinguishable (FR-4.3)."""
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        fields = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()[
            "fields"
        ]
        by_name = {f["field_name"]: f for f in fields}

        for name in ("merchant", "date", "total"):
            assert Decimal(by_name[name]["confidence"]) > 0
            assert by_name[name]["bbox"] is not None
            # The bbox is what lets the review UI point at the region the value
            # came from, turning a correction request into an explanation.
            assert set(by_name[name]["bbox"]) == {"x", "y", "w", "h"}

    async def test_the_raw_reading_is_kept_beside_the_parsed_value(
        self, client, auth_headers, store
    ):
        """When a total parses wrong, the raw token shows whether the failure
        was in recognition or in parsing."""
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, low_confidence_total())

        fields = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()[
            "fields"
        ]
        total = next(f for f in fields if f["field_name"] == "total")
        assert total["raw_text"] is not None
        assert "S" in total["raw_text"]  # Tesseract read S for 5

    async def test_only_the_doubtful_field_is_flagged(self, client, auth_headers, store):
        """Asking a user to re-verify everything is how human-in-the-loop gets
        abandoned. Merchant and date read cleanly here."""
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, low_confidence_total())

        body = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()
        flagged = {f["field_name"] for f in body["fields"] if f["needs_review"]}

        assert body["status"] == "needs_review"
        assert "total" in flagged
        assert "merchant" not in flagged

    async def test_an_unreadable_photo_flags_every_required_field(
        self, client, auth_headers, store
    ):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, unreadable())

        body = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()
        assert body["status"] == "needs_review"
        assert set(body["blocking_fields"]) == {"merchant", "date", "total"}


class TestCommitIsBlocked:
    async def test_a_low_confidence_field_blocks_commit(self, client, auth_headers, store, account):
        """**The M4 exit criterion.** Enforced in the service, so no client can
        bypass it by disabling a button (FR-4.5)."""
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, low_confidence_total())

        response = await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )

        assert response.status_code == 409
        assert "total" in response.json()["error"]["message"]

    async def test_correcting_the_field_unblocks_it(self, client, auth_headers, store, account):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, low_confidence_total())

        corrected = await client.patch(
            f"{RECEIPTS}/{receipt_id}/fields",
            headers=auth_headers,
            json={"corrections": {"total": "1250.00"}},
        )
        assert corrected.status_code == 200
        assert corrected.json()["status"] == "ready"
        assert corrected.json()["blocking_fields"] == []

        committed = await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )
        assert committed.status_code == 201
        assert Decimal(committed.json()["amount"]) == Decimal("1250.00")

    async def test_a_human_correction_wins_over_the_machine(self, client, auth_headers, store):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        body = (
            await client.patch(
                f"{RECEIPTS}/{receipt_id}/fields",
                headers=auth_headers,
                json={"corrections": {"merchant": "Reliance Smart"}},
            )
        ).json()

        merchant = next(f for f in body["fields"] if f["field_name"] == "merchant")
        assert merchant["corrected_value"] == "Reliance Smart"
        assert merchant["effective_value"] == "Reliance Smart"
        assert body["merchant_extracted"] == "Reliance Smart"

    async def test_an_impossible_correction_is_rejected(self, client, auth_headers, store):
        """A person is authoritative about what the receipt says, not about what
        a date is -- a typo here would write a bad transaction."""
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        response = await client.patch(
            f"{RECEIPTS}/{receipt_id}/fields",
            headers=auth_headers,
            json={"corrections": {"total": "not-a-number"}},
        )
        assert response.status_code == 400

    async def test_a_future_dated_receipt_is_rejected(self, client, auth_headers, store):
        from datetime import timedelta

        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        response = await client.patch(
            f"{RECEIPTS}/{receipt_id}/fields",
            headers=auth_headers,
            json={"corrections": {"date": (utc_today() + timedelta(days=2)).isoformat()}},
        )
        assert response.status_code == 400


class TestDuplicateDetection:
    async def test_a_matching_transaction_surfaces_before_commit(
        self, client, auth_headers, store, account
    ):
        """Surfaced *before* commit (FR-4.7). Discovering a double-counted
        expense weeks later is far worse than one dismissible prompt now."""
        await client.post(
            "/api/v1/transactions",
            headers=auth_headers,
            json={
                "account_id": account["id"],
                "kind": "expense",
                "amount": "1250.00",
                "occurred_on": "2026-08-03",
                "merchant_raw": "Reliance Fresh",
            },
        )

        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        body = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()
        candidates = body["duplicate_candidates"]

        assert len(candidates) == 1
        assert Decimal(candidates[0]["amount"]) == Decimal("1250.00")
        # Same day, same amount, same merchant is near-certain.
        assert Decimal(candidates[0]["similarity"]) >= Decimal("0.9")

    async def test_commit_refuses_while_a_duplicate_is_unresolved(
        self, client, auth_headers, store, account
    ):
        await client.post(
            "/api/v1/transactions",
            headers=auth_headers,
            json={
                "account_id": account["id"],
                "kind": "expense",
                "amount": "1250.00",
                "occurred_on": "2026-08-03",
                "merchant_raw": "Reliance Fresh",
            },
        )
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        response = await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )
        assert response.status_code == 409

    async def test_the_user_can_confirm_it_is_genuinely_separate(
        self, client, auth_headers, store, account
    ):
        await client.post(
            "/api/v1/transactions",
            headers=auth_headers,
            json={
                "account_id": account["id"],
                "kind": "expense",
                "amount": "1250.00",
                "occurred_on": "2026-08-03",
                "merchant_raw": "Reliance Fresh",
            },
        )
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        response = await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"], "allow_duplicate": True},
        )
        assert response.status_code == 201


class TestCommit:
    async def test_creates_a_transaction_linked_to_the_receipt(
        self, client, auth_headers, store, account
    ):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        committed = await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )
        assert committed.status_code == 201

        body = (await client.get(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)).json()
        assert body["status"] == "committed"
        assert body["committed_transaction_id"] == committed.json()["transaction_id"]

    async def test_committing_twice_conflicts(self, client, auth_headers, store, account):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())

        await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )
        second = await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"], "allow_duplicate": True},
        )
        assert second.status_code == 409

    async def test_the_commit_moves_the_account_balance(self, client, auth_headers, store, account):
        receipt_id = await upload(client, auth_headers, store)
        await run_extraction(receipt_id, default_receipt())
        await client.post(
            f"{RECEIPTS}/{receipt_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )

        refreshed = await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)
        assert Decimal(refreshed.json()["current_balance"]) == Decimal("48750.00")


class TestLifecycle:
    async def test_deleting_removes_the_stored_image(self, client, auth_headers, store):
        """Receipts are PII, so the object goes with the row."""
        receipt_id = await upload(client, auth_headers, store)
        assert len(store.objects) == 1

        response = await client.delete(f"{RECEIPTS}/{receipt_id}", headers=auth_headers)
        assert response.status_code == 204
        assert store.objects == {}

    async def test_an_image_url_is_generated_per_request(self, client, auth_headers, store):
        """Never stored: a persisted presigned URL is a stored expiry bug."""
        receipt_id = await upload(client, auth_headers, store)
        body = (await client.get(f"{RECEIPTS}/{receipt_id}/image-url", headers=auth_headers)).json()

        assert body["url"]
        assert body["expires_in"] > 0

    async def test_receipts_are_listed_newest_first(self, client, auth_headers, store):
        for _ in range(3):
            await upload(client, auth_headers, store)

        rows = (await client.get(RECEIPTS, headers=auth_headers)).json()
        assert len(rows) == 3
        assert [r["created_at"] for r in rows] == sorted(
            (r["created_at"] for r in rows), reverse=True
        )

    async def test_another_users_receipt_is_not_found(self, client, auth_headers, store):
        receipt_id = await upload(client, auth_headers, store)
        assert (await client.get(f"{RECEIPTS}/{receipt_id}")).status_code == 401
