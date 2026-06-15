"""Add soft-delete support to documents table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-15 00:00:00.000000

Adds Document.status ('active' | 'deleted') and Document.deleted_at
to enable git-style soft deletion: deletion is recorded, never erased.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite does not support ADD COLUMN with a non-constant default inside a
    # transaction, so we use server_default (a string literal) instead.
    op.add_column(
        "documents",
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "status")
