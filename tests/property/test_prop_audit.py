# Feature: doc-exchange-center, Property 23: 写操作日志完整性
# Feature: doc-exchange-center, Property 24: 日志查询正确性
"""
Property-based tests for AuditLogService.

**Validates: Requirements 9.1, 9.2, 9.4**
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.models import Base, ProjectSpace
from doc_exchange.models.entities import AuditLog
from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_operation_types = st.sampled_from([
    "push_document",
    "register_subproject",
    "change_stage",
    "add_subscription",
    "remove_subscription",
    "claim_task",
    "complete_task",
    "ack_update",
])

valid_result = st.sampled_from(["success", "failure"])

valid_project_id = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"),
    min_size=1,
    max_size=32,
)

valid_target_id = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_/"),
    min_size=1,
    max_size=64,
)


# ---------------------------------------------------------------------------
# Helper: create isolated in-memory DB
# ---------------------------------------------------------------------------

def _make_session():
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
        name="prop-audit-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()

    return session, space.id, engine


# ---------------------------------------------------------------------------
# Property 23: 写操作日志完整性
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    operation_type=valid_operation_types,
    operator_project_id=valid_project_id,
    target_id=valid_target_id,
    result=valid_result,
)
def test_prop_audit_write_integrity(
    operation_type: str,
    operator_project_id: str,
    target_id: str,
    result: str,
):
    """
    Property 23: 写操作日志完整性

    For any write operation (document push, subscription change, task status change,
    subproject registration, stage change), after the operation completes, the audit
    log must contain a corresponding entry with: operation_type, UTC timestamp,
    operator project_id, target identifier, and operation result.

    **Validates: Requirements 9.1, 9.2**
    """
    # Feature: doc-exchange-center, Property 23: 写操作日志完整性
    session, space_id, engine = _make_session()
    try:
        svc = AuditLogService(db=session)
        before = datetime.now(timezone.utc)

        entry = svc.log(
            operation_type=operation_type,
            operator_project_id=operator_project_id,
            target_id=target_id,
            result=result,
            project_space_id=space_id,
        )

        after = datetime.now(timezone.utc)

        # The entry must have a non-empty id
        assert entry.id, "audit log entry must have a non-empty id"

        # operation_type must match
        assert entry.operation_type == operation_type, (
            f"operation_type mismatch: expected {operation_type!r}, got {entry.operation_type!r}"
        )

        # UTC timestamp must be within the test window
        operated_at = entry.operated_at
        if operated_at.tzinfo is None:
            operated_at = operated_at.replace(tzinfo=timezone.utc)
        assert before <= operated_at <= after, (
            f"operated_at {operated_at} not in [{before}, {after}]"
        )

        # operator_project_id must match
        assert entry.operator_project_id == operator_project_id, (
            f"operator_project_id mismatch: expected {operator_project_id!r}, got {entry.operator_project_id!r}"
        )

        # target_id must match
        assert entry.target_id == target_id, (
            f"target_id mismatch: expected {target_id!r}, got {entry.target_id!r}"
        )

        # result must match
        assert entry.result == result, (
            f"result mismatch: expected {result!r}, got {entry.result!r}"
        )

        # The entry must be retrievable via query()
        logs = svc.query(project_space_id=space_id)
        assert any(log.id == entry.id for log in logs), (
            "written audit log entry must be retrievable via query()"
        )

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 24: 日志查询正确性
# ---------------------------------------------------------------------------

# Strategy: generate a list of (operator_project_id, offset_seconds) pairs
# representing log entries at different times relative to a base time.
log_entry_strategy = st.lists(
    st.tuples(
        valid_project_id,                        # operator_project_id
        st.integers(min_value=0, max_value=3600), # offset in seconds from base
    ),
    min_size=1,
    max_size=20,
)


@settings(max_examples=100)
@given(
    entries=log_entry_strategy,
    query_operator=st.one_of(st.none(), valid_project_id),
    start_offset=st.integers(min_value=0, max_value=1800),
    end_offset=st.integers(min_value=1800, max_value=3600),
)
def test_prop_audit_query_correctness(
    entries,
    query_operator,
    start_offset: int,
    end_offset: int,
):
    """
    Property 24: 日志查询正确性

    For any combination of time range and operator_project_id, the query result
    must contain exactly the records that satisfy all specified conditions —
    no missing records and no extra records.

    **Validates: Requirements 9.4**
    """
    # Feature: doc-exchange-center, Property 24: 日志查询正确性
    session, space_id, engine = _make_session()
    try:
        base_time = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        start_time = base_time + timedelta(seconds=start_offset)
        end_time = base_time + timedelta(seconds=end_offset)

        # Insert log entries with controlled timestamps
        inserted = []
        for operator_id, offset in entries:
            operated_at = base_time + timedelta(seconds=offset)
            log = AuditLog(
                id=str(uuid.uuid4()),
                project_space_id=space_id,
                operation_type="push_document",
                operated_at=operated_at,
                operator_project_id=operator_id,
                target_id="doc/requirement",
                result="success",
            )
            session.add(log)
            inserted.append((log.id, operator_id, operated_at))

        session.flush()

        # Compute expected results manually
        expected_ids = set()
        for log_id, operator_id, operated_at in inserted:
            time_ok = start_time <= operated_at <= end_time
            operator_ok = (query_operator is None) or (operator_id == query_operator)
            if time_ok and operator_ok:
                expected_ids.add(log_id)

        # Run the query
        svc = AuditLogService(db=session)
        results = svc.query(
            project_space_id=space_id,
            operator_project_id=query_operator,
            start_time=start_time,
            end_time=end_time,
        )
        result_ids = {r.id for r in results}

        # No missing records
        missing = expected_ids - result_ids
        assert not missing, (
            f"Query missing {len(missing)} expected record(s): {missing}"
        )

        # No extra records
        extra = result_ids - expected_ids
        assert not extra, (
            f"Query returned {len(extra)} unexpected record(s): {extra}"
        )

    finally:
        session.close()
        engine.dispose()
