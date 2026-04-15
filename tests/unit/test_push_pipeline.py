"""
Integration tests for DocumentService push pipeline.

Verifies that after a successful push():
- published status triggers NotificationService.generate() for subscribers
- published status triggers TaskService.generate() for affected projects
- draft status (system_llm) does NOT trigger notifications or tasks
- missing pipeline services still allows push() to succeed (backward compat)

Requirements: 5.1, 7.1, 11.2, 11.3
"""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from doc_exchange.analyzer.analyzer_service import AnalyzerService
from doc_exchange.analyzer.base import AffectedProject, AnalysisResult, TaskTemplate
from doc_exchange.analyzer.rule_engine import RuleEngineAnalyzer
from doc_exchange.models.entities import SubProject
from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.notification_service import NotificationService
from doc_exchange.services.schemas import PushRequest
from doc_exchange.services.subscription_service import SubscriptionService
from doc_exchange.services.task_service import TaskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subproject(db_session, space_id: str, sub_id: str, sub_type: str = "testing") -> SubProject:
    sp = SubProject(
        id=sub_id,
        project_space_id=space_id,
        name=f"Sub {sub_id}",
        type=sub_type,
        stage="development",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sp)
    db_session.flush()
    return sp


def _make_full_service(db_session, tmp_docs_root, space_id: str):
    """Build a DocumentService wired with all pipeline services."""
    audit = AuditLogService(db=db_session)
    notification_svc = NotificationService(db=db_session)
    task_svc = TaskService(db=db_session)
    subscription_svc = SubscriptionService(db=db_session)

    # Use RuleEngineAnalyzer as both primary and fallback
    rule_analyzer = RuleEngineAnalyzer()
    analyzer_svc = AnalyzerService(
        analyzer=rule_analyzer,
        fallback=rule_analyzer,
        audit_log_service=audit,
    )

    return DocumentService(
        db=db_session,
        docs_root=tmp_docs_root,
        audit_log_service=audit,
        analyzer_service=analyzer_svc,
        subscription_service=subscription_svc,
        notification_service=notification_svc,
        task_service=task_svc,
    ), notification_svc, task_svc, subscription_svc


def _push(svc, doc_id, content, pushed_by="agent-1", project_space_id="space-1", metadata=None):
    req = PushRequest(
        doc_id=doc_id,
        content=content,
        pushed_by=pushed_by,
        project_space_id=project_space_id,
        metadata=metadata or {},
    )
    return svc.push(req)


# ---------------------------------------------------------------------------
# Test: published push triggers notifications for subscribers
# ---------------------------------------------------------------------------


def test_published_push_triggers_notifications_for_subscribers(
    db_session, default_space, tmp_docs_root
):
    """Requirement 5.1, 11.3: published push generates notifications for subscribers."""
    space_id = default_space.id
    subscriber_id = str(uuid.uuid4())
    _make_subproject(db_session, space_id, subscriber_id, "testing")

    doc_svc, notification_svc, task_svc, subscription_svc = _make_full_service(
        db_session, tmp_docs_root, space_id
    )

    # Subscribe to "requirement" doc type
    subscription_svc.add_rule(
        subscriber_project_id=subscriber_id,
        project_space_id=space_id,
        target_doc_type="requirement",
    )

    # Push a published document
    _push(doc_svc, "sub1/requirement", "# Req v1", pushed_by="agent-1", project_space_id=space_id)

    # Subscriber should have an unread notification
    notifications = notification_svc.get_unread(subscriber_id, space_id)
    assert len(notifications) == 1
    assert notifications[0].document_id == "sub1/requirement"
    assert notifications[0].version == 1
    assert notifications[0].status == "unread"


# ---------------------------------------------------------------------------
# Test: published push triggers tasks for affected projects
# ---------------------------------------------------------------------------


def test_published_push_triggers_tasks_for_affected_projects(
    db_session, default_space, tmp_docs_root
):
    """Requirement 7.1, 11.3: published push generates tasks for affected subprojects."""
    space_id = default_space.id
    testing_sub_id = str(uuid.uuid4())
    _make_subproject(db_session, space_id, testing_sub_id, "testing")

    doc_svc, notification_svc, task_svc, subscription_svc = _make_full_service(
        db_session, tmp_docs_root, space_id
    )

    # Push a requirement doc — RuleEngineAnalyzer maps requirement → testing subprojects
    _push(doc_svc, "sub1/requirement", "# Req v1", pushed_by="agent-1", project_space_id=space_id)

    # Tasks should be generated for the testing subproject
    tasks = task_svc.get_by_doc_id("sub1/requirement", space_id)
    assert len(tasks) >= 1
    assert all(t.trigger_doc_id == "sub1/requirement" for t in tasks)
    assert all(t.trigger_version == 1 for t in tasks)


# ---------------------------------------------------------------------------
# Test: draft push (system_llm) does NOT trigger notifications
# ---------------------------------------------------------------------------


def test_draft_push_does_not_trigger_notifications(
    db_session, default_space, tmp_docs_root
):
    """Requirement 11.2: draft (system_llm) push must NOT generate notifications."""
    space_id = default_space.id
    subscriber_id = str(uuid.uuid4())
    _make_subproject(db_session, space_id, subscriber_id, "testing")

    doc_svc, notification_svc, task_svc, subscription_svc = _make_full_service(
        db_session, tmp_docs_root, space_id
    )

    subscription_svc.add_rule(
        subscriber_project_id=subscriber_id,
        project_space_id=space_id,
        target_doc_type="requirement",
    )

    # Push as system_llm → draft
    result = _push(
        doc_svc, "sub1/requirement", "# Draft req", pushed_by="system_llm", project_space_id=space_id
    )
    assert result.status == "draft"

    # No notifications should be generated
    notifications = notification_svc.get_unread(subscriber_id, space_id)
    assert len(notifications) == 0


# ---------------------------------------------------------------------------
# Test: draft push (system_llm) does NOT trigger tasks
# ---------------------------------------------------------------------------


def test_draft_push_does_not_trigger_tasks(
    db_session, default_space, tmp_docs_root
):
    """Requirement 11.2: draft (system_llm) push must NOT generate tasks."""
    space_id = default_space.id
    testing_sub_id = str(uuid.uuid4())
    _make_subproject(db_session, space_id, testing_sub_id, "testing")

    doc_svc, notification_svc, task_svc, subscription_svc = _make_full_service(
        db_session, tmp_docs_root, space_id
    )

    # Push as system_llm → draft
    result = _push(
        doc_svc, "sub1/requirement", "# Draft req", pushed_by="system_llm", project_space_id=space_id
    )
    assert result.status == "draft"

    # No tasks should be generated
    tasks = task_svc.get_by_doc_id("sub1/requirement", space_id)
    assert len(tasks) == 0


# ---------------------------------------------------------------------------
# Test: push() without pipeline services still works (backward compat)
# ---------------------------------------------------------------------------


def test_push_without_pipeline_services_succeeds(db_session, default_space, tmp_docs_root):
    """Backward compatibility: DocumentService without pipeline services still pushes."""
    audit = AuditLogService(db=db_session)
    doc_svc = DocumentService(
        db=db_session,
        docs_root=tmp_docs_root,
        audit_log_service=audit,
        # No pipeline services
    )

    result = _push(doc_svc, "sub1/design", "# Design", pushed_by="agent-1", project_space_id=default_space.id)
    assert result.version == 1
    assert result.status == "published"


# ---------------------------------------------------------------------------
# Test: second published push generates a second notification
# ---------------------------------------------------------------------------


def test_second_published_push_generates_new_notification(
    db_session, default_space, tmp_docs_root
):
    """Each new published version generates a new notification for subscribers."""
    space_id = default_space.id
    subscriber_id = str(uuid.uuid4())
    _make_subproject(db_session, space_id, subscriber_id, "testing")

    doc_svc, notification_svc, task_svc, subscription_svc = _make_full_service(
        db_session, tmp_docs_root, space_id
    )

    subscription_svc.add_rule(
        subscriber_project_id=subscriber_id,
        project_space_id=space_id,
        target_doc_type="requirement",
    )

    _push(doc_svc, "sub1/requirement", "# v1", pushed_by="agent-1", project_space_id=space_id)
    _push(doc_svc, "sub1/requirement", "# v2", pushed_by="agent-1", project_space_id=space_id)

    notifications = notification_svc.get_unread(subscriber_id, space_id)
    assert len(notifications) == 2
    versions = {n.version for n in notifications}
    assert versions == {1, 2}
