"""
Unit tests for SubscriptionService (Requirements 4.1 – 4.5).
"""

import pytest
from sqlalchemy.orm import Session

from doc_exchange.services import DocExchangeError, ProjectService, SubscriptionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_svc(db_session: Session) -> SubscriptionService:
    return SubscriptionService(db_session)


def register_project(db_session: Session, space_id: str, name: str = "proj", ptype: str = "testing"):
    """Helper to register a sub-project and return it."""
    return ProjectService(db_session).register(name=name, type=ptype, project_space_id=space_id)


# ---------------------------------------------------------------------------
# add_rule tests
# ---------------------------------------------------------------------------


def test_add_rule_by_doc_type_succeeds(db_session, default_space):
    """add_rule with target_doc_type creates a subscription (Req 4.1, 4.2)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id)

    rule = svc.add_rule(
        subscriber_project_id=proj.id,
        project_space_id=default_space.id,
        target_doc_type="api",
    )

    assert rule.id
    assert rule.subscriber_project_id == proj.id
    assert rule.target_doc_type == "api"
    assert rule.target_doc_id is None


def test_add_rule_by_doc_id_succeeds(db_session, default_space):
    """add_rule with target_doc_id creates a subscription (Req 4.1, 4.2)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id)

    rule = svc.add_rule(
        subscriber_project_id=proj.id,
        project_space_id=default_space.id,
        target_doc_id="proj-a/requirement",
    )

    assert rule.id
    assert rule.target_doc_id == "proj-a/requirement"
    assert rule.target_doc_type is None


def test_add_rule_nonexistent_project_raises_project_not_found(db_session, default_space):
    """add_rule for non-existent project_id raises PROJECT_NOT_FOUND (Req 4.5)."""
    svc = make_svc(db_session)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.add_rule(
            subscriber_project_id="does-not-exist",
            project_space_id=default_space.id,
            target_doc_type="api",
        )

    assert exc_info.value.error_code == "PROJECT_NOT_FOUND"


def test_add_rule_without_target_raises_missing_field(db_session, default_space):
    """add_rule with neither target_doc_id nor target_doc_type raises MISSING_REQUIRED_FIELD."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.add_rule(
            subscriber_project_id=proj.id,
            project_space_id=default_space.id,
        )

    assert exc_info.value.error_code == "MISSING_REQUIRED_FIELD"


# ---------------------------------------------------------------------------
# remove_rule tests
# ---------------------------------------------------------------------------


def test_remove_rule_removes_the_rule(db_session, default_space):
    """remove_rule deletes the subscription rule (Req 4.3)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id)
    rule = svc.add_rule(
        subscriber_project_id=proj.id,
        project_space_id=default_space.id,
        target_doc_type="design",
    )

    svc.remove_rule(rule.id, default_space.id)

    remaining = svc.list_rules(proj.id, default_space.id)
    assert all(r.id != rule.id for r in remaining)


def test_remove_rule_nonexistent_does_not_raise(db_session, default_space):
    """remove_rule on a non-existent rule_id does not raise (Req 4.3)."""
    svc = make_svc(db_session)
    # Should not raise
    svc.remove_rule("no-such-rule", default_space.id)


# ---------------------------------------------------------------------------
# get_subscribers tests
# ---------------------------------------------------------------------------


def test_get_subscribers_by_doc_type(db_session, default_space):
    """get_subscribers returns projects subscribed to a doc_type (Req 4.1)."""
    svc = make_svc(db_session)
    proj_a = register_project(db_session, default_space.id, name="a")
    proj_b = register_project(db_session, default_space.id, name="b")

    svc.add_rule(proj_a.id, default_space.id, target_doc_type="api")
    svc.add_rule(proj_b.id, default_space.id, target_doc_type="requirement")

    subscribers = svc.get_subscribers(default_space.id, doc_type="api")
    assert proj_a.id in subscribers
    assert proj_b.id not in subscribers


def test_get_subscribers_by_doc_id(db_session, default_space):
    """get_subscribers returns projects subscribed to an exact doc_id (Req 4.1)."""
    svc = make_svc(db_session)
    proj_a = register_project(db_session, default_space.id, name="a")
    proj_b = register_project(db_session, default_space.id, name="b")

    svc.add_rule(proj_a.id, default_space.id, target_doc_id="svc-x/api")
    svc.add_rule(proj_b.id, default_space.id, target_doc_id="svc-y/api")

    subscribers = svc.get_subscribers(default_space.id, doc_id="svc-x/api")
    assert proj_a.id in subscribers
    assert proj_b.id not in subscribers


def test_get_subscribers_returns_union_of_doc_id_and_doc_type(db_session, default_space):
    """get_subscribers returns union of doc_id and doc_type matches (Req 4.1)."""
    svc = make_svc(db_session)
    proj_a = register_project(db_session, default_space.id, name="a")
    proj_b = register_project(db_session, default_space.id, name="b")

    # proj_a subscribes by exact doc_id
    svc.add_rule(proj_a.id, default_space.id, target_doc_id="svc-x/api")
    # proj_b subscribes by doc_type
    svc.add_rule(proj_b.id, default_space.id, target_doc_type="api")

    subscribers = svc.get_subscribers(default_space.id, doc_id="svc-x/api", doc_type="api")
    assert proj_a.id in subscribers
    assert proj_b.id in subscribers


def test_get_subscribers_deduplicates(db_session, default_space):
    """get_subscribers deduplicates when a project matches both doc_id and doc_type."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id, name="a")

    svc.add_rule(proj.id, default_space.id, target_doc_id="svc-x/api")
    svc.add_rule(proj.id, default_space.id, target_doc_type="api")

    subscribers = svc.get_subscribers(default_space.id, doc_id="svc-x/api", doc_type="api")
    assert subscribers.count(proj.id) == 1


# ---------------------------------------------------------------------------
# list_rules tests
# ---------------------------------------------------------------------------


def test_list_rules_returns_all_rules_for_subscriber(db_session, default_space):
    """list_rules returns all subscription rules for a subscriber (Req 4.1)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id)

    svc.add_rule(proj.id, default_space.id, target_doc_type="api")
    svc.add_rule(proj.id, default_space.id, target_doc_type="requirement")
    svc.add_rule(proj.id, default_space.id, target_doc_id="svc-z/design")

    rules = svc.list_rules(proj.id, default_space.id)
    assert len(rules) == 3


def test_list_rules_empty_when_no_rules(db_session, default_space):
    """list_rules returns empty list when no rules exist for subscriber."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id)

    assert svc.list_rules(proj.id, default_space.id) == []


# ---------------------------------------------------------------------------
# infer_initial_subscriptions tests
# ---------------------------------------------------------------------------


def test_infer_initial_subscriptions_testing_type(db_session, default_space):
    """testing type creates api + requirement subscriptions (Req 4.4)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id, ptype="testing")

    rules = svc.infer_initial_subscriptions(proj.id, "testing", default_space.id)

    doc_types = {r.target_doc_type for r in rules}
    assert doc_types == {"api", "requirement"}
    assert len(rules) == 2


def test_infer_initial_subscriptions_development_type(db_session, default_space):
    """development type creates requirement + design subscriptions (Req 4.4)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id, ptype="development")

    rules = svc.infer_initial_subscriptions(proj.id, "development", default_space.id)

    doc_types = {r.target_doc_type for r in rules}
    assert doc_types == {"requirement", "design"}
    assert len(rules) == 2


def test_infer_initial_subscriptions_ops_type(db_session, default_space):
    """ops type creates config + design subscriptions (Req 4.4)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id, ptype="ops")

    rules = svc.infer_initial_subscriptions(proj.id, "ops", default_space.id)

    doc_types = {r.target_doc_type for r in rules}
    assert doc_types == {"config", "design"}
    assert len(rules) == 2


def test_infer_initial_subscriptions_unknown_type_returns_empty(db_session, default_space):
    """Unknown sub-project type returns empty list (no mapping defined)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id, ptype="custom")

    rules = svc.infer_initial_subscriptions(proj.id, "custom", default_space.id)

    assert rules == []


def test_infer_initial_subscriptions_rules_are_persisted(db_session, default_space):
    """Inferred rules are persisted and visible via list_rules (Req 4.4)."""
    svc = make_svc(db_session)
    proj = register_project(db_session, default_space.id, ptype="testing")

    svc.infer_initial_subscriptions(proj.id, "testing", default_space.id)

    persisted = svc.list_rules(proj.id, default_space.id)
    assert len(persisted) == 2
