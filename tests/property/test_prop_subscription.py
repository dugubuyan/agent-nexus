# Feature: doc-exchange-center, Property 12: 订阅规则 Round-Trip
# Feature: doc-exchange-center, Property 13: 订阅推断正确性
"""
Property-based tests for subscription rules.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4**
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
from doc_exchange.services.project_service import ProjectService
from doc_exchange.services.subscription_service import (
    INITIAL_SUBSCRIPTION_MAP,
    SubscriptionService,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_doc_type = st.sampled_from(["api", "requirement", "design", "config", "task"])

valid_subproject_type = st.sampled_from(["development", "testing", "ops"])


# ---------------------------------------------------------------------------
# Helper: create an isolated in-memory DB with a space and a subproject
# ---------------------------------------------------------------------------


def _make_db_with_subproject(proj_type: str):
    """Return (session, space_id, subproject_id) using a fresh in-memory SQLite DB."""
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

    proj_svc = ProjectService(session)
    subproject = proj_svc.register(
        name="test-subproject",
        type=proj_type,
        project_space_id=space.id,
    )

    return session, engine, space.id, subproject.id


# ---------------------------------------------------------------------------
# Property 12: 订阅规则 Round-Trip
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(doc_type=valid_doc_type)
def test_prop_subscription_rule_round_trip(doc_type: str):
    """
    Property 12: 订阅规则 Round-Trip

    For any valid subscription rule, after add_rule() get_subscribers() must
    include the subscriber; after remove_rule() get_subscribers() must no
    longer include the subscriber.

    **Validates: Requirements 4.1, 4.2, 4.3**
    """
    # Feature: doc-exchange-center, Property 12: 对于任意合法的订阅规则，添加后查询订阅列表应包含该规则；
    # 删除该规则后，查询订阅列表应不再包含该规则。
    session, engine, space_id, subproject_id = _make_db_with_subproject("testing")
    try:
        svc = SubscriptionService(session)

        # 1. Add rule and verify subscriber appears in get_subscribers (Req 4.1, 4.2)
        rule = svc.add_rule(
            subscriber_project_id=subproject_id,
            project_space_id=space_id,
            target_doc_type=doc_type,
        )
        assert rule.id, "add_rule must return a rule with a non-empty id"

        subscribers_after_add = svc.get_subscribers(space_id, doc_type=doc_type)
        assert subproject_id in subscribers_after_add, (
            f"After add_rule, subscriber {subproject_id!r} must appear in get_subscribers "
            f"for doc_type={doc_type!r}; got {subscribers_after_add!r}"
        )

        # 2. Remove rule and verify subscriber no longer appears (Req 4.3)
        svc.remove_rule(rule.id, space_id)

        subscribers_after_remove = svc.get_subscribers(space_id, doc_type=doc_type)
        assert subproject_id not in subscribers_after_remove, (
            f"After remove_rule, subscriber {subproject_id!r} must NOT appear in get_subscribers "
            f"for doc_type={doc_type!r}; got {subscribers_after_remove!r}"
        )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(doc_type=valid_doc_type)
def test_prop_subscription_rule_round_trip_by_doc_id(doc_type: str):
    """
    Property 12 (doc_id variant): After add_rule by doc_id, get_subscribers includes
    the subscriber; after remove_rule, it no longer does.

    **Validates: Requirements 4.1, 4.2, 4.3**
    """
    # Feature: doc-exchange-center, Property 12: doc_id 订阅 Round-Trip
    session, engine, space_id, subproject_id = _make_db_with_subproject("development")
    try:
        svc = SubscriptionService(session)
        doc_id = f"some-project/{doc_type}"

        rule = svc.add_rule(
            subscriber_project_id=subproject_id,
            project_space_id=space_id,
            target_doc_id=doc_id,
        )

        subscribers_after_add = svc.get_subscribers(space_id, doc_id=doc_id)
        assert subproject_id in subscribers_after_add, (
            f"After add_rule by doc_id, subscriber must appear in get_subscribers; "
            f"got {subscribers_after_add!r}"
        )

        svc.remove_rule(rule.id, space_id)

        subscribers_after_remove = svc.get_subscribers(space_id, doc_id=doc_id)
        assert subproject_id not in subscribers_after_remove, (
            f"After remove_rule, subscriber must NOT appear in get_subscribers; "
            f"got {subscribers_after_remove!r}"
        )
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 13: 订阅推断正确性
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(proj_type=valid_subproject_type)
def test_prop_infer_initial_subscriptions_matches_mapping(proj_type: str):
    """
    Property 13: 订阅推断正确性

    For each known subproject type, infer_initial_subscriptions() must return
    exactly the doc_types defined in INITIAL_SUBSCRIPTION_MAP.

    **Validates: Requirements 4.4**
    """
    # Feature: doc-exchange-center, Property 13: 对于任意子项目类型，注册后系统推断的初始订阅规则
    # 应符合预定义的类型映射表。
    session, engine, space_id, subproject_id = _make_db_with_subproject(proj_type)
    try:
        svc = SubscriptionService(session)

        rules = svc.infer_initial_subscriptions(
            subproject_id=subproject_id,
            subproject_type=proj_type,
            project_space_id=space_id,
        )

        expected_doc_types = set(INITIAL_SUBSCRIPTION_MAP[proj_type])
        actual_doc_types = {r.target_doc_type for r in rules}

        assert actual_doc_types == expected_doc_types, (
            f"For subproject type {proj_type!r}, expected doc_types {expected_doc_types!r}, "
            f"got {actual_doc_types!r}"
        )
        assert len(rules) == len(expected_doc_types), (
            f"Expected {len(expected_doc_types)} rules for type {proj_type!r}, got {len(rules)}"
        )

        # Each rule must be persisted (visible via list_rules)
        persisted = svc.list_rules(subproject_id, space_id)
        persisted_ids = {r.id for r in persisted}
        for rule in rules:
            assert rule.id in persisted_ids, (
                f"Inferred rule {rule.id!r} must be persisted and visible via list_rules"
            )
    finally:
        session.close()
        engine.dispose()
