# Feature: doc-exchange-center, Property 18: 任务生成正确性
# Feature: doc-exchange-center, Property 19: 任务状态机
# Feature: doc-exchange-center, Property 20: 按 doc_id 查询任务
"""
Property-based tests for TaskService.

**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.6**
"""

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.analyzer.base import AffectedProject, AnalysisResult, TaskTemplate
from doc_exchange.models import Base, ProjectSpace
from doc_exchange.services.task_service import TaskService


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_doc_id = st.from_regex(r"[a-z][a-z0-9\-]{0,15}/[a-z][a-z0-9\-]{0,15}", fullmatch=True)

valid_version = st.integers(min_value=1, max_value=9999)

valid_project_id = st.from_regex(r"proj-[a-z0-9]{4,8}", fullmatch=True)

valid_task_title = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs"), whitelist_characters="-_"),
    min_size=1,
    max_size=64,
)

# A list of (project_id, [(title, description)]) pairs representing affected projects
affected_projects_strategy = st.lists(
    st.tuples(
        valid_project_id,
        st.lists(
            st.tuples(valid_task_title, st.just("Task description.")),
            min_size=1,
            max_size=3,
        ),
    ),
    min_size=1,
    max_size=5,
    unique_by=lambda x: x[0],
)


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


def _make_analysis(doc_id: str, version: int, affected: list) -> AnalysisResult:
    """Build an AnalysisResult from raw (project_id, [(title, desc)]) tuples."""
    return AnalysisResult(
        doc_id=doc_id,
        version=version,
        affected_projects=[
            AffectedProject(
                project_id=proj_id,
                tasks=[TaskTemplate(title=t, description=d) for t, d in tasks],
            )
            for proj_id, tasks in affected
        ],
    )


# ---------------------------------------------------------------------------
# Property 18: 任务生成正确性
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_id=valid_doc_id,
    version=valid_version,
    affected=affected_projects_strategy,
)
def test_prop_task_generation_correctness(doc_id: str, version: int, affected: list):
    """
    Property 18: 任务生成正确性

    对于任意文档推送，根据文档类型和相关子项目类型的映射规则，应为所有受影响的子项目生成任务；
    生成的任务中应包含触发该任务的 doc_id 和版本号；调用 get_my_tasks 应能查询到这些任务。

    For any document push with an AnalysisResult, TaskService.generate() must:
    - Create exactly one task per (affected_project, task_template) pair.
    - Each task must record the trigger doc_id and version.
    - Each task must start with status "pending".
    - get_pending() for each affected project must return those tasks.

    **Validates: Requirements 7.1, 7.2**
    """
    # Feature: doc-exchange-center, Property 18: 对于任意文档推送，根据文档类型和相关子项目类型的映射规则，
    # 应为所有受影响的子项目生成任务；生成的任务中应包含触发该任务的 doc_id 和版本号；
    # 调用 get_my_tasks 应能查询到这些任务。
    session, engine, space_id = _make_db()
    try:
        svc = TaskService(session)
        analysis = _make_analysis(doc_id, version, affected)

        tasks = svc.generate(analysis, space_id)

        # Total tasks = sum of templates per project
        expected_count = sum(len(tasks_list) for _, tasks_list in affected)
        assert len(tasks) == expected_count, (
            f"Expected {expected_count} tasks, got {len(tasks)}"
        )

        # Every task must carry the trigger doc_id and version (Req 7.1)
        for task in tasks:
            assert task.trigger_doc_id == doc_id, (
                f"trigger_doc_id mismatch: expected {doc_id!r}, got {task.trigger_doc_id!r}"
            )
            assert task.trigger_version == version, (
                f"trigger_version mismatch: expected {version}, got {task.trigger_version}"
            )
            assert task.status == "pending", (
                f"New task status should be 'pending', got {task.status!r}"
            )

        # get_pending() must return the generated tasks for each affected project (Req 7.2)
        for proj_id, proj_tasks in affected:
            pending = svc.get_pending(proj_id, space_id)
            pending_ids = {t.id for t in pending}
            generated_for_proj = [t for t in tasks if t.assignee_project_id == proj_id]
            for t in generated_for_proj:
                assert t.id in pending_ids, (
                    f"Task {t.id!r} for project {proj_id!r} not found in get_pending()"
                )
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 19: 任务状态机
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_id=valid_doc_id,
    version=valid_version,
    project_id=valid_project_id,
)
def test_prop_task_state_machine(doc_id: str, version: int, project_id: str):
    """
    Property 19: 任务状态机

    对于任意 pending 状态的任务，认领后状态应变为 in_progress，且记录认领方和认领时间；
    完成后状态应变为 completed，且记录完成时间；状态转换不可逆（completed 任务不能回到 pending）。

    For any pending task:
    - After claim(): status == "in_progress", claimed_by is set, claimed_at is set.
    - After complete(): status == "completed", completed_at is set.
    - A completed task is no longer returned by get_pending().

    **Validates: Requirements 7.3, 7.4**
    """
    # Feature: doc-exchange-center, Property 19: 对于任意 pending 状态的任务，认领后状态应变为 in_progress，
    # 且记录认领方和认领时间；完成后状态应变为 completed，且记录完成时间；
    # 状态转换不可逆（completed 任务不能回到 pending）。
    session, engine, space_id = _make_db()
    try:
        svc = TaskService(session)
        analysis = _make_analysis(
            doc_id,
            version,
            [(project_id, [("Review doc", "Please review.")])],
        )

        tasks = svc.generate(analysis, space_id)
        assert len(tasks) == 1
        task = tasks[0]

        # Initial state: pending
        assert task.status == "pending"
        assert task.claimed_by is None
        assert task.claimed_at is None
        assert task.completed_at is None

        # Claim: pending → in_progress (Req 7.3)
        claimed = svc.claim(task.id, project_id, space_id)
        assert claimed.status == "in_progress", (
            f"After claim(), expected 'in_progress', got {claimed.status!r}"
        )
        assert claimed.claimed_by == project_id, (
            f"claimed_by should be {project_id!r}, got {claimed.claimed_by!r}"
        )
        assert claimed.claimed_at is not None, "claimed_at must be set after claim()"

        # Task still appears in get_pending (in_progress is included)
        pending_after_claim = svc.get_pending(project_id, space_id)
        assert any(t.id == task.id for t in pending_after_claim), (
            "in_progress task should still appear in get_pending()"
        )

        # Complete: in_progress → completed (Req 7.4)
        completed = svc.complete(task.id, project_id, space_id)
        assert completed.status == "completed", (
            f"After complete(), expected 'completed', got {completed.status!r}"
        )
        assert completed.completed_at is not None, "completed_at must be set after complete()"

        # Irreversibility: completed task must NOT appear in get_pending (Req 7.4)
        pending_after_complete = svc.get_pending(project_id, space_id)
        assert not any(t.id == task.id for t in pending_after_complete), (
            "completed task must not appear in get_pending()"
        )
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 20: 按 doc_id 查询任务
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    doc_id_a=valid_doc_id,
    doc_id_b=valid_doc_id,
    version_a=valid_version,
    version_b=valid_version,
    affected_a=affected_projects_strategy,
    affected_b=affected_projects_strategy,
)
def test_prop_get_tasks_by_doc_id(
    doc_id_a: str,
    doc_id_b: str,
    version_a: int,
    version_b: int,
    affected_a: list,
    affected_b: list,
):
    """
    Property 20: 按 doc_id 查询任务

    对于任意文档推送，通过该文档的 doc_id 查询任务，应返回所有由该文档变更触发的任务，
    且不包含其他文档触发的任务。

    For any two distinct document pushes (doc_id_a and doc_id_b):
    - get_by_doc_id(doc_id_a) returns exactly the tasks triggered by doc_id_a.
    - get_by_doc_id(doc_id_b) returns exactly the tasks triggered by doc_id_b.
    - Neither result contains tasks from the other document.

    **Validates: Requirements 7.6**
    """
    # Feature: doc-exchange-center, Property 20: 对于任意文档推送，通过该文档的 doc_id 查询任务，
    # 应返回所有由该文档变更触发的任务，且不包含其他文档触发的任务。

    # Skip when both doc_ids are identical to avoid ambiguity
    if doc_id_a == doc_id_b:
        return

    session, engine, space_id = _make_db()
    try:
        svc = TaskService(session)

        analysis_a = _make_analysis(doc_id_a, version_a, affected_a)
        analysis_b = _make_analysis(doc_id_b, version_b, affected_b)

        tasks_a = svc.generate(analysis_a, space_id)
        tasks_b = svc.generate(analysis_b, space_id)

        ids_a = {t.id for t in tasks_a}
        ids_b = {t.id for t in tasks_b}

        # Query by doc_id_a — must return all tasks from doc_a, none from doc_b
        result_a = svc.get_by_doc_id(doc_id_a, space_id)
        result_a_ids = {t.id for t in result_a}

        assert result_a_ids == ids_a, (
            f"get_by_doc_id({doc_id_a!r}) returned unexpected tasks. "
            f"Expected {ids_a}, got {result_a_ids}"
        )
        assert result_a_ids.isdisjoint(ids_b), (
            f"get_by_doc_id({doc_id_a!r}) must not include tasks from {doc_id_b!r}"
        )

        # All returned tasks must have trigger_doc_id == doc_id_a
        for task in result_a:
            assert task.trigger_doc_id == doc_id_a, (
                f"Task {task.id!r} has trigger_doc_id={task.trigger_doc_id!r}, "
                f"expected {doc_id_a!r}"
            )

        # Query by doc_id_b — must return all tasks from doc_b, none from doc_a
        result_b = svc.get_by_doc_id(doc_id_b, space_id)
        result_b_ids = {t.id for t in result_b}

        assert result_b_ids == ids_b, (
            f"get_by_doc_id({doc_id_b!r}) returned unexpected tasks. "
            f"Expected {ids_b}, got {result_b_ids}"
        )
        assert result_b_ids.isdisjoint(ids_a), (
            f"get_by_doc_id({doc_id_b!r}) must not include tasks from {doc_id_a!r}"
        )

        # All returned tasks must have trigger_doc_id == doc_id_b
        for task in result_b:
            assert task.trigger_doc_id == doc_id_b, (
                f"Task {task.id!r} has trigger_doc_id={task.trigger_doc_id!r}, "
                f"expected {doc_id_b!r}"
            )
    finally:
        session.close()
        engine.dispose()
