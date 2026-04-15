"""
NotificationService: generate and manage change notifications.

Covers Requirements 5.1 – 5.5.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from doc_exchange.models.entities import Notification
from doc_exchange.services.errors import DocExchangeError


class NotificationService:
    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Requirement 5.1, 5.5: generate unread notifications (idempotent)
    # ------------------------------------------------------------------

    def generate(
        self,
        doc_id: str,
        version: int,
        subscriber_ids: list[str],
        project_space_id: str,
    ) -> list[Notification]:
        """
        Create unread notifications for each subscriber.

        Idempotent: if a notification for the same (doc_id, version, recipient)
        already exists, it is skipped (Requirement 5.5).
        """
        created: list[Notification] = []
        for recipient_id in subscriber_ids:
            existing = (
                self._db.query(Notification)
                .filter(
                    Notification.project_space_id == project_space_id,
                    Notification.recipient_project_id == recipient_id,
                    Notification.document_id == doc_id,
                    Notification.version == version,
                )
                .first()
            )
            if existing is not None:
                continue

            notification = Notification(
                id=str(uuid.uuid4()),
                project_space_id=project_space_id,
                recipient_project_id=recipient_id,
                document_id=doc_id,
                version=version,
                status="unread",
                created_at=datetime.now(timezone.utc),
                read_at=None,
            )
            self._db.add(notification)
            created.append(notification)

        self._db.flush()
        return created

    # ------------------------------------------------------------------
    # Requirement 5.2: get unread notifications for a recipient
    # ------------------------------------------------------------------

    def get_unread(self, project_id: str, project_space_id: str) -> list[Notification]:
        """Return all unread notifications for a recipient in the given space."""
        return (
            self._db.query(Notification)
            .filter(
                Notification.project_space_id == project_space_id,
                Notification.recipient_project_id == project_id,
                Notification.status == "unread",
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Requirement 5.3, 5.4: acknowledge a notification
    # ------------------------------------------------------------------

    def ack(self, update_id: str, project_id: str, project_space_id: str) -> None:
        """
        Mark a notification as read.

        Raises DocExchangeError(NOTIFICATION_NOT_FOUND) if update_id does not
        exist for the given recipient and space (Requirement 5.4).
        """
        notification = (
            self._db.query(Notification)
            .filter(
                Notification.id == update_id,
                Notification.project_space_id == project_space_id,
                Notification.recipient_project_id == project_id,
            )
            .first()
        )
        if notification is None:
            raise DocExchangeError(
                error_code="NOTIFICATION_NOT_FOUND",
                message=f"Notification '{update_id}' not found.",
                details={"update_id": update_id},
            )

        notification.status = "read"
        notification.read_at = datetime.now(timezone.utc)
        self._db.flush()
