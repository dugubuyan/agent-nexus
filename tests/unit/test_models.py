"""
Smoke tests for ORM model definitions and conftest fixtures.

Verifies:
- All tables are created correctly in the in-memory SQLite DB
- All ORM models can be instantiated and persisted
- project_space_id is present on all core entities (Requirement 10.5)
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from doc_exchange.models import (
    AuditLog,
    Base,
    Document,
    DocumentVersion,
    DocumentVersionContent,
    Notification,
    ProjectSpace,
    SubProject,
    Subscription,
    Task,
)


EXPECTED_TABLES = {
    "project_spaces",
    "subprojects",
    "documents",
    "document_versions",
    "document_version_contents",
    "subscriptions",
    "notifications",
    "tasks",
    "audit_logs",
}


def test_all_tables_exist(engine):
    """All expected tables should be present in the schema."""
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    assert EXPECTED_TABLES.issubset(actual_tables)


def test_project_space_id_on_all_tables(engine):
    """Every core entity table (except project_spaces itself) must have project_space_id."""
    inspector = inspect(engine)
    tables_needing_space_id = EXPECTED_TABLES - {"project_spaces"}
    for table_name in tables_needing_space_id:
        columns = {col["name"] for col in inspector.get_columns(table_name)}
        assert "project_space_id" in columns, (
            f"Table '{table_name}' is missing project_space_id column"
        )


def test_create_project_space(db_session: Session):
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="Test Space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(space)
    db_session.flush()
    fetched = db_session.get(ProjectSpace, space.id)
    assert fetched is not None
    assert fetched.name == "Test Space"
    assert fetched.status == "active"


def test_create_subproject(db_session: Session, default_space: ProjectSpace):
    sub = SubProject(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        name="Backend Service",
        type="development",
        stage="design",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.flush()
    fetched = db_session.get(SubProject, sub.id)
    assert fetched is not None
    assert fetched.project_space_id == default_space.id


def test_create_document_and_version(db_session: Session, default_space: ProjectSpace):
    sub_id = str(uuid.uuid4())
    doc = Document(
        id=f"{sub_id}/requirement",
        project_space_id=default_space.id,
        subproject_id=sub_id,
        doc_type="requirement",
        config_stage=None,
        latest_version=1,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(doc)
    db_session.flush()

    version = DocumentVersion(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        project_space_id=default_space.id,
        version=1,
        content_hash="abc123",
        pushed_by=sub_id,
        status="published",
        is_milestone=False,
        milestone_stage=None,
        pushed_at=datetime.now(timezone.utc),
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    db_session.flush()

    content = DocumentVersionContent(
        version_id=version.id,
        project_space_id=default_space.id,
        content="# Requirements\n\nSome content here.",
    )
    db_session.add(content)
    db_session.flush()

    fetched_doc = db_session.get(Document, doc.id)
    assert fetched_doc is not None
    assert fetched_doc.latest_version == 1
    assert len(fetched_doc.versions) == 1
    assert fetched_doc.versions[0].content.content == "# Requirements\n\nSome content here."


def test_create_subscription(db_session: Session, default_space: ProjectSpace):
    sub = Subscription(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        subscriber_project_id=str(uuid.uuid4()),
        target_doc_type="requirement",
        target_doc_id=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.flush()
    fetched = db_session.get(Subscription, sub.id)
    assert fetched is not None
    assert fetched.target_doc_type == "requirement"


def test_create_notification(db_session: Session, default_space: ProjectSpace):
    notif = Notification(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        recipient_project_id=str(uuid.uuid4()),
        document_id="some-sub/requirement",
        version=1,
        status="unread",
        created_at=datetime.now(timezone.utc),
        read_at=None,
    )
    db_session.add(notif)
    db_session.flush()
    fetched = db_session.get(Notification, notif.id)
    assert fetched is not None
    assert fetched.status == "unread"


def test_create_task(db_session: Session, default_space: ProjectSpace):
    task = Task(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        assignee_project_id=str(uuid.uuid4()),
        trigger_doc_id="some-sub/api",
        trigger_version=2,
        title="Review API changes",
        description="The API document was updated, please review.",
        status="pending",
        claimed_by=None,
        claimed_at=None,
        completed_at=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    db_session.flush()
    fetched = db_session.get(Task, task.id)
    assert fetched is not None
    assert fetched.status == "pending"


def test_create_audit_log(db_session: Session, default_space: ProjectSpace):
    log = AuditLog(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        operation_type="push_document",
        operated_at=datetime.now(timezone.utc),
        operator_project_id=str(uuid.uuid4()),
        target_id="some-sub/requirement",
        result="success",
        detail=None,
    )
    db_session.add(log)
    db_session.flush()
    fetched = db_session.get(AuditLog, log.id)
    assert fetched is not None
    assert fetched.result == "success"


def test_tmp_docs_root_fixture(tmp_docs_root):
    """The tmp_docs_root fixture should provide an existing directory."""
    assert os.path.isdir(tmp_docs_root)


import os
