# Feature: doc-exchange-center, Property 29: Analyzer 降级
"""
Property-based tests for Analyzer graceful degradation.

**Validates: Requirements 13.4**
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.analyzer.analyzer_service import AnalyzerService
from doc_exchange.analyzer.base import AnalysisResult, Analyzer
from doc_exchange.analyzer.rule_engine import RuleEngineAnalyzer
from doc_exchange.models import Base, ProjectSpace
from doc_exchange.models.entities import Document, DocumentVersion, SubProject
from doc_exchange.services.audit_log_service import AuditLogService


# ---------------------------------------------------------------------------
# Failing Analyzer implementation
# ---------------------------------------------------------------------------


class AlwaysFailingAnalyzer(Analyzer):
    """An Analyzer that always raises an exception — used to test degradation."""

    def __init__(self, error_message: str = "simulated analyzer failure"):
        self._error_message = error_message

    async def analyze(self, doc, new_version, all_subprojects):
        raise RuntimeError(self._error_message)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_doc_type = st.sampled_from(["requirement", "design", "api", "config", "task"])
valid_subproject_type = st.sampled_from(["development", "testing", "ops", "design", "deployment"])
valid_error_message = st.text(min_size=1, max_size=200)

subproject_entries = st.lists(
    st.tuples(
        st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"),
            min_size=1,
            max_size=32,
        ),
        valid_subproject_type,
    ),
    min_size=0,
    max_size=5,
)


# ---------------------------------------------------------------------------
# Helper: build an isolated in-memory SQLite session
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
    return session, engine


# ---------------------------------------------------------------------------
# Property 29: Analyzer 降级
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_type=valid_doc_type,
    subprojects_data=subproject_entries,
    error_message=valid_error_message,
)
def test_prop_analyzer_degradation(doc_type: str, subprojects_data, error_message: str):
    """
    Property 29: Analyzer 降级

    For any configuration with a failing Analyzer implementation, when the
    Analyzer raises an exception, the system must:
    1. NOT raise an exception (graceful degradation)
    2. Return a valid AnalysisResult (from the rule engine fallback)
    3. Record the failure reason in the audit log

    **Validates: Requirements 13.4**
    """
    session, engine = _make_session()

    try:
        # Create a project space
        space = ProjectSpace(
            id=str(uuid.uuid4()),
            name="prop-analyzer-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(space)
        session.flush()

        # Create a document
        doc = Document(
            id=str(uuid.uuid4()),
            project_space_id=space.id,
            subproject_id=str(uuid.uuid4()),
            doc_type=doc_type,
            latest_version=1,
            created_at=datetime.now(timezone.utc),
        )
        session.add(doc)
        session.flush()

        # Create a document version
        new_version = DocumentVersion(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            project_space_id=space.id,
            version=1,
            content_hash="abc123",
            pushed_by="system",
            status="published",
            pushed_at=datetime.now(timezone.utc),
        )
        session.add(new_version)
        session.flush()

        # Create subprojects
        all_subprojects = []
        for name, sp_type in subprojects_data:
            sp = SubProject(
                id=str(uuid.uuid4()),
                project_space_id=space.id,
                name=name,
                type=sp_type,
                stage="development",
                stage_updated_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
            )
            session.add(sp)
            all_subprojects.append(sp)
        session.flush()

        # Wire up services
        audit_svc = AuditLogService(session)
        failing_analyzer = AlwaysFailingAnalyzer(error_message=error_message)
        fallback = RuleEngineAnalyzer()
        analyzer_service = AnalyzerService(
            analyzer=failing_analyzer,
            fallback=fallback,
            audit_log_service=audit_svc,
        )

        # --- Assertion 1: call does NOT raise an exception ---
        result = asyncio.get_event_loop().run_until_complete(
            analyzer_service.analyze(doc, new_version, all_subprojects)
        )

        # --- Assertion 2: result is a valid AnalysisResult ---
        assert isinstance(result, AnalysisResult), (
            f"Expected AnalysisResult, got {type(result)}"
        )
        assert result.doc_id == doc.id, (
            f"Expected doc_id {doc.id!r}, got {result.doc_id!r}"
        )
        assert result.version == new_version.version, (
            f"Expected version {new_version.version}, got {result.version}"
        )
        assert isinstance(result.affected_projects, list), (
            "affected_projects must be a list"
        )

        # --- Assertion 3: audit log contains a failure entry ---
        logs = audit_svc.query(project_space_id=space.id)
        failure_logs = [
            log for log in logs
            if log.operation_type == "analyzer_failure" and log.result == "failure"
        ]
        assert len(failure_logs) >= 1, (
            "Expected at least one analyzer_failure audit log entry"
        )
        # The failure detail must mention the error
        failure_log = failure_logs[0]
        assert failure_log.detail is not None, "Failure log detail must not be None"
        assert "Primary analyzer failed" in failure_log.detail, (
            f"Expected failure detail to mention 'Primary analyzer failed', got: {failure_log.detail!r}"
        )

    finally:
        session.close()
        engine.dispose()
