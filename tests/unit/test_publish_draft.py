"""
Unit tests for DocumentService.publish_draft().

Covers:
- publish_draft() on a draft version succeeds and sets status=published
- publish_draft() on non-existent version raises INVALID_STATUS_TRANSITION
- publish_draft() on already-published version raises INVALID_STATUS_TRANSITION
- publish_draft() triggers notifications for subscribers (when pipeline services configured)
- publish_draft() on draft version sets published_at

Requirements: 11.3, 11.6
"""

from unittest.mock import MagicMock, patch

import pytest

from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.errors import DocExchangeError
from doc_exchange.services.notification_service import NotificationService
from doc_exchange.services.schemas import PushRequest
from doc_exchange.services.subscription_service import SubscriptionService
from doc_exchange.services.task_service import TaskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(db_session, tmp_docs_root, **kwargs):
    audit = AuditLogService(db=db_session)
    return DocumentService(
        db=db_session,
        docs_root=tmp_docs_root,
        audit_log_service=audit,
        **kwargs,
    )


def _push(svc, doc_id, content, pushed_by="agent-1", project_space_id="space-1", metadata=None):
    req = PushRequest(
        doc_id=doc_id,
        content=content,
        pushed_by=pushed_by,
        project_space_id=project_space_id,
        metadata=metadata or {},
    )
    return svc.push(req)


def _push_draft(svc, doc_id, content, project_space_id):
    """Push a document as system_llm (creates a draft version)."""
    return _push(svc, doc_id, content, pushed_by="system_llm", project_space_id=project_space_id)


# ---------------------------------------------------------------------------
# publish_draft() — success cases
# ---------------------------------------------------------------------------


def test_publish_draft_succeeds_and_sets_status_published(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push_draft(svc, "sub1/design", "# Draft content", project_space_id=default_space.id)

    result = svc.publish_draft("sub1/design", version=1, project_space_id=default_space.id)

    assert result["status"] == "published"
    assert result["doc_id"] == "sub1/design"
    assert result["version"] == 1


def test_publish_draft_updates_db_status_to_published(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push_draft(svc, "sub1/requirement", "# Draft", project_space_id=default_space.id)

    svc.publish_draft("sub1/requirement", version=1, project_space_id=default_space.id)

    # Verify via list_versions
    versions = svc.list_versions("sub1/requirement", default_space.id)
    assert versions[0].status == "published"


def test_publish_draft_sets_published_at(db_session, default_space, tmp_docs_root):
    from doc_exchange.models.entities import DocumentVersion

    svc = _make_service(db_session, tmp_docs_root)
    _push_draft(svc, "sub1/api", "# Draft API", project_space_id=default_space.id)

    # Confirm published_at is None before publishing
    dv_before = (
        db_session.query(DocumentVersion)
        .filter(
            DocumentVersion.document_id == "sub1/api",
            DocumentVersion.project_space_id == default_space.id,
            DocumentVersion.version == 1,
        )
        .first()
    )
    assert dv_before.published_at is None

    svc.publish_draft("sub1/api", version=1, project_space_id=default_space.id)

    db_session.expire(dv_before)
    assert dv_before.published_at is not None


# ---------------------------------------------------------------------------
# publish_draft() — error cases
# ---------------------------------------------------------------------------


def test_publish_draft_nonexistent_version_raises_invalid_status_transition(
    db_session, default_space, tmp_docs_root
):
    svc = _make_service(db_session, tmp_docs_root)
    # No document pushed at all
    with pytest.raises(DocExchangeError) as exc_info:
        svc.publish_draft("sub1/design", version=99, project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_STATUS_TRANSITION"


def test_publish_draft_nonexistent_doc_raises_invalid_status_transition(
    db_session, default_space, tmp_docs_root
):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.publish_draft("nonexistent/requirement", version=1, project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_STATUS_TRANSITION"


def test_publish_draft_already_published_raises_invalid_status_transition(
    db_session, default_space, tmp_docs_root
):
    svc = _make_service(db_session, tmp_docs_root)
    # Push as external agent → status=published
    _push(svc, "sub1/design", "# Published", pushed_by="agent-1", project_space_id=default_space.id)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.publish_draft("sub1/design", version=1, project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_STATUS_TRANSITION"


def test_publish_draft_already_published_error_message_contains_status(
    db_session, default_space, tmp_docs_root
):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/api", "# Published", pushed_by="agent-1", project_space_id=default_space.id)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.publish_draft("sub1/api", version=1, project_space_id=default_space.id)
    # Error message should mention the current status (Req 11.6)
    assert "published" in exc_info.value.message.lower()


def test_publish_draft_double_publish_raises_invalid_status_transition(
    db_session, default_space, tmp_docs_root
):
    svc = _make_service(db_session, tmp_docs_root)
    _push_draft(svc, "sub1/task", "# Draft", project_space_id=default_space.id)
    svc.publish_draft("sub1/task", version=1, project_space_id=default_space.id)

    # Second publish attempt should fail
    with pytest.raises(DocExchangeError) as exc_info:
        svc.publish_draft("sub1/task", version=1, project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_STATUS_TRANSITION"


# ---------------------------------------------------------------------------
# publish_draft() — pipeline / notification triggering
# ---------------------------------------------------------------------------


def test_publish_draft_triggers_notifications_for_subscribers(
    db_session, default_space, tmp_docs_root
):
    """When pipeline services are configured, publish_draft triggers notifications."""
    from doc_exchange.analyzer.analyzer_service import AnalyzerService
    from doc_exchange.analyzer.rule_engine import RuleEngineAnalyzer

    notification_svc = NotificationService(db=db_session)
    subscription_svc = SubscriptionService(db=db_session)
    task_svc = TaskService(db=db_session)
    audit = AuditLogService(db=db_session)
    rule_engine = RuleEngineAnalyzer()
    analyzer_svc = AnalyzerService(
        analyzer=rule_engine,
        fallback=rule_engine,
        audit_log_service=audit,
    )

    svc = _make_service(
        db_session,
        tmp_docs_root,
        analyzer_service=analyzer_svc,
        subscription_service=subscription_svc,
        notification_service=notification_svc,
        task_service=task_svc,
    )

    # Add a subscriber for the doc type
    import uuid
    from datetime import datetime, timezone
    from doc_exchange.models.entities import Subscription

    subscriber_id = str(uuid.uuid4())
    sub = Subscription(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        subscriber_project_id=subscriber_id,
        target_doc_id="sub1/requirement",
        target_doc_type=None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.flush()

    # Push a draft
    _push_draft(svc, "sub1/requirement", "# Draft requirement", project_space_id=default_space.id)

    # No notifications yet (draft doesn't trigger)
    unread_before = notification_svc.get_unread(subscriber_id, default_space.id)
    assert len(unread_before) == 0

    # Publish the draft
    svc.publish_draft("sub1/requirement", version=1, project_space_id=default_space.id)

    # Now subscriber should have a notification
    unread_after = notification_svc.get_unread(subscriber_id, default_space.id)
    assert len(unread_after) == 1
    assert unread_after[0].document_id == "sub1/requirement"
    assert unread_after[0].version == 1


def test_publish_draft_without_pipeline_services_does_not_raise(
    db_session, default_space, tmp_docs_root
):
    """publish_draft works fine even without pipeline services configured."""
    svc = _make_service(db_session, tmp_docs_root)
    _push_draft(svc, "sub1/design", "# Draft", project_space_id=default_space.id)

    # Should not raise even without analyzer/subscription/notification/task services
    result = svc.publish_draft("sub1/design", version=1, project_space_id=default_space.id)
    assert result["status"] == "published"
