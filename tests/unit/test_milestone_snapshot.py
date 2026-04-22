"""
Unit tests for Task 15: Milestone Snapshot functionality.

Covers Requirements 12.1, 12.2, 12.3, 12.5, 7.7.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from doc_exchange.models.entities import (
    AuditLog,
    Document,
    DocumentVersion,
    DocumentVersionContent,
    Task,
)
from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.project_service import ProjectService
from doc_exchange.services.task_service import TaskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_document_service(db_session, tmp_docs_root):
    audit = AuditLogService(db_session)
    return DocumentService(db=db_session, docs_root=tmp_docs_root, audit_log_service=audit)


def make_project_service(db_session):
    return ProjectService(db_session)


def make_task_service(db_session):
    return TaskService(db_session)


def _add_document_with_published_version(
    db_session, space_id: str, subproject_id: str, doc_type: str = "requirement"
) -> tuple[Document, DocumentVersion]:
    """Helper: insert a Document + published DocumentVersion + content."""
    doc_id = f"{subproject_id}/{doc_type}"
    now = datetime.now(timezone.utc)

    doc = Document(
        id=doc_id,
        project_space_id=space_id,
        subproject_id=subproject_id,
        doc_type=doc_type,
        doc_variant=None,
        latest_version=1,
        created_at=now,
    )
    db_session.add(doc)

    ver_id = str(uuid.uuid4())
    ver = DocumentVersion(
        id=ver_id,
        document_id=doc_id,
        project_space_id=space_id,
        version=1,
        content_hash="abc123",
        pushed_by="agent-x",
        status="published",
        is_milestone=False,
        milestone_stage=None,
        pushed_at=now,
        published_at=now,
    )
    db_session.add(ver)

    content = DocumentVersionContent(
        version_id=ver_id,
        project_space_id=space_id,
        content="# Hello\nThis is the document content.",
    )
    db_session.add(content)
    db_session.flush()
    return doc, ver


def _add_document_draft_only(
    db_session, space_id: str, subproject_id: str, doc_type: str = "design"
) -> Document:
    """Helper: insert a Document with only a draft version (no published)."""
    doc_id = f"{subproject_id}/{doc_type}"
    now = datetime.now(timezone.utc)

    doc = Document(
        id=doc_id,
        project_space_id=space_id,
        subproject_id=subproject_id,
        doc_type=doc_type,
        doc_variant=None,
        latest_version=1,
        created_at=now,
    )
    db_session.add(doc)

    ver_id = str(uuid.uuid4())
    ver = DocumentVersion(
        id=ver_id,
        document_id=doc_id,
        project_space_id=space_id,
        version=1,
        content_hash="draft_hash",
        pushed_by="system_llm",
        status="draft",
        is_milestone=False,
        milestone_stage=None,
        pushed_at=now,
        published_at=None,
    )
    db_session.add(ver)
    db_session.flush()
    return doc


# ---------------------------------------------------------------------------
# Tests for DocumentService.create_milestone_snapshot()
# ---------------------------------------------------------------------------


def test_snapshot_creates_for_published_documents(db_session, default_space, tmp_docs_root):
    """create_milestone_snapshot() creates a snapshot for each published document (Req 12.1)."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())

    _add_document_with_published_version(db_session, default_space.id, subproject_id, "requirement")
    _add_document_with_published_version(db_session, default_space.id, subproject_id, "api")

    result = svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="testing",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    assert result["snapshots_created"] == 2
    assert result["skipped"] == 0


def test_snapshot_skips_documents_with_no_published_version(db_session, default_space, tmp_docs_root):
    """create_milestone_snapshot() skips documents with no published version (Req 12.5)."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())

    _add_document_draft_only(db_session, default_space.id, subproject_id, "design")

    result = svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="testing",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    assert result["snapshots_created"] == 0
    assert result["skipped"] == 1


def test_snapshot_logs_skipped_documents_to_audit_log(db_session, default_space, tmp_docs_root):
    """Skipped documents are logged to AuditLog (Req 12.5)."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())

    _add_document_draft_only(db_session, default_space.id, subproject_id, "design")

    svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="testing",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    logs = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.project_space_id == default_space.id,
            AuditLog.operation_type == "milestone_snapshot",
            AuditLog.result == "skipped",
        )
        .all()
    )
    assert len(logs) == 1
    assert "No published version" in logs[0].detail


def test_snapshot_has_is_milestone_true(db_session, default_space, tmp_docs_root):
    """Snapshot version has is_milestone=True (Req 12.2)."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())
    doc, _ = _add_document_with_published_version(db_session, default_space.id, subproject_id)

    svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="deployment",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    snapshot = (
        db_session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.is_milestone == True,
        )
        .first()
    )
    assert snapshot is not None
    assert snapshot.is_milestone is True


def test_snapshot_has_correct_milestone_stage(db_session, default_space, tmp_docs_root):
    """Snapshot records the correct stage name (Req 12.3)."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())
    doc, _ = _add_document_with_published_version(db_session, default_space.id, subproject_id)

    svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="deployment",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    snapshot = (
        db_session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.is_milestone == True,
        )
        .first()
    )
    assert snapshot.milestone_stage == "deployment"


def test_snapshot_content_matches_source_published_version(db_session, default_space, tmp_docs_root):
    """Snapshot content is identical to the source published version (Req 12.3)."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())
    doc, source_ver = _add_document_with_published_version(db_session, default_space.id, subproject_id)

    svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="testing",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    snapshot = (
        db_session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.is_milestone == True,
        )
        .first()
    )
    assert snapshot.content_hash == source_ver.content_hash

    snapshot_content = (
        db_session.query(DocumentVersionContent)
        .filter(DocumentVersionContent.version_id == snapshot.id)
        .first()
    )
    source_content = (
        db_session.query(DocumentVersionContent)
        .filter(DocumentVersionContent.version_id == source_ver.id)
        .first()
    )
    assert snapshot_content is not None
    assert snapshot_content.content == source_content.content


def test_snapshot_mixed_published_and_draft(db_session, default_space, tmp_docs_root):
    """Mixed documents: published ones get snapshots, draft-only ones are skipped."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())

    _add_document_with_published_version(db_session, default_space.id, subproject_id, "requirement")
    _add_document_draft_only(db_session, default_space.id, subproject_id, "design")

    result = svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="testing",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    assert result["snapshots_created"] == 1
    assert result["skipped"] == 1


def test_snapshot_no_documents_returns_zero(db_session, default_space, tmp_docs_root):
    """Subproject with no documents returns zeros."""
    svc = make_document_service(db_session, tmp_docs_root)
    subproject_id = str(uuid.uuid4())

    result = svc.create_milestone_snapshot(
        subproject_id=subproject_id,
        new_stage="testing",
        triggered_by=subproject_id,
        project_space_id=default_space.id,
    )

    assert result["snapshots_created"] == 0
    assert result["skipped"] == 0


# ---------------------------------------------------------------------------
# Tests for ProjectService.change_stage() with milestone snapshot
# ---------------------------------------------------------------------------


def test_change_stage_triggers_milestone_snapshot(db_session, default_space, tmp_docs_root):
    """change_stage() triggers milestone snapshot when document_service provided (Req 12.1)."""
    ps = make_project_service(db_session)
    ds = make_document_service(db_session, tmp_docs_root)

    project = ps.register(name="proj-a", type="development", project_space_id=default_space.id)
    _add_document_with_published_version(db_session, default_space.id, project.id, "requirement")

    ps.change_stage(
        project_id=project.id,
        new_stage="testing",
        project_space_id=default_space.id,
        document_service=ds,
    )

    snapshots = (
        db_session.query(DocumentVersion)
        .filter(
            DocumentVersion.project_space_id == default_space.id,
            DocumentVersion.is_milestone == True,
            DocumentVersion.milestone_stage == "testing",
        )
        .all()
    )
    assert len(snapshots) == 1


def test_change_stage_without_document_service_still_works(db_session, default_space):
    """change_stage() without document_service works (backward compat)."""
    ps = make_project_service(db_session)
    project = ps.register(name="proj-b", type="testing", project_space_id=default_space.id)

    updated = ps.change_stage(
        project_id=project.id,
        new_stage="deployment",
        project_space_id=default_space.id,
    )

    assert updated.stage == "deployment"


def test_change_stage_triggers_stage_switch_task(db_session, default_space):
    """change_stage() generates a stage-switch task when task_service provided (Req 7.7)."""
    ps = make_project_service(db_session)
    ts = make_task_service(db_session)

    project = ps.register(name="proj-c", type="ops", project_space_id=default_space.id)

    ps.change_stage(
        project_id=project.id,
        new_stage="deployment",
        project_space_id=default_space.id,
        task_service=ts,
    )

    tasks = (
        db_session.query(Task)
        .filter(
            Task.project_space_id == default_space.id,
            Task.assignee_project_id == project.id,
        )
        .all()
    )
    assert len(tasks) == 1
    assert tasks[0].title == "Stage switch"
    assert "deployment" in tasks[0].description


def test_change_stage_without_task_service_no_tasks_created(db_session, default_space):
    """change_stage() without task_service does not create tasks."""
    ps = make_project_service(db_session)
    project = ps.register(name="proj-d", type="development", project_space_id=default_space.id)

    ps.change_stage(
        project_id=project.id,
        new_stage="testing",
        project_space_id=default_space.id,
    )

    tasks = (
        db_session.query(Task)
        .filter(Task.project_space_id == default_space.id)
        .all()
    )
    assert len(tasks) == 0
