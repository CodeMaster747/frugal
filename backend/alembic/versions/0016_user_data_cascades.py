"""cascade user-owned data on account deletion

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-06 14:25:00.000000

FR-1.8 requires that deleting an account removes everything it owns. It did not.
`refresh_tokens` was the only table that cascaded from `users`; the other
nineteen user-owned tables carried a `user_id` with no foreign key at all.
Deleting a user removed the login and left the transactions, receipts, budgets,
insights, forecasts, and notifications behind -- orphaned, unreachable, and
permanent.

It went unnoticed because the deletion test asserted that signing in afterwards
fails, which it did. Nothing asserted the data was gone. It surfaced while
purging accumulated local test users: 71 users remained, and 1,416,254
transactions did.

`audit_log` is deliberately excluded. Audit rows outlive the account by design,
holding only an opaque user id -- that is what makes a deletion provable after
the fact, and cascading here would erase the evidence that it happened.

Depends on 0015: without an index on every foreign key child column, the orphan
purge below cascades into sequential scans and does not finish in usable time.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Every table whose `user_id` names a row in `users`.
#:
#: `refresh_tokens` is absent because it already cascades; `audit_log` is absent
#: by design (see the module docstring).
#:
#: Ordered so that tables which cascade into others are emptied first --
#: deleting orphaned transactions directly is far cheaper than reaching them
#: through a cascade from `accounts`.
USER_OWNED: tuple[str, ...] = (
    "transactions",
    "accounts",
    "budgets",
    "categories",
    "categorization_feedback",
    "forecasts",
    "goals",
    "health_snapshots",
    "insights",
    "jobs",
    "notification_preferences",
    "notifications",
    "price_alerts",
    "purchase_evaluations",
    "receipt_fields",
    "receipt_line_items",
    "receipts",
    "recurring_items",
    "wishlist_items",
)


def _constraint(table: str) -> str:
    return f"fk_{table}_user_id_users"


def upgrade() -> None:
    for table in USER_OWNED:
        # `user_id IS NOT NULL` matters for `categories`, where the shared
        # system taxonomy has a NULL owner and must survive.
        op.execute(
            f"DELETE FROM {table} t "  # noqa: S608 -- table names are a fixed literal tuple
            "WHERE t.user_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.user_id)"
        )
        op.create_foreign_key(
            _constraint(table), table, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    for table in USER_OWNED:
        op.drop_constraint(_constraint(table), table, type_="foreignkey")
