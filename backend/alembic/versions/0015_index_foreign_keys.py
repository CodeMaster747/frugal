"""index every foreign key child column

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-06 14:20:00.000000

Postgres indexes the *parent* side of a foreign key automatically and the child
side not at all. Nineteen of ours had no index, which is invisible until
something deletes a parent row: the check for referencing children is
``WHERE child_col = $1``, and with no index that is a sequential scan of the
whole child table, once per deleted parent.

Found by timing. Deleting 14,279 orphaned `accounts` ran for over ten minutes
before it was cancelled, because `transactions.account_id` cascades and was
unindexed -- so each account scanned all 1.4 M transactions. Account deletion
was effectively O(accounts x transactions).

Nullable columns get a partial index. Most of these are sparse -- a transaction
usually has no receipt, no recurring item, and no transfer pair -- so
``WHERE col IS NOT NULL`` produces a far smaller index that still satisfies the
referential check, since ``col = $1`` can never match a NULL. That matters on
`transactions`, which is the hottest write path in the product and is gaining
five indexes here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: (table, column, nullable). Nullable columns get a partial index.
FK_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("budgets", "category_id", True),
    ("categories", "parent_id", True),
    ("categorization_feedback", "corrected_category_id", False),
    ("categorization_feedback", "predicted_category_id", True),
    ("categorization_feedback", "transaction_id", True),
    ("goals", "linked_account_id", True),
    ("insights", "subject_id", True),
    ("price_alerts", "product_id", False),
    ("price_alerts", "wishlist_item_id", False),
    ("purchase_evaluations", "product_id", True),
    ("receipts", "committed_transaction_id", True),
    ("recurring_items", "account_id", True),
    ("recurring_items", "category_id", True),
    ("transactions", "account_id", False),
    ("transactions", "category_id", True),
    ("transactions", "receipt_id", True),
    ("transactions", "recurring_item_id", True),
    ("transactions", "transfer_pair_id", True),
    ("wishlist_items", "product_id", False),
)


def _name(table: str, column: str) -> str:
    return f"ix_{table}_{column}"


def upgrade() -> None:
    for table, column, nullable in FK_COLUMNS:
        op.create_index(
            _name(table, column),
            table,
            [column],
            postgresql_where=sa.text(f"{column} IS NOT NULL") if nullable else None,
        )


def downgrade() -> None:
    for table, column, _ in FK_COLUMNS:
        op.drop_index(_name(table, column), table_name=table)
