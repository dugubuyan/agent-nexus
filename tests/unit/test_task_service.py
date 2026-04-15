"""
Unit tests for TaskService (Requirements 7.1 – 7.6).
"""

import pytest
from sqlalchemy.orm import Session

from doc_exchange.analyzer.base import AffectedProject, AnalysisResult, TaskTemplate
from doc_exchange.services import DocExchangeError, TaskService


def make_svc(db_session: Session) -> TaskService:
    return TaskService(db_session)


def make_analysis(
    doc_id: str = "svc-x/api",
    version: int = 1,
    affected: list[tuple[str, list[tuple[str, str]]]] | None = None,
) -> AnalysisResult:
    """Build an AnalysisResult with optional affected projects."""
    if affected is None:
        affected = [("proj-a", [("Review API", "Check the updated API spec.")])]
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
# generate() tests
# ---------------------------------------------------------------------------


def test_generate_creates_tasks_for_each_affected_project(db_session, default_space):
    """generate() creates one task per template per affected project (Req 7.1)."""
    svc = make_svc(db_session)
    analysis = make_analysis(
        affected=[
            ("proj-a", [("Task A", "Desc A")]),
            ("proj-b", [("Task B", "Desc B")]),
        ]
    )

    tasks = svc.generate(analysis, default_space.id)

    assert len(tasks) == 2
    assignees = {t.assignee_project_id for t in tasks}
    assert assignees == {"proj-a", "proj-b"}


def test_generate_includes_trigger_doc_id_and_version(db_session, default_space):
    """generate() records trigger_doc_id and trigger_version on each task (Req 7.1)."""
    svc = make_svc(db_session)
    analysis = make_analysis(doc_id="svc-y/requirement", version=5)

    tasks = svc.generate(analysis, default_space.id)

    for task in tasks:
        assert task.trigger_doc_id == "svc-y/requirement"
        assert task.trigger_version == 5


def test_generate_tasks_start_as_pending(db_session, default_space):
    """generate() creates tasks with status=pending."""
    svc = make_svc(db_session)
    tasks = svc.generate(make_analysis(), default_space.id)

    for task in tasks:
        assert task.status == "pending"


def test_generate_multiple_templates_per_project(db_session, default_space):
    """generate() creates one task per template when a project has multiple templates."""
    svc = make_svc(db_session)
    analysis = make_analysis(
        affected=[("proj-a", [("Task 1", "Desc 1"), ("Task 2", "Desc 2")])]
    )

    tasks = svc.generate(analysis, default_space.id)

    assert len(tasks) == 2
    titles = {t.title for t in tasks}
    assert titles == {"Task 1", "Task 2"}


# ---------------------------------------------------------------------------
# get_pending() tests
# ---------------------------------------------------------------------------


def test_get_pending_returns_pending_and_in_progress_tasks(db_session, default_space):
    """get_pending() returns tasks with status pending or in_progress (Req 7.2)."""
    svc = make_svc(db_session)
    tasks = svc.generate(make_analysis(affected=[
        ("proj-a", [("T1", "D1"), ("T2", "D2")])
    ]), default_space.id)

    # Claim one task to put it in_progress
    svc.claim(tasks[0].id, "proj-a", default_space.id)

    pending = svc.get_pending("proj-a", default_space.id)
    statuses = {t.status for t in pending}

    assert len(pending) == 2
    assert statuses == {"pending", "in_progress"}


def test_get_pending_excludes_completed_tasks(db_session, default_space):
    """get_pending() does not return completed tasks."""
    svc = make_svc(db_session)
    tasks = svc.generate(make_analysis(), default_space.id)
    task = tasks[0]

    svc.claim(task.id, "proj-a", default_space.id)
    svc.complete(task.id, "proj-a", default_space.id)

    pending = svc.get_pending("proj-a", default_space.id)
    assert all(t.id != task.id for t in pending)


# ---------------------------------------------------------------------------
# claim() tests
# ---------------------------------------------------------------------------


def test_claim_transitions_task_to_in_progress(db_session, default_space):
    """claim() sets status to in_progress and records claimed_by/claimed_at (Req 7.3)."""
    svc = make_svc(db_session)
    tasks = svc.generate(make_analysis(), default_space.id)
    task = tasks[0]

    claimed = svc.claim(task.id, "proj-a", default_space.id)

    assert claimed.status == "in_progress"
    assert claimed.claimed_by == "proj-a"
    assert claimed.claimed_at is not None


def test_claim_nonexistent_task_raises_task_not_found(db_session, default_space):
    """claim() raises TASK_NOT_FOUND for unknown task_id (Req 7.5)."""
    svc = make_svc(db_session)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.claim("no-such-task", "proj-a", default_space.id)

    assert exc_info.value.error_code == "TASK_NOT_FOUND"


# ---------------------------------------------------------------------------
# complete() tests
# ---------------------------------------------------------------------------


def test_complete_transitions_task_to_completed(db_session, default_space):
    """complete() sets status to completed and records completed_at (Req 7.4)."""
    svc = make_svc(db_session)
    tasks = svc.generate(make_analysis(), default_space.id)
    task = tasks[0]

    svc.claim(task.id, "proj-a", default_space.id)
    completed = svc.complete(task.id, "proj-a", default_space.id)

    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_complete_nonexistent_task_raises_task_not_found(db_session, default_space):
    """complete() raises TASK_NOT_FOUND for unknown task_id (Req 7.5)."""
    svc = make_svc(db_session)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.complete("no-such-task", "proj-a", default_space.id)

    assert exc_info.value.error_code == "TASK_NOT_FOUND"


# ---------------------------------------------------------------------------
# get_by_doc_id() tests
# ---------------------------------------------------------------------------


def test_get_by_doc_id_returns_only_tasks_for_that_doc(db_session, default_space):
    """get_by_doc_id() returns only tasks triggered by the specified doc_id (Req 7.6)."""
    svc = make_svc(db_session)

    svc.generate(make_analysis(doc_id="svc-x/api", version=1), default_space.id)
    svc.generate(make_analysis(doc_id="svc-y/design", version=1), default_space.id)

    tasks = svc.get_by_doc_id("svc-x/api", default_space.id)

    assert len(tasks) == 1
    assert all(t.trigger_doc_id == "svc-x/api" for t in tasks)


def test_get_by_doc_id_returns_empty_for_unknown_doc(db_session, default_space):
    """get_by_doc_id() returns empty list when no tasks match the doc_id."""
    svc = make_svc(db_session)

    tasks = svc.get_by_doc_id("nonexistent/doc", default_space.id)

    assert tasks == []
