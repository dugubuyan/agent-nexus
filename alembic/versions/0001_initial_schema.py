"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # project_spaces
    op.create_table(
        "project_spaces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # subprojects
    op.create_table(
        "subprojects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("stage_updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # documents
    op.create_table(
        "documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("subproject_id", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("doc_variant", sa.String(), nullable=True),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # document_versions
    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("pushed_by", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("is_milestone", sa.Boolean(), nullable=False),
        sa.Column("milestone_stage", sa.String(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # document_version_contents
    op.create_table(
        "document_version_contents",
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("version_id"),
    )

    # subscriptions
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("subscriber_project_id", sa.String(), nullable=False),
        sa.Column("target_doc_id", sa.String(), nullable=True),
        sa.Column("target_doc_type", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # notifications
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("recipient_project_id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("assignee_project_id", sa.String(), nullable=False),
        sa.Column("trigger_doc_id", sa.String(), nullable=False),
        sa.Column("trigger_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_space_id", sa.String(), nullable=False),
        sa.Column("operation_type", sa.String(), nullable=False),
        sa.Column("operated_at", sa.DateTime(), nullable=False),
        sa.Column("operator_project_id", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_space_id"], ["project_spaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("tasks")
    op.drop_table("notifications")
    op.drop_table("subscriptions")
    op.drop_table("document_version_contents")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("subprojects")
    op.drop_table("project_spaces")
