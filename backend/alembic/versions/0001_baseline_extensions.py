"""M0 baseline: required Postgres extensions

No tables yet -- M0 is the foundation skeleton. This migration establishes the
version history and installs the extensions later milestones depend on:

  citext   case-insensitive email comparison enforced by the database, so no
           code path can forget to lowercase (M1, users.email)
  pg_trgm  trigram GIN indexes for fuzzy merchant and product search
           (M2 transactions.merchant_normalized, M8 products.canonical_name)

Extensions are installed here rather than alongside their first use because
CREATE EXTENSION needs elevated privileges that are cleaner to grant once.

Revision ID: 0001
Revises:
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # Dropping extensions would cascade into any dependent column type, so this
    # is deliberately conservative: the downgrade is a no-op and the extensions
    # are left in place. They are inert when unused.
    pass
