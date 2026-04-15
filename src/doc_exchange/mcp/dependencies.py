"""
ServiceContainer: holds all services needed by MCP tool handlers.

Provides a single place to wire up all service dependencies.
"""

from sqlalchemy.orm import Session

from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.notification_service import NotificationService
from doc_exchange.services.project_service import ProjectService
from doc_exchange.services.subscription_service import SubscriptionService
from doc_exchange.services.task_service import TaskService


class ServiceContainer:
    """Wires all services together for use by MCP tool handlers."""

    def __init__(self, db_session: Session, docs_root: str):
        self.db = db_session
        self.audit_log_service = AuditLogService(db_session)
        self.project_service = ProjectService(db_session)
        self.subscription_service = SubscriptionService(db_session)
        self.notification_service = NotificationService(db_session)
        self.task_service = TaskService(db_session)
        self.document_service = DocumentService(
            db=db_session,
            docs_root=docs_root,
            audit_log_service=self.audit_log_service,
            subscription_service=self.subscription_service,
            notification_service=self.notification_service,
            task_service=self.task_service,
        )
