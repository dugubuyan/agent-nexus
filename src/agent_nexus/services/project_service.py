"""
ProjectService: sub-project registration, listing, and stage management.

Covers Requirements 1.1 – 1.5, 7.7, 12.1.
"""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from agent_nexus.models.entities import SubProject
from agent_nexus.services.errors import AgentNexusError

if TYPE_CHECKING:
    from agent_nexus.services.document_service import DocumentService
    from agent_nexus.services.task_service import TaskService

VALID_STAGES = {"design", "development", "testing", "deployment", "upgrade"}


class ProjectService:
    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Requirement 1.2: register a new sub-project
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        type: str,
        project_space_id: str,
        stage: str = "design",
    ) -> SubProject:
        """
        Register a new sub-project, or return the existing one if a sub-project
        with the same name already exists in this space (idempotent).

        Raises AgentNexusError(MISSING_REQUIRED_FIELD) if name or type is
        empty/None (Requirement 1.4).
        Returns the created or existing SubProject (Requirement 1.2).
        """
        missing = [f for f, v in (("name", name), ("type", type)) if not v]
        if missing:
            raise AgentNexusError(
                error_code="MISSING_REQUIRED_FIELD",
                message="Registration request is missing required fields.",
                details={"missing_fields": missing},
            )

        # Idempotent: return existing sub-project if name already taken in space
        existing = (
            self._db.query(SubProject)
            .filter(
                SubProject.name == name,
                SubProject.project_space_id == project_space_id,
            )
            .first()
        )
        if existing is not None:
            return existing

        now = datetime.now(timezone.utc)
        subproject = SubProject(
            id=str(uuid.uuid4()),
            project_space_id=project_space_id,
            name=name,
            type=type,
            stage=stage,
            stage_updated_at=now,
            created_at=now,
        )
        self._db.add(subproject)
        self._db.flush()
        return subproject

    def delete(self, project_id: str, project_space_id: str) -> bool:
        """
        Delete a sub-project by ID.

        Returns True if deleted, False if not found.
        Does NOT cascade-delete documents, subscriptions, or notifications
        owned by this project — those remain for audit purposes.
        """
        subproject = self.get(project_id, project_space_id)
        if subproject is None:
            return False
        self._db.delete(subproject)
        self._db.flush()
        return True

    # ------------------------------------------------------------------
    # Requirement 1.5: list all sub-projects in a space
    # ------------------------------------------------------------------

    def list_subprojects(self, project_space_id: str) -> list[SubProject]:
        """Return all sub-projects belonging to the given project space."""
        return (
            self._db.query(SubProject)
            .filter(SubProject.project_space_id == project_space_id)
            .all()
        )

    # ------------------------------------------------------------------
    # Requirement 1.3: change stage
    # ------------------------------------------------------------------

    def change_stage(
        self,
        project_id: str,
        new_stage: str,
        project_space_id: str,
        document_service: Optional["DocumentService"] = None,
        task_service: Optional["TaskService"] = None,
    ) -> SubProject:
        """
        Update the stage of a sub-project.

        Raises AgentNexusError(PROJECT_NOT_FOUND) if the project does not exist.
        Updates stage and stage_updated_at (Requirement 1.3).

        If document_service is provided, creates milestone snapshots for all
        published documents of the subproject (Requirement 12.1).

        If task_service is provided, generates a stage-switch task for the
        subproject (Requirement 7.7).
        """
        subproject = self.get(project_id, project_space_id)
        if subproject is None:
            raise AgentNexusError(
                error_code="PROJECT_NOT_FOUND",
                message=f"Sub-project '{project_id}' not found.",
                details={"project_id": project_id},
            )

        subproject.stage = new_stage
        subproject.stage_updated_at = datetime.now(timezone.utc)
        self._db.flush()

        # Req 12.1: create milestone snapshots for all published documents
        if document_service is not None:
            document_service.create_milestone_snapshot(
                subproject_id=project_id,
                new_stage=new_stage,
                triggered_by=project_id,
                project_space_id=project_space_id,
            )

        # Req 7.7: generate stage-switch task for the subproject
        if task_service is not None:
            from agent_nexus.analyzer.base import AnalysisResult, AffectedProject, TaskTemplate
            analysis = AnalysisResult(
                affected_projects=[
                    AffectedProject(
                        project_id=project_id,
                        tasks=[
                            TaskTemplate(
                                title="Stage switch",
                                description=f"Subproject stage changed to {new_stage}",
                            )
                        ],
                    )
                ],
                doc_id=f"{project_id}/task",
                version=0,
            )
            task_service.generate(analysis=analysis, project_space_id=project_space_id)

        return subproject

    # ------------------------------------------------------------------
    # Helper: get a single sub-project
    # ------------------------------------------------------------------

    def get(self, project_id: str, project_space_id: str) -> SubProject | None:
        """Return the sub-project or None if not found."""
        return (
            self._db.query(SubProject)
            .filter(
                SubProject.id == project_id,
                SubProject.project_space_id == project_space_id,
            )
            .first()
        )
