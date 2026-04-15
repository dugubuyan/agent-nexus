"""
Unit tests for AuditLogService.

Covers:
- log() creates a record with correct fields
- query() by operator_project_id
- query() by time range
- query() with combined filters
- No modification methods exist
"""

from datetime import datetime, timedelta, timezone

import pytest

from doc_exchange.services.audit_log_service import AuditLogService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(db_session):
    return AuditLogService(db=db_session)


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# log() — field correctness
# ---------------------------------------------------------------------------


def test_log_creates_record_with_correct_fields(db_session, default_space):
    svc = _make_service(db_session)

    before = _utcnow()
    entry = svc.log(
        operation_type="push_document",
        operator_project_id="proj-a",
        target_id="proj-a/requirement",
        result="success",
        project_space_id=default_space.id,
    )
    after = _utcnow()

    assert entry.id is not None
    assert entry.operation_type == "push_document"
    assert entry.operator_project_id == "proj-a"
    assert entry.target_id == "proj-a/requirement"
    assert entry.result == "success"
    assert entry.project_space_id == default_space.id
    assert entry.detail is None
    # operated_at must be a UTC timestamp within the test window
    assert before <= entry.operated_at.replace(tzinfo=timezone.utc) <= after


def test_log_stores_detail_on_failure(db_session, default_space):
    svc = _make_service(db_session)

    entry = svc.log(
        operation_type="push_document",
        operator_project_id="proj-b",
        target_id="proj-b/design",
        result="failure",
        project_space_id=default_space.id,
        detail="Content unchanged",
    )

    assert entry.result == "failure"
    assert entry.detail == "Content unchanged"


def test_log_returns_persisted_entry(db_session, default_space):
    """The returned entry should be queryable from the session."""
    svc = _make_service(db_session)
    entry = svc.log(
        operation_type="register_subproject",
        operator_project_id="admin",
        target_id="new-subproject-id",
        result="success",
        project_space_id=default_space.id,
    )

    results = svc.query(project_space_id=default_space.id)
    assert any(r.id == entry.id for r in results)


# ---------------------------------------------------------------------------
# query() — by operator_project_id
# ---------------------------------------------------------------------------


def test_query_by_operator_returns_only_matching(db_session, default_space):
    svc = _make_service(db_session)

    svc.log("push_document", "proj-a", "doc-1", "success", default_space.id)
    svc.log("push_document", "proj-b", "doc-2", "success", default_space.id)
    svc.log("ack_update", "proj-a", "notif-1", "success", default_space.id)

    results = svc.query(project_space_id=default_space.id, operator_project_id="proj-a")

    assert len(results) == 2
    assert all(r.operator_project_id == "proj-a" for r in results)


def test_query_by_operator_returns_empty_when_no_match(db_session, default_space):
    svc = _make_service(db_session)
    svc.log("push_document", "proj-a", "doc-1", "success", default_space.id)

    results = svc.query(project_space_id=default_space.id, operator_project_id="nobody")
    assert results == []


# ---------------------------------------------------------------------------
# query() — by time range
# ---------------------------------------------------------------------------


def test_query_by_start_time_excludes_earlier_records(db_session, default_space):
    svc = _make_service(db_session)

    # Manually create entries with controlled timestamps
    from doc_exchange.models.entities import AuditLog
    import uuid

    t1 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

    for t, op in [(t1, "op-early"), (t2, "op-mid"), (t3, "op-late")]:
        db_session.add(AuditLog(
            id=str(uuid.uuid4()),
            project_space_id=default_space.id,
            operation_type="push_document",
            operated_at=t,
            operator_project_id="proj-x",
            target_id="doc-x",
            result="success",
        ))
    db_session.flush()

    results = svc.query(
        project_space_id=default_space.id,
        start_time=datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
    )

    operated_ats = [r.operated_at for r in results]
    assert all(t >= datetime(2024, 1, 1, 11, 0, 0) for t in operated_ats)
    assert len(results) == 2  # t2 and t3


def test_query_by_end_time_excludes_later_records(db_session, default_space):
    svc = _make_service(db_session)

    from doc_exchange.models.entities import AuditLog
    import uuid

    t1 = datetime(2024, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2024, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2024, 2, 1, 12, 0, 0, tzinfo=timezone.utc)

    for t in [t1, t2, t3]:
        db_session.add(AuditLog(
            id=str(uuid.uuid4()),
            project_space_id=default_space.id,
            operation_type="ack_update",
            operated_at=t,
            operator_project_id="proj-y",
            target_id="notif-y",
            result="success",
        ))
    db_session.flush()

    results = svc.query(
        project_space_id=default_space.id,
        end_time=datetime(2024, 2, 1, 11, 0, 0, tzinfo=timezone.utc),
    )

    assert len(results) == 2  # t1 and t2


def test_query_by_time_range_combined(db_session, default_space):
    svc = _make_service(db_session)

    from doc_exchange.models.entities import AuditLog
    import uuid

    times = [
        datetime(2024, 3, 1, 6, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 3, 1, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 3, 1, 15, 0, 0, tzinfo=timezone.utc),
    ]
    for t in times:
        db_session.add(AuditLog(
            id=str(uuid.uuid4()),
            project_space_id=default_space.id,
            operation_type="claim_task",
            operated_at=t,
            operator_project_id="proj-z",
            target_id="task-z",
            result="success",
        ))
    db_session.flush()

    results = svc.query(
        project_space_id=default_space.id,
        start_time=datetime(2024, 3, 1, 8, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 3, 1, 13, 0, 0, tzinfo=timezone.utc),
    )

    assert len(results) == 2  # 09:00 and 12:00


# ---------------------------------------------------------------------------
# query() — combined operator + time range
# ---------------------------------------------------------------------------


def test_query_combined_operator_and_time_range(db_session, default_space):
    svc = _make_service(db_session)

    from doc_exchange.models.entities import AuditLog
    import uuid

    t_early = datetime(2024, 4, 1, 8, 0, 0, tzinfo=timezone.utc)
    t_late = datetime(2024, 4, 1, 16, 0, 0, tzinfo=timezone.utc)
    t_mid = datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc)

    # proj-a at t_early (outside range)
    db_session.add(AuditLog(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        operation_type="push_document",
        operated_at=t_early,
        operator_project_id="proj-a",
        target_id="doc-1",
        result="success",
    ))
    # proj-a at t_mid (inside range)
    db_session.add(AuditLog(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        operation_type="push_document",
        operated_at=t_mid,
        operator_project_id="proj-a",
        target_id="doc-2",
        result="success",
    ))
    # proj-b at t_mid (inside range but wrong operator)
    db_session.add(AuditLog(
        id=str(uuid.uuid4()),
        project_space_id=default_space.id,
        operation_type="push_document",
        operated_at=t_mid,
        operator_project_id="proj-b",
        target_id="doc-3",
        result="success",
    ))
    db_session.flush()

    results = svc.query(
        project_space_id=default_space.id,
        operator_project_id="proj-a",
        start_time=datetime(2024, 4, 1, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 4, 1, 14, 0, 0, tzinfo=timezone.utc),
    )

    assert len(results) == 1
    assert results[0].operator_project_id == "proj-a"
    assert results[0].target_id == "doc-2"


# ---------------------------------------------------------------------------
# No modification methods
# ---------------------------------------------------------------------------


def test_no_update_method_exists():
    assert not hasattr(AuditLogService, "update")


def test_no_delete_method_exists():
    assert not hasattr(AuditLogService, "delete")


def test_no_modify_method_exists():
    """Ensure no method names suggest mutation of existing records."""
    mutation_names = {"update", "delete", "remove", "edit", "patch", "modify"}
    public_methods = {
        name for name in dir(AuditLogService)
        if not name.startswith("_") and callable(getattr(AuditLogService, name))
    }
    assert public_methods.isdisjoint(mutation_names)
