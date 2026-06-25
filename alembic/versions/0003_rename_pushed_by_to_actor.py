"""Rename document_versions.pushed_by -> actor

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-25 00:00:00.000000

v4 introduces the Principal/SubProject/Document three-layer model (v4-ideas §18).
The pushed_by column was historically used to record both the SubProject of origin
and special cross-boundary actors ("agent:planner", "system", "bootstrap").
Renaming it to "actor" makes its real semantic role explicit: it is the
actor label of the write, not just "who pushed".

This is a pure rename — no data migration is required. SQLite supports
ALTER TABLE ... RENAME COLUMN since 3.25.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("document_versions") as batch_op:
        batch_op.alter_column("pushed_by", new_column_name="actor")


def downgrade() -> None:
    with op.batch_alter_table("document_versions") as batch_op:
        batch_op.alter_column("actor", new_column_name="pushed_by")
