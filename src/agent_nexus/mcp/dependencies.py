"""
ServiceContainer: holds all services needed by MCP tool handlers.

Provides a single place to wire up all service dependencies.
Also exposes make_engine / make_session_factory so server.py and main.py
share the same engine configuration (WAL mode, foreign keys).
"""

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from agent_nexus.analyzer import AnalyzerService
from agent_nexus.analyzer.llm_analyzer import LLMAnalyzer
from agent_nexus.analyzer.rule_engine import RuleEngineAnalyzer
from agent_nexus.planner.llm_client import make_llm_client
from agent_nexus.planner.planner_service import PlannerService
from agent_nexus.services.audit_log_service import AuditLogService
from agent_nexus.services.document_service import DocumentService
from agent_nexus.services.notification_service import NotificationService
from agent_nexus.services.project_service import ProjectService
from agent_nexus.services.subscription_service import SubscriptionService
from agent_nexus.services.task_service import TaskService


def make_engine(db_url: str | None = None):
    """Create a SQLAlchemy engine with WAL mode and foreign keys enabled."""
    url = db_url or os.environ.get("AGENT_NEXUS_DB_URL", "sqlite:///agent_nexus.db")
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
        llm_client = make_llm_client()
        rule_engine = RuleEngineAnalyzer()
        llm_analyzer = LLMAnalyzer()
        self.analyzer_service = AnalyzerService(
            analyzer=llm_analyzer,
            fallback=rule_engine,
            audit_log_service=self.audit_log_service,
        )
        self.document_service = DocumentService(
            db=db_session,
            docs_root=docs_root,
            audit_log_service=self.audit_log_service,
            analyzer_service=self.analyzer_service,
            subscription_service=self.subscription_service,
            notification_service=self.notification_service,
            task_service=self.task_service,
        )
        self.planner_service = PlannerService(
            container=self,
            llm_client=llm_client,
            require_review=True,
        )
