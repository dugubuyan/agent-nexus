"""
Unit tests for NotificationService (Requirements 5.1 – 5.5).
"""

import pytest
from sqlalchemy.orm import Session

from doc_exchange.services import DocExchangeError, NotificationService


def make_svc(db_session: Session) -> NotificationService:
    return NotificationService(db_session)


# ---------------------------------------------------------------------------
# generate() tests
# ---------------------------------------------------------------------------


def test_generate_creates_unread_notifications_for_all_subscribers(db_session, default_space):
    """generate() creates one unread notification per subscriber (Req 5.1)."""
    svc = make_svc(db_session)
    subscriber_ids = ["proj-a", "proj-b", "proj-c"]

    notifications = svc.generate(
        doc_id="svc-x/api",
        version=1,
        subscriber_ids=subscriber_ids,
        project_space_id=default_space.id,
    )

    assert len(notifications) == 3
    for n in notifications:
        assert n.status == "unread"
        assert n.document_id == "svc-x/api"
        assert n.version == 1
        assert n.project_space_id == default_space.id


def test_generate_is_idempotent_same_doc_version_recipient(db_session, default_space):
    """generate() skips duplicates for same (doc_id, version, recipient) (Req 5.5)."""
    svc = make_svc(db_session)

    first = svc.generate(
        doc_id="svc-x/api",
        version=2,
        subscriber_ids=["proj-a"],
        project_space_id=default_space.id,
    )
    second = svc.generate(
        doc_id="svc-x/api",
        version=2,
        subscriber_ids=["proj-a"],
        project_space_id=default_space.id,
    )

    assert len(first) == 1
    assert len(second) == 0  # skipped — already exists

    # Only one notification in DB
    unread = svc.get_unread("proj-a", default_space.id)
    assert len(unread) == 1


def test_generate_different_versions_create_separate_notifications(db_session, default_space):
    """generate() creates separate notifications for different versions."""
    svc = make_svc(db_session)

    svc.generate("svc-x/api", 1, ["proj-a"], default_space.id)
    svc.generate("svc-x/api", 2, ["proj-a"], default_space.id)

    unread = svc.get_unread("proj-a", default_space.id)
    assert len(unread) == 2


# ---------------------------------------------------------------------------
# get_unread() tests
# ---------------------------------------------------------------------------


def test_get_unread_returns_only_unread_notifications(db_session, default_space):
    """get_unread() returns only unread notifications (Req 5.2)."""
    svc = make_svc(db_session)
    notifications = svc.generate("doc/req", 1, ["proj-a"], default_space.id)

    # Ack one notification
    svc.ack(notifications[0].id, "proj-a", default_space.id)

    unread = svc.get_unread("proj-a", default_space.id)
    assert len(unread) == 0


def test_get_unread_returns_correct_recipient_only(db_session, default_space):
    """get_unread() returns notifications only for the specified recipient."""
    svc = make_svc(db_session)
    svc.generate("doc/api", 1, ["proj-a", "proj-b"], default_space.id)

    unread_a = svc.get_unread("proj-a", default_space.id)
    unread_b = svc.get_unread("proj-b", default_space.id)

    assert len(unread_a) == 1
    assert len(unread_b) == 1
    assert unread_a[0].recipient_project_id == "proj-a"
    assert unread_b[0].recipient_project_id == "proj-b"


# ---------------------------------------------------------------------------
# ack() tests
# ---------------------------------------------------------------------------


def test_ack_marks_notification_as_read(db_session, default_space):
    """ack() marks the notification as read and sets read_at (Req 5.3)."""
    svc = make_svc(db_session)
    notifications = svc.generate("doc/design", 1, ["proj-a"], default_space.id)
    n = notifications[0]

    svc.ack(n.id, "proj-a", default_space.id)

    assert n.status == "read"
    assert n.read_at is not None


def test_ack_nonexistent_update_id_raises_notification_not_found(db_session, default_space):
    """ack() raises NOTIFICATION_NOT_FOUND for unknown update_id (Req 5.4)."""
    svc = make_svc(db_session)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.ack("no-such-id", "proj-a", default_space.id)

    assert exc_info.value.error_code == "NOTIFICATION_NOT_FOUND"


def test_get_unread_after_ack_does_not_return_acked_notification(db_session, default_space):
    """get_unread() does not return acked notifications (Req 5.3)."""
    svc = make_svc(db_session)
    notifications = svc.generate("doc/task", 3, ["proj-a"], default_space.id)
    n = notifications[0]

    svc.ack(n.id, "proj-a", default_space.id)

    unread = svc.get_unread("proj-a", default_space.id)
    assert all(u.id != n.id for u in unread)
