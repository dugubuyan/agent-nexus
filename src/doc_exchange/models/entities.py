"""
SQLAlchemy ORM models for the Doc Exchange Center.

All tables include project_space_id for multi-tenant isolation (Requirement 10.5).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ProjectSpace(Base):
    """Top-level isolation unit for multi-tenancy (Requirement 10.1)."""

    __tablename__ = "project_spaces"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    subprojects: Mapped[list["SubProject"]] = relationship(back_populates="project_space")
    documents: Mapped[list["Document"]] = relationship(back_populates="project_space")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="project_space")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="project_space")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project_space")
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="project_space")


class SubProject(Base):
    """A sub-project within a Project_Space (Requirement 1.1)."""

    __tablename__ = "subprojects"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID, i.e. project_id
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # development | testing | ops | ...
    stage: Mapped[str] = mapped_column(String, nullable=False)  # design | development | testing | deployment | upgrade
    stage_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    project_space: Mapped["ProjectSpace"] = relationship(back_populates="subprojects")


class Document(Base):
    """A document entity, identified by {subproject_id}/{doc_type}[/{stage}] (Requirement 3.1)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # format: {subproject_id}/{doc_type}[/{stage}]
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    subproject_id: Mapped[str] = mapped_column(String, nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False)  # e.g. requirement | design | api | config | changelog | runbook | schema | test-plan
    doc_variant: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. config→dev/test/prod, api→rest/graphql, changelog→notes/breaking
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    project_space: Mapped["ProjectSpace"] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document")


class DocumentVersion(Base):
    """A specific version of a document (Requirement 3.2, 3.4)."""

    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    document_id: Mapped[str] = mapped_column(
        String, ForeignKey("documents.id"), nullable=False
    )
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)  # incremental version number
    content_hash: Mapped[str] = mapped_column(String, nullable=False)  # SHA-256 for dedup
    pushed_by: Mapped[str] = mapped_column(String, nullable=False)  # project_id or "system_llm"
    status: Mapped[str] = mapped_column(String, nullable=False, default="published")  # draft | published
    is_milestone: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    milestone_stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pushed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    document: Mapped["Document"] = relationship(back_populates="versions")
    content: Mapped[Optional["DocumentVersionContent"]] = relationship(
        back_populates="version_obj", uselist=False
    )


class DocumentVersionContent(Base):
    """Stores the actual Markdown content for a document version (Requirement 3.3)."""

    __tablename__ = "document_version_contents"

    version_id: Mapped[str] = mapped_column(
        String, ForeignKey("document_versions.id"), primary_key=True
    )
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown content

    # Relationships
    version_obj: Mapped["DocumentVersion"] = relationship(back_populates="content")


class Subscription(Base):
    """Defines which documents a sub-project subscribes to (Requirement 4.1)."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    subscriber_project_id: Mapped[str] = mapped_column(String, nullable=False)
    target_doc_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # exact doc subscription
    target_doc_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # doc type subscription
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    project_space: Mapped["ProjectSpace"] = relationship(back_populates="subscriptions")


class Notification(Base):
    """A change notification sent to a subscribing sub-project (Requirement 5.1)."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    recipient_project_id: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="unread")  # unread | read
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    project_space: Mapped["ProjectSpace"] = relationship(back_populates="notifications")


class Task(Base):
    """A work item triggered by a document change (Requirement 7.1)."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    assignee_project_id: Mapped[str] = mapped_column(String, nullable=False)
    trigger_doc_id: Mapped[str] = mapped_column(String, nullable=False)
    trigger_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending | in_progress | completed
    claimed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    project_space: Mapped["ProjectSpace"] = relationship(back_populates="tasks")


class AuditLog(Base):
    """Immutable audit log for all write operations (Requirement 9.1, 9.2)."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_space_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_spaces.id"), nullable=False
    )
    operation_type: Mapped[str] = mapped_column(String, nullable=False)  # push_document | ack_update | ...
    operated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC
    operator_project_id: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)  # identifier of the operated object
    result: Mapped[str] = mapped_column(String, nullable=False)  # success | failure
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # failure reason or extra info

    # Relationships
    project_space: Mapped["ProjectSpace"] = relationship(back_populates="audit_logs")
