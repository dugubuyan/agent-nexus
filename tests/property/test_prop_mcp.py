# Feature: doc-exchange-center, Property 22: project_id 合法性验证
"""
Property-based tests for MCP tool project_id validation.

**Validates: Requirements 8.4**
"""

import asyncio
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.mcp.dependencies import ServiceContainer
from doc_exchange.mcp.tools import ToolHandler
from doc_exchange.models import Base, ProjectSpace
from doc_exchange.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Helpers
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


def _make_handler(session, docs_root: str) -> ToolHandler:
    container = ServiceContainer(db_session=session, docs_root=docs_root)
    return ToolHandler(container)


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Strategy: generate UUIDs that are guaranteed not to be registered
# We generate fresh UUIDs; since each test uses a fresh DB, none will exist.
# ---------------------------------------------------------------------------

nonexistent_project_id = st.uuids().map(str)


# ---------------------------------------------------------------------------
# Property 22: project_id 合法性验证
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(project_id=nonexistent_project_id)
def test_prop_mcp_nonexistent_project_id_get_my_updates(project_id: str):
    """
    Property 22 (get_my_updates): For any non-existent project_id,
    get_my_updates must return a list containing an UNAUTHORIZED error.

    **Validates: Requirements 8.4**
    """
    session, engine, space_id = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            handler = _make_handler(session, docs_root)
            result = _run(handler.get_my_updates(project_id=project_id))
            assert isinstance(result, list), "get_my_updates must return a list"
            assert len(result) == 1, f"Expected 1 error entry, got {len(result)}"
            assert result[0].get("error") == "UNAUTHORIZED", (
                f"Expected UNAUTHORIZED, got {result[0]}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(project_id=nonexistent_project_id)
def test_prop_mcp_nonexistent_project_id_get_document(project_id: str):
    """
    Property 22 (get_document): For any non-existent project_id,
    get_document must return a dict with error == UNAUTHORIZED.

    **Validates: Requirements 8.4**
    """
    session, engine, space_id = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            handler = _make_handler(session, docs_root)
            result = _run(handler.get_document(project_id=project_id, doc_id="any/requirement"))
            assert isinstance(result, dict), "get_document must return a dict"
            assert result.get("error") == "UNAUTHORIZED", (
                f"Expected UNAUTHORIZED, got {result}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(project_id=nonexistent_project_id)
def test_prop_mcp_nonexistent_project_id_push_document(project_id: str):
    """
    Property 22 (push_document): For any non-existent project_id,
    push_document must return a dict with error == UNAUTHORIZED.

    **Validates: Requirements 8.4**
    """
    session, engine, space_id = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            handler = _make_handler(session, docs_root)
            result = _run(
                handler.push_document(
                    project_id=project_id,
                    doc_id="any/requirement",
                    content="# Test",
                )
            )
            assert isinstance(result, dict), "push_document must return a dict"
            assert result.get("error") == "UNAUTHORIZED", (
                f"Expected UNAUTHORIZED, got {result}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(project_id=nonexistent_project_id)
def test_prop_mcp_nonexistent_project_id_get_my_tasks(project_id: str):
    """
    Property 22 (get_my_tasks): For any non-existent project_id,
    get_my_tasks must return a list containing an UNAUTHORIZED error.

    **Validates: Requirements 8.4**
    """
    session, engine, space_id = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            handler = _make_handler(session, docs_root)
            result = _run(handler.get_my_tasks(project_id=project_id))
            assert isinstance(result, list), "get_my_tasks must return a list"
            assert len(result) == 1, f"Expected 1 error entry, got {len(result)}"
            assert result[0].get("error") == "UNAUTHORIZED", (
                f"Expected UNAUTHORIZED, got {result[0]}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(project_id=nonexistent_project_id)
def test_prop_mcp_nonexistent_project_id_ack_update(project_id: str):
    """
    Property 22 (ack_update): For any non-existent project_id,
    ack_update must return a dict with error == UNAUTHORIZED.

    **Validates: Requirements 8.4**
    """
    session, engine, space_id = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            handler = _make_handler(session, docs_root)
            result = _run(
                handler.ack_update(project_id=project_id, update_id=str(uuid.uuid4()))
            )
            assert isinstance(result, dict), "ack_update must return a dict"
            assert result.get("error") == "UNAUTHORIZED", (
                f"Expected UNAUTHORIZED, got {result}"
            )
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(project_id=nonexistent_project_id)
def test_prop_mcp_nonexistent_project_id_get_config(project_id: str):
    """
    Property 22 (get_config): For any non-existent project_id,
    get_config must return a dict with error == UNAUTHORIZED.

    **Validates: Requirements 8.4**
    """
    session, engine, space_id = _make_db()
    try:
        with tempfile.TemporaryDirectory() as docs_root:
            handler = _make_handler(session, docs_root)
            result = _run(handler.get_config(project_id=project_id, stage="dev"))
            assert isinstance(result, dict), "get_config must return a dict"
            assert result.get("error") == "UNAUTHORIZED", (
                f"Expected UNAUTHORIZED, got {result}"
            )
    finally:
        session.close()
        engine.dispose()
