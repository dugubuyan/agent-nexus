# Feature: doc-exchange-center, Property 14: 通知生成与查询
# Feature: doc-exchange-center, Property 15: ack 后通知消失
# Feature: doc-exchange-center, Property 16: 通知幂等性
"""
Property-based tests for notification generation, querying, and idempotency.

**Validates: Requirements 5.1, 5.2, 5.3, 5.5**
"""

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.models import Base, ProjectSpace
from doc_exchange.models.entities import SubProject
from doc_exchange.services.notification_service import NotificationService
from doc_exchange.services.project_service import ProjectService
from doc_exchange.services.subscription_service import SubscriptionService


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_doc_type = st.sampled_from(["api", "requirement", "design", "config", "task"])

valid_version = st.integers(min_value=1, max_value=9999)

valid_subproject_type = st.sampled_from(["development", "testing", "ops", "design", "deployment"])


# ---------------------------------------------------------------------------
# Helper: create an isolated in-memory DB
# ---------------------------------------------------------------------------


def _make_db():
    """Return (session, engine, space_id) using a fresh in-memory SQLite DB."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="prop-test-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()

    return session, engine, space.id


def _register_subproject(session, space_id: str, proj_type: str = "testing") -> str:
    """Register a subproject and return its id."""
    svc = ProjectService(session)
    subproject = svc.register(
        name=f"sub-{uuid.uuid4().hex[:8]}",
        type=proj_type,
        project_space_id=space_id,
    )
    return subproject.id


# ---------------------------------------------------------------------------
# Property 14: 通知生成与查询
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_type=valid_doc_type,
    version=valid_version,
    num_subscribers=st.integers(min_value=1, max_value=5),
    subscribe_by_doc_id=st.booleans(),
)
def test_prop_notification_generation_and_query(
    doc_type: str,
    version: int,
    num_subscribers: int,
    subscribe_by_doc_id: bool,
):
    """
    Property 14: 通知生成与查询

    For any document push, all sub-projects that subscribed (by doc_id or doc_type)
    should see the corresponding unread notification via get_unread after a
    successful push. The notification must contain the correct doc_id, version,
    and be in unread status.

    # Feature: doc-exchange-center, Property 14: 对于任意文档推送，所有订阅了该文档（按 doc_id 或 doc_type）
    # 的子项目，在推送成功后调用 get_my_updates 应能看到对应的未读通知，通知中包含正确的 doc_id、版本号。

    **Validates: Requirements 5.1, 5.2**
    """
    session, engine, space_id = _make_db()
    try:
        sub_svc = SubscriptionService(session)
        notif_svc = NotificationService(session)

        # Register subscribers and set up subscriptions
        subscriber_ids = []
        doc_id = f"proj-abc/{doc_type}"

        for _ in range(num_subscribers):
            sub_id = _register_subproject(session, space_id)
            subscriber_ids.append(sub_id)

            if subscribe_by_doc_id:
                sub_svc.add_rule(
                    subscriber_project_id=sub_id,
                    project_space_id=space_id,
                    target_doc_id=doc_id,
                )
            else:
                sub_svc.add_rule(
                    subscriber_project_id=sub_id,
                    project_space_id=space_id,
                    target_doc_type=doc_type,
                )

        # Resolve subscribers and generate notifications
        if subscribe_by_doc_id:
            resolved = sub_svc.get_subscribers(space_id, doc_id=doc_id)
        else:
            resolved = sub_svc.get_subscribers(space_id, doc_type=doc_type)

        notif_svc.generate(
            doc_id=doc_id,
            version=version,
            subscriber_ids=resolved,
            project_space_id=space_id,
        )

        # Each subscriber must see the notification in get_unread
        for sub_id in subscriber_ids:
            unread = notif_svc.get_unread(sub_id, space_id)
            assert len(unread) >= 1, (
                f"Subscriber {sub_id!r} should have at least 1 unread notification, got 0"
            )

            matching = [n for n in unread if n.document_id == doc_id and n.version == version]
            assert len(matching) == 1, (
                f"Subscriber {sub_id!r} should have exactly 1 notification for "
                f"doc_id={doc_id!r} version={version}, got {len(matching)}"
            )

            notif = matching[0]
            assert notif.status == "unread", (
                f"Notification status should be 'unread', got {notif.status!r}"
            )
            assert notif.document_id == doc_id, (
                f"Notification doc_id mismatch: expected {doc_id!r}, got {notif.document_id!r}"
            )
            assert notif.version == version, (
                f"Notification version mismatch: expected {version}, got {notif.version}"
            )
            assert notif.created_at is not None, "Notification must have a created_at timestamp"
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 15: ack 后通知消失
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_type=valid_doc_type,
    version=valid_version,
)
def test_prop_ack_removes_notification_from_unread(doc_type: str, version: int):
    """
    Property 15: ack 后通知消失

    For any unread notification, after calling ack_update the notification
    must no longer appear in get_unread. The ack operation is idempotent:
    calling ack on an already-read notification must not raise an error.

    # Feature: doc-exchange-center, Property 15: 对于任意未读通知，调用 ack_update 后，
    # 再次调用 get_my_updates 应不再返回该通知；ack 操作是幂等的（对已读通知再次 ack 不报错）。

    **Validates: Requirements 5.3**
    """
    session, engine, space_id = _make_db()
    try:
        sub_svc = SubscriptionService(session)
        notif_svc = NotificationService(session)

        # Register a subscriber
        sub_id = _register_subproject(session, space_id)
        doc_id = f"proj-xyz/{doc_type}"

        sub_svc.add_rule(
            subscriber_project_id=sub_id,
            project_space_id=space_id,
            target_doc_type=doc_type,
        )

        # Generate a notification
        notif_svc.generate(
            doc_id=doc_id,
            version=version,
            subscriber_ids=[sub_id],
            project_space_id=space_id,
        )

        # Verify it's unread
        unread_before = notif_svc.get_unread(sub_id, space_id)
        assert len(unread_before) == 1, (
            f"Expected 1 unread notification before ack, got {len(unread_before)}"
        )
        notif_id = unread_before[0].id

        # Ack the notification
        notif_svc.ack(notif_id, sub_id, space_id)

        # Must no longer appear in get_unread
        unread_after = notif_svc.get_unread(sub_id, space_id)
        assert len(unread_after) == 0, (
            f"After ack, expected 0 unread notifications, got {len(unread_after)}"
        )

        # Idempotency: ack again must not raise an error
        # (notification exists but is already read — ack should succeed silently)
        notif_svc.ack(notif_id, sub_id, space_id)

        # Still no unread notifications
        unread_after_second_ack = notif_svc.get_unread(sub_id, space_id)
        assert len(unread_after_second_ack) == 0, (
            f"After second ack, expected 0 unread notifications, got {len(unread_after_second_ack)}"
        )
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 16: 通知幂等性
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_type=valid_doc_type,
    version=valid_version,
    repeat_count=st.integers(min_value=2, max_value=10),
)
def test_prop_notification_idempotency(doc_type: str, version: int, repeat_count: int):
    """
    Property 16: 通知幂等性

    For a single version change of any document, the same subscriber must see
    at most one notification in get_unread, regardless of how many times
    generate() is called internally.

    # Feature: doc-exchange-center, Property 16: 对于任意文档的单次版本变更，同一订阅方在
    # get_my_updates 中最多只能看到一条对应的通知记录，无论触发了多少次内部处理。

    **Validates: Requirements 5.5**
    """
    session, engine, space_id = _make_db()
    try:
        sub_svc = SubscriptionService(session)
        notif_svc = NotificationService(session)

        # Register a subscriber
        sub_id = _register_subproject(session, space_id)
        doc_id = f"proj-idem/{doc_type}"

        sub_svc.add_rule(
            subscriber_project_id=sub_id,
            project_space_id=space_id,
            target_doc_type=doc_type,
        )

        # Call generate() multiple times for the same (doc_id, version, subscriber)
        for _ in range(repeat_count):
            notif_svc.generate(
                doc_id=doc_id,
                version=version,
                subscriber_ids=[sub_id],
                project_space_id=space_id,
            )

        # Must have exactly one unread notification for this (doc_id, version)
        unread = notif_svc.get_unread(sub_id, space_id)
        matching = [n for n in unread if n.document_id == doc_id and n.version == version]

        assert len(matching) == 1, (
            f"Expected exactly 1 notification for doc_id={doc_id!r} version={version} "
            f"after {repeat_count} generate() calls, got {len(matching)}"
        )
    finally:
        session.close()
        engine.dispose()
