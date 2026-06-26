# Feature: agent-nexus, Property 25: Project_Space 数据隔离
# Feature: agent-nexus, Property 26: 归档状态拒绝写入
"""
Property-based tests for Project_Space multi-tenancy isolation and archive enforcement.

**Validates: Requirements 10.1, 10.2, 10.3, 10.5, 10.6, 10.7**
"""

import tempfile
import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agent_nexus.mcp.dependencies import ServiceContainer
from agent_nexus.mcp.tools import ToolHandler
from agent_nexus.models import Base, ProjectSpace
from agent_nexus.services.document_service import DocumentService
from agent_nexus.services.audit_log_service import AuditLogService
from agent_nexus.services.notification_service import NotificationService
from agent_nexus.search.fts import ensure_fts_table
from agent_nexus.services.project_service import ProjectService
from agent_nexus.services.schemas import PushRequest
from agent_nexus.services.subscription_service import SubscriptionService
from agent_nexus.services.task_service import TaskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db():
    """Return (session, engine) using a fresh in-memory SQLite DB."""
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
    ensure_fts_table(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    return session, engine


def _create_space(session, name: str, status: str = "active") -> ProjectSpace:
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name=name,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()
    return space


def _register_subproject(session, space_id: str, proj_type: str = "development") -> str:
    svc = ProjectService(session)
    sub = svc.register(
        name=f"sub-{uuid.uuid4().hex[:8]}",
        type=proj_type,
        project_space_id=space_id,
    )
    return sub.id


def _make_doc_service(session, docs_root: str) -> DocumentService:
    audit = AuditLogService(session)
    sub_svc = SubscriptionService(session)
    notif_svc = NotificationService(session)
    task_svc = TaskService(session)
    return DocumentService(
        db=session,
        docs_root=docs_root,
        audit_log_service=audit,
        subscription_service=sub_svc,
        notification_service=notif_svc,
        task_service=task_svc,
    )


# ---------------------------------------------------------------------------
# Property 25: Project_Space 数据隔离
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    num_subprojects=st.integers(min_value=1, max_value=3),
    num_docs=st.integers(min_value=1, max_value=3),
)
def test_prop_space_data_isolation_subprojects(num_subprojects: int, num_docs: int):
    """
    Property 25: Project_Space 数据隔离 — subprojects

    For any two different Project_Spaces, subprojects registered in Space A
    must not appear in Space B's queries (should return empty, not error).

    **Validates: Requirements 10.1, 10.2, 10.5**
    """
    session, engine = _make_db()
    try:
        space_a = _create_space(session, "space-a")
        space_b = _create_space(session, "space-b")

        proj_svc = ProjectService(session)

        # Register subprojects in Space A
        for _ in range(num_subprojects):
            proj_svc.register(
                name=f"sub-{uuid.uuid4().hex[:8]}",
                type="development",
                project_space_id=space_a.id,
            )

        # Space B must return empty list — not an error
        subs_b = proj_svc.list_subprojects(space_b.id)
        assert subs_b == [], (
            f"Space B should have no subprojects, got {len(subs_b)}"
        )

        # Space A must have exactly num_subprojects
        subs_a = proj_svc.list_subprojects(space_a.id)
        assert len(subs_a) == num_subprojects, (
            f"Space A should have {num_subprojects} subprojects, got {len(subs_a)}"
        )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(num_docs=st.integers(min_value=1, max_value=3))
def test_prop_space_data_isolation_documents(num_docs: int):
    """
    Property 25: Project_Space 数据隔离 — documents

    Documents pushed to Space A must not be visible in Space B queries
    (should return DOC_NOT_FOUND, not a cross-space leak).

    **Validates: Requirements 10.1, 10.3, 10.5**
    """
    session, engine = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            space_a = _create_space(session, "space-a")
            space_b = _create_space(session, "space-b")

            # Register a subproject in Space A
            sub_a_id = _register_subproject(session, space_a.id)

            doc_svc = _make_doc_service(session, docs_root)

            pushed_doc_ids = []
            for i in range(num_docs):
                doc_id = f"{sub_a_id}/requirement"
                if i == 0:
                    req = PushRequest(
                        doc_id=doc_id,
                        content=f"# Doc {i}\nContent {uuid.uuid4().hex}",
                        actor=sub_a_id,
                        project_space_id=space_a.id,
                    )
                    doc_svc.push(req)
                    pushed_doc_ids.append(doc_id)

            # Querying the same doc_id in Space B must raise DOC_NOT_FOUND (not return Space A data)
            from agent_nexus.services.errors import AgentNexusError
            for doc_id in pushed_doc_ids:
                try:
                    result = doc_svc.get(doc_id=doc_id, project_space_id=space_b.id)
                    # If no exception, the data leaked across spaces — fail the test
                    pytest.fail(
                        f"Document {doc_id!r} from Space A was visible in Space B: {result}"
                    )
                except AgentNexusError as e:
                    assert e.error_code == "DOC_NOT_FOUND", (
                        f"Expected DOC_NOT_FOUND for cross-space query, got {e.error_code}"
                    )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(num_subs=st.integers(min_value=1, max_value=3))
def test_prop_space_data_isolation_subscriptions(num_subs: int):
    """
    Property 25: Project_Space 数据隔离 — subscriptions

    Subscriptions created in Space A must not appear in Space B queries.

    **Validates: Requirements 10.1, 10.5**
    """
    session, engine = _make_db()
    try:
        space_a = _create_space(session, "space-a")
        space_b = _create_space(session, "space-b")

        sub_svc = SubscriptionService(session)

        # Register a subproject in Space A and add subscriptions
        sub_a_id = _register_subproject(session, space_a.id)
        for _ in range(num_subs):
            sub_svc.add_rule(
                subscriber_project_id=sub_a_id,
                project_space_id=space_a.id,
                target_doc_type="requirement",
            )

        # Space B must return empty subscribers list
        subscribers_b = sub_svc.get_subscribers(space_b.id, doc_type="requirement")
        assert subscribers_b == [], (
            f"Space B should have no subscribers, got {subscribers_b}"
        )

        # Space A must have the subscriber
        subscribers_a = sub_svc.get_subscribers(space_a.id, doc_type="requirement")
        assert sub_a_id in subscribers_a, (
            f"Space A subscriber {sub_a_id!r} not found in {subscribers_a}"
        )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(num_tasks=st.integers(min_value=1, max_value=3))
def test_prop_space_data_isolation_tasks(num_tasks: int):
    """
    Property 25: Project_Space 数据隔离 — tasks

    Tasks created in Space A must not appear in Space B queries.

    **Validates: Requirements 10.1, 10.5**
    """
    session, engine = _make_db()
    try:
        space_a = _create_space(session, "space-a")
        space_b = _create_space(session, "space-b")

        task_svc = TaskService(session)

        # Register a subproject in Space A
        sub_a_id = _register_subproject(session, space_a.id)

        # Create tasks in Space A
        from agent_nexus.analyzer.base import AnalysisResult, AffectedProject, TaskTemplate
        analysis = AnalysisResult(
            affected_projects=[
                AffectedProject(
                    project_id=sub_a_id,
                    tasks=[
                        TaskTemplate(title=f"Task {i}", description=f"Desc {i}")
                        for i in range(num_tasks)
                    ],
                )
            ],
            doc_id=f"{sub_a_id}/requirement",
            version=1,
        )
        task_svc.generate(analysis=analysis, project_space_id=space_a.id)

        # Space B must return empty tasks
        tasks_b = task_svc.get_pending(project_id=sub_a_id, project_space_id=space_b.id)
        assert tasks_b == [], (
            f"Space B should have no tasks for sub_a_id, got {len(tasks_b)}"
        )

        # Space A must have the tasks
        tasks_a = task_svc.get_pending(project_id=sub_a_id, project_space_id=space_a.id)
        assert len(tasks_a) == num_tasks, (
            f"Space A should have {num_tasks} tasks, got {len(tasks_a)}"
        )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(dummy=st.integers(min_value=0, max_value=99))
def test_prop_archived_space_rejects_ack_update(dummy: int):
    """
    Property 26: 归档状态拒绝写入 — ack_update

    For any archived Project_Space, ack_update via MCP must return
    SPACE_ARCHIVED error.

    **Validates: Requirements 10.6, 10.7**
    """
    import asyncio

    session, engine = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            space = _create_space(session, "archived-space", status="archived")
            sub_id = _register_subproject(session, space.id)

            container = ServiceContainer(db_session=session, docs_root=docs_root)
            handler = ToolHandler(container)

            result = asyncio.get_event_loop().run_until_complete(
                handler.ack_update(
                    project_id=sub_id,
                    update_id=str(uuid.uuid4()),
                )
            )
            assert isinstance(result, dict), "ack_update must return a dict"
            assert result.get("error") == "SPACE_ARCHIVED", (
                f"Expected SPACE_ARCHIVED for archived space, got {result}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(dummy=st.integers(min_value=0, max_value=99))
def test_prop_archived_space_allows_read_get_document(dummy: int):
    """
    Property 26: 归档状态允许读操作 — get_document

    For an archived Project_Space that has a document, get_document must
    succeed (return the document, not SPACE_ARCHIVED).

    **Validates: Requirements 10.6, 10.7**
    """
    import asyncio

    session, engine = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            # First create an active space, push a document, then archive it
            space = _create_space(session, "to-archive-space", status="active")
            sub_id = _register_subproject(session, space.id)

            doc_svc = _make_doc_service(session, docs_root)
            doc_id = f"{sub_id}/requirement"
            doc_svc.push(
                PushRequest(
                    doc_id=doc_id,
                    content="# Archived content",
                    actor=sub_id,
                    project_space_id=space.id,
                )
            )

            # Archive the space
            space.status = "archived"
            session.flush()

            container = ServiceContainer(db_session=session, docs_root=docs_root)
            handler = ToolHandler(container)

            result = asyncio.get_event_loop().run_until_complete(
                handler.get_document(project_id=sub_id, doc_id=doc_id)
            )
            assert isinstance(result, dict), "get_document must return a dict"
            # Must NOT be an error — reads are allowed on archived spaces
            assert result.get("error") is None, (
                f"get_document should succeed on archived space, got error: {result}"
            )
            assert result.get("doc_id") == doc_id, (
                f"Expected doc_id={doc_id!r}, got {result}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(dummy=st.integers(min_value=0, max_value=99))
def test_prop_archived_space_allows_read_get_my_updates_with_context(dummy: int):
    """
    Property 26: 归档状态允许读操作 — get_my_updates_with_context

    For an archived Project_Space, get_my_updates_with_context must return a list
    (possibly empty) without SPACE_ARCHIVED error.

    **Validates: Requirements 10.6, 10.7**
    """
    import asyncio

    session, engine = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            space = _create_space(session, "archived-space", status="archived")
            sub_id = _register_subproject(session, space.id)

            container = ServiceContainer(db_session=session, docs_root=docs_root)
            handler = ToolHandler(container)

            result = asyncio.get_event_loop().run_until_complete(
                handler.get_my_updates_with_context(project_id=sub_id)
            )
            assert isinstance(result, list), "get_my_updates_with_context must return a list"
            # Must not contain SPACE_ARCHIVED error
            for item in result:
                assert item.get("error") != "SPACE_ARCHIVED", (
                    f"get_my_updates_with_context should not return SPACE_ARCHIVED on archived space, got {item}"
                )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(dummy=st.integers(min_value=0, max_value=99))
def test_prop_archived_space_allows_read_get_my_tasks(dummy: int):
    """
    Property 26: 归档状态允许读操作 — get_my_tasks

    For an archived Project_Space, get_my_tasks must return a list
    (possibly empty) without SPACE_ARCHIVED error.

    **Validates: Requirements 10.6, 10.7**
    """
    import asyncio

    session, engine = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            space = _create_space(session, "archived-space", status="archived")
            sub_id = _register_subproject(session, space.id)

            container = ServiceContainer(db_session=session, docs_root=docs_root)
            handler = ToolHandler(container)

            result = asyncio.get_event_loop().run_until_complete(
                handler.get_my_tasks(project_id=sub_id)
            )
            assert isinstance(result, list), "get_my_tasks must return a list"
            for item in result:
                assert item.get("error") != "SPACE_ARCHIVED", (
                    f"get_my_tasks should not return SPACE_ARCHIVED on archived space, got {item}"
                )
    finally:
        session.close()
        engine.dispose()
