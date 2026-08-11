"""Seed the system category taxonomy

Reference data, not schema, so it ships as a migration rather than a script:
every environment then has the same taxonomy at the same revision, and the
categoriser's label space (M5) is reproducible from the migration history alone.

Rows are inserted with deterministic UUIDs derived from their slug. That makes
the migration idempotent and keeps category ids identical across environments,
so a fixture or an exported model can reference one without a lookup.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from alembic import op
from sqlalchemy import String, column, table

from app.modules.finance.taxonomy import TAXONOMY

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed namespace so slug -> id is stable forever.
NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

categories = table(
    "categories",
    column("id"),
    column("user_id"),
    column("parent_id"),
    column("name", String),
    column("slug", String),
    column("kind", String),
    column("icon", String),
    column("is_system"),
    column("sort_order"),
)


def category_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"frugal.category.{slug}")


def upgrade() -> None:
    rows = []
    order = 0

    for node in TAXONOMY:
        rows.append(
            {
                "id": category_id(node.slug),
                "user_id": None,  # NULL marks the shared system taxonomy
                "parent_id": None,
                "name": node.name,
                "slug": node.slug,
                "kind": node.kind,
                "icon": node.icon,
                "is_system": True,
                "sort_order": order,
            }
        )
        order += 1

        for child_slug, child_name in node.children:
            rows.append(
                {
                    "id": category_id(child_slug),
                    "user_id": None,
                    "parent_id": category_id(node.slug),
                    "name": child_name,
                    "slug": child_slug,
                    "kind": node.kind,
                    "icon": node.icon,
                    "is_system": True,
                    "sort_order": order,
                }
            )
            order += 1

    op.bulk_insert(categories, rows)


def downgrade() -> None:
    # Only system rows; a user's own categories are untouched.
    op.execute("DELETE FROM categories WHERE is_system = true AND user_id IS NULL")
