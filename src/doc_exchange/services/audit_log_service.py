"""
AuditLogService: write and query immutable audit logs.

Covers Requirements 9.1 – 9.5.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from doc_exchange.models.entities import AuditLog


class AuditLogService:
    """
    Provides write-once audit logging and read-only query access.

    No update or delete methods are exposed (Requirement 9.5).
    """

    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Requirement 9.1, 9.2, 9.3: write a log entry
    # ------------------------------------------------------------------

    def log(
        self,
        operation_type: str,
        operator_project_id: str,
        target_id: str,
        result: str,
        project_space_id: str,
        detail: str | None = None,
    ) -> AuditLog:
        """
        Write a new audit log entry with a UTC timestamp.

        Parameters
        ----------
        operation_type:
            One of push_document | ack_update | claim_task |
            register_subproject | change_stage | add_subscription |
            remove_subscription | complete_task
        operator_project_id:
            The project_id of the actor performing the operation.
        target_id:
            Identifier of the object being operated on (e.g. doc_id, task_id).
        result:
            "success" or "failure".
        project_space_id:
            The Project_Space this log entry belongs to.
        detail:
            Optional failure reason or supplementary information.
        """
        entry = AuditLog(
            id=str(uuid.uuid4()),
            project_space_id=project_space_id,
            operation_type=operation_type,
            operated_at=datetime.now(timezone.utc),
            operator_project_id=operator_project_id,
            target_id=target_id,
            result=result,
            detail=detail,
        )
        self._db.add(entry)
        self._db.flush()
        return entry

    # ------------------------------------------------------------------
    # Requirement 9.4: query logs (read-only)
    # ------------------------------------------------------------------

    def query(
        self,
        project_space_id: str,
        operator_project_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[AuditLog]:
        """
        Query audit logs with optional filters.

        All filters are ANDed together.  Returns only records belonging to
        the given project_space_id (Requirement 10.1 isolation).

        Parameters
        ----------
        project_space_id:
            Mandatory scope filter.
        operator_project_id:
            If provided, only return logs from this operator.
        start_time:
            If provided, only return logs with operated_at >= start_time.
        end_time:
            If provided, only return logs with operated_at <= end_time.
        """
        q = self._db.query(AuditLog).filter(
            AuditLog.project_space_id == project_space_id
        )

        if operator_project_id is not None:
            q = q.filter(AuditLog.operator_project_id == operator_project_id)

        if start_time is not None:
            q = q.filter(AuditLog.operated_at >= start_time)

        if end_time is not None:
            q = q.filter(AuditLog.operated_at <= end_time)

        return q.order_by(AuditLog.operated_at).all()
