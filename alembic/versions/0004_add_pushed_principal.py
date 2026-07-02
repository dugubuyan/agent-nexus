"""Add document_versions.pushed_principal

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-02 00:00:00.000000

v4 makes the Principal dimension explicit (v4-pre §8). Following the
"Principal is an annotation, not an entity" positioning, we do NOT add a
principals table; instead each write carries a self-attested role label,
recorded per DocumentVersion — the "git author" of the write.

pushed_principal is nullable: in the degenerate single-actor case (one
workspace = one agent = one SubProject) it stays NULL and behaviour is
unchanged. It activates only when multiple principals act on one boundary.

attestation != authorization: this column only records "who acted", it never
gates "who may write". See v4-pre §8.5.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("document_versions") as batch_op:
        batch_op.add_column(sa.Column("pushed_principal", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document_versions") as batch_op:
        batch_op.drop_column("pushed_principal")
