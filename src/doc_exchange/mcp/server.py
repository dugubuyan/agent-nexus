"""
MCP Server registration for the Doc Exchange Center.

Tool logic lives in ToolHandler (tools.py); this module only handles
MCP server setup and tool registration.

Runs in streamable-HTTP mode so multiple agents can connect simultaneously.
Default endpoint: http://0.0.0.0:10000/mcp
"""

import os

from mcp.server.fastmcp import FastMCP
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.mcp.dependencies import ServiceContainer
from doc_exchange.mcp.tools import ToolHandler

mcp = FastMCP(
    "doc-exchange-center",
    host=os.environ.get("DOC_EXCHANGE_HOST", "0.0.0.0"),
    port=int(os.environ.get("DOC_EXCHANGE_PORT", "10086")),
)

# ---------------------------------------------------------------------------
# Default service container (SQLite, configurable via env vars)
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get("DOC_EXCHANGE_DB_URL", "sqlite:///doc_exchange.db")
_DOCS_ROOT = os.environ.get("DOC_EXCHANGE_DOCS_ROOT", "./docs")

_engine = create_engine(_DB_URL, connect_args={"check_same_thread": False})

@event.listens_for(_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

_SessionLocal = sessionmaker(bind=_engine)


def _get_handler() -> tuple[ToolHandler, any]:
    """Return (handler, session) — caller must commit/close the session."""
    session = _SessionLocal()
    container = ServiceContainer(db_session=session, docs_root=_DOCS_ROOT)
    return ToolHandler(container), session


# ---------------------------------------------------------------------------
# Tool registrations — delegate to ToolHandler
# ---------------------------------------------------------------------------


@mcp.tool()
async def push_document(
    project_id: str,
    doc_id: str,
    content: str,
    metadata: dict = {},
) -> dict:
    """Push a new document version to the exchange center."""
    handler, session = _get_handler()
    try:
        result = await handler.push_document(project_id, doc_id, content, metadata)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def get_document(
    project_id: str,
    doc_id: str,
    version: int | None = None,
) -> dict:
    """Retrieve a document (latest or specific version)."""
    handler, session = _get_handler()
    try:
        return await handler.get_document(project_id, doc_id, version)
    finally:
        session.close()


@mcp.tool()
async def get_my_updates_with_context(project_id: str) -> list[dict]:
    """
    Return all unread notifications with diff and full latest document content.
    One call gives everything needed to understand what changed and act on it.
    After processing, call ack_update for each update_id to mark as read.
    """
    handler, session = _get_handler()
    try:
        return await handler.get_my_updates_with_context(project_id)
    finally:
        session.close()


@mcp.tool()
async def get_my_updates(project_id: str) -> list[dict]:
    """Return all unread notifications for the given project."""
    handler, session = _get_handler()
    try:
        return await handler.get_my_updates(project_id)
    finally:
        session.close()


@mcp.tool()
async def ack_update(project_id: str, update_id: str) -> dict:
    """Acknowledge (mark as read) a notification."""
    handler, session = _get_handler()
    try:
        result = await handler.ack_update(project_id, update_id)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def get_my_tasks(project_id: str) -> list[dict]:
    """Return all pending/in-progress tasks for the given project."""
    handler, session = _get_handler()
    try:
        return await handler.get_my_tasks(project_id)
    finally:
        session.close()


@mcp.tool()
async def get_config(project_id: str, stage: str) -> dict:
    """Return the config document for the given project and stage."""
    handler, session = _get_handler()
    try:
        return await handler.get_config(project_id, stage)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Admin tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def generate_steering_file(project_name: str, project_space_id: str) -> dict:
    """
    Generate the content for a .kiro/steering/doc-exchange.md Steering file.
    The sub-project Kiro should create this file to enable automatic doc-update checks.
    """
    handler, session = _get_handler()
    try:
        return await handler.generate_steering_file(project_name, project_space_id)
    finally:
        session.close()


@mcp.tool()
async def get_project_id_by_name(name: str, project_space_id: str) -> dict:
    """Look up a sub-project's project_id by its human-readable name."""
    handler, session = _get_handler()
    try:
        return await handler.get_project_id_by_name(name, project_space_id)
    finally:
        session.close()


@mcp.tool()
async def add_subscription(
    subscriber_project_id: str,
    project_space_id: str,
    target_doc_id: str | None = None,
    target_doc_type: str | None = None,
) -> dict:
    """
    Add a subscription rule. Provide target_doc_id for exact doc or target_doc_type for all docs of that type.
    """
    handler, session = _get_handler()
    try:
        result = await handler.add_subscription(
            subscriber_project_id, project_space_id, target_doc_id, target_doc_type
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def create_space(name: str) -> dict:
    """Create a new Project Space. Returns the space_id needed for registering projects."""
    handler, session = _get_handler()
    try:
        result = await handler.create_space(name)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def register_project(
    name: str,
    type: str,
    project_space_id: str,
    stage: str = "design",
) -> dict:
    """
    Register a new sub-project in the given project space.

    type: development | testing | ops | infra | shared | ...
    stage: design | development | testing | deployment | upgrade
    """
    handler, session = _get_handler()
    try:
        result = await handler.register_project(name, type, project_space_id, stage)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def list_projects(project_space_id: str) -> list[dict]:
    """List all sub-projects in the given project space."""
    handler, session = _get_handler()
    try:
        return await handler.list_projects(project_space_id)
    finally:
        session.close()


@mcp.tool()
async def publish_draft(
    project_id: str,
    doc_id: str,
    version: int,
) -> dict:
    """Confirm a draft document version, publishing it and triggering notifications."""
    handler, session = _get_handler()
    try:
        result = await handler.publish_draft(project_id, doc_id, version)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def list_documents(project_id: str) -> list[dict]:
    """List all documents belonging to the given sub-project."""
    handler, session = _get_handler()
    try:
        return await handler.list_documents(project_id)
    finally:
        session.close()
