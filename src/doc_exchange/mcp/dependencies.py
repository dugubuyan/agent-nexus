"""
ServiceContainer: holds all services needed by MCP tool handlers.

Provides a single place to wire up all service dependencies.
Also exposes make_engine / make_session_factory so server.py and main.py
share the same engine configuration (WAL mode, foreign keys).
"""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.notification_service import NotificationService
from doc_exchange.services.project_service import ProjectService
from doc_exchange.services.subscription_service import SubscriptionService
from doc_exchange.services.task_service import TaskService


def make_engine(db_url: str | None = None):
    """Create a SQLAlchemy engine with WAL mode and foreign keys enabled."""
    url = db_url or os.environ.get("DOC_EXCHANGE_DB_URL", "sqlite:///doc_exchange.db")
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(db_url: str | None = None):
    """Return a SessionLocal factory backed by a properly configured engine."""
    return sessionmaker(bind=make_engine(db_url))


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
