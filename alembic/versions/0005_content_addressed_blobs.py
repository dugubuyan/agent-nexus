"""Content-addressed blob storage

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-30 00:00:00.000000

Move document content from a per-version table (document_version_contents,
keyed by version_id) to a content-addressed store (blobs, keyed by
(project_space_id, content_hash)). A DocumentVersion already carries
content_hash, so it now references its content by hash rather than embedding a
full copy — identical content across versions, milestone snapshots, or
documents within a space is stored exactly once, mirroring Git's blob model.

Data migration: every existing content row is folded into blobs, deduplicated
on (project_space_id, content_hash) via the owning version's hash.
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blobs",
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("project_space_id", "content_hash"),
    )

    # Fold existing per-version content into the content-addressed store,
    # deduplicating on (project_space_id, content_hash).
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT dv.project_space_id AS space, dv.content_hash AS hash, dvc.content AS content "
            "FROM document_version_contents dvc "
            "JOIN document_versions dv ON dv.id = dvc.version_id"
        )
    )
    now = datetime.now(timezone.utc)
    seen: set[tuple[str, str]] = set()
    insert = sa.text(
        "INSERT INTO blobs (project_space_id, content_hash, content, created_at) "
        "VALUES (:space, :hash, :content, :created_at)"
    )
    for row in rows:
        key = (row.space, row.hash)
        if key in seen:
            continue
        seen.add(key)
        conn.execute(
            insert,
            {"space": row.space, "hash": row.hash, "content": row.content, "created_at": now},
        )

    op.drop_table("document_version_contents")


def downgrade() -> None:
    op.create_table(
        "document_version_contents",
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("version_id"),
    )

    # Rehydrate per-version content from the blob store via each version's hash.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT dv.id AS version_id, dv.project_space_id AS space, b.content AS content "
            "FROM document_versions dv "
            "JOIN blobs b ON b.project_space_id = dv.project_space_id "
            "AND b.content_hash = dv.content_hash "
            "WHERE dv.status != 'archived'"
        )
    )
    insert = sa.text(
        "INSERT INTO document_version_contents (version_id, project_space_id, content) "
        "VALUES (:version_id, :space, :content)"
    )
    for row in rows:
        conn.execute(
            insert,
            {"version_id": row.version_id, "space": row.space, "content": row.content},
        )

    op.drop_table("blobs")
