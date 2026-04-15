"""
TaskService: generate and manage tasks triggered by document changes.

Covers Requirements 7.1 – 7.6.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from doc_exchange.analyzer.base import AnalysisResult
from doc_exchange.models.entities import Task
from doc_exchange.services.errors import DocExchangeError


class TaskService:
    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Requirement 7.1: generate tasks from AnalysisResult
    # ------------------------------------------------------------------

    def generate(self, analysis: AnalysisResult, project_space_id: str) -> list[Task]:
        """
        Create tasks for each affected project based on AnalysisResult task templates.

        Each task records trigger_doc_id and trigger_version (Requirement 7.1).
        """
        created: list[Task] = []
        for affected in analysis.affected_projects:
            for template in affected.tasks:
                task = Task(
                    id=str(uuid.uuid4()),
                    project_space_id=project_space_id,
                    assignee_project_id=affected.project_id,
                    trigger_doc_id=analysis.doc_id,
                    trigger_version=analysis.version,
                    title=template.title,
                    description=template.description,
                    status="pending",
                    claimed_by=None,
                    claimed_at=None,
                    completed_at=None,
                    created_at=datetime.now(timezone.utc),
                )
                self._db.add(task)
                created.append(task)

        self._db.flush()
        return created

    # ------------------------------------------------------------------
    # Requirement 7.2: get pending/in-progress tasks for a project
    # ------------------------------------------------------------------

    def get_pending(self, project_id: str, project_space_id: str) -> list[Task]:
        """Return tasks with status pending or in_progress for the given project."""
        return (
            self._db.query(Task)
            .filter(
                Task.project_space_id == project_space_id,
                Task.assignee_project_id == project_id,
                Task.status.in_(["pending", "in_progress"]),
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Requirement 7.3: claim a task (pending → in_progress)
    # ------------------------------------------------------------------

    def claim(self, task_id: str, project_id: str, project_space_id: str) -> Task:
        """
        Transition a task from pending to in_progress.

        Records claimed_by and claimed_at.
        Raises DocExchangeError(TASK_NOT_FOUND) if task does not exist (Requirement 7.5).
        """
        task = (
            self._db.query(Task)
            .filter(
                Task.id == task_id,
                Task.project_space_id == project_space_id,
            )
            .first()
        )
        if task is None:
            raise DocExchangeError(
                error_code="TASK_NOT_FOUND",
                message=f"Task '{task_id}' not found.",
                details={"task_id": task_id},
            )

        task.status = "in_progress"
        task.claimed_by = project_id
        task.claimed_at = datetime.now(timezone.utc)
        self._db.flush()
        return task

    # ------------------------------------------------------------------
    # Requirement 7.4: complete a task (in_progress → completed)
    # ------------------------------------------------------------------

    def complete(self, task_id: str, project_id: str, project_space_id: str) -> Task:
        """
        Transition a task from in_progress to completed.

        Records completed_at.
        Raises DocExchangeError(TASK_NOT_FOUND) if task does not exist (Requirement 7.5).
        """
        task = (
            self._db.query(Task)
            .filter(
                Task.id == task_id,
                Task.project_space_id == project_space_id,
            )
            .first()
        )
        if task is None:
            raise DocExchangeError(
                error_code="TASK_NOT_FOUND",
                message=f"Task '{task_id}' not found.",
                details={"task_id": task_id},
            )

        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        self._db.flush()
        return task

    # ------------------------------------------------------------------
    # Requirement 7.6: get tasks by trigger doc_id
    # ------------------------------------------------------------------

    def get_by_doc_id(self, trigger_doc_id: str, project_space_id: str) -> list[Task]:
        """Return all tasks triggered by a specific doc_id in the given space."""
        return (
            self._db.query(Task)
            .filter(
                Task.project_space_id == project_space_id,
                Task.trigger_doc_id == trigger_doc_id,
            )
            .all()
        )
