"""
Unit tests for MCP ToolHandler.

Covers Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 10.6, 10.7.
"""

import uuid
from datetime import datetime, timezone

import pytest

from doc_exchange.mcp.dependencies import ServiceContainer
from doc_exchange.mcp.tools import ToolHandler
from doc_exchange.models.entities import Notification, ProjectSpace, SubProject, Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_space(db_session, status: str = "active") -> ProjectSpace:
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="test-space",
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(space)
    db_session.flush()
    return space


def make_subproject(db_session, space_id: str) -> SubProject:
    sp = SubProject(
        id=str(uuid.uuid4()),
        project_space_id=space_id,
        name="test-project",
        type="development",
        stage="design",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sp)
    db_session.flush()
    return sp


def make_handler(db_session, tmp_docs_root) -> ToolHandler:
    container = ServiceContainer(db_session=db_session, docs_root=tmp_docs_root)
    return ToolHandler(container)


# ---------------------------------------------------------------------------
# push_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_document_valid_project(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    doc_id = f"{sp.id}/design"
    result = await handler.push_document(sp.id, doc_id, "# Design\nContent here")

    assert "error" not in result
    assert result["version"] == 1
    assert result["doc_id"] == doc_id


@pytest.mark.asyncio
async def test_push_document_invalid_project_returns_unauthorized(db_session, tmp_docs_root):
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.push_document("nonexistent-id", "some/design", "content")

    assert result["error"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_push_document_archived_space_returns_space_archived(db_session, tmp_docs_root):
    space = make_space(db_session, status="archived")
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.push_document(sp.id, f"{sp.id}/design", "# Content")

    assert result["error"] == "SPACE_ARCHIVED"


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_valid_project_returns_document(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    doc_id = f"{sp.id}/requirement"
    await handler.push_document(sp.id, doc_id, "# Requirements\nSome content")

    result = await handler.get_document(sp.id, doc_id)

    assert "error" not in result
    assert result["doc_id"] == doc_id
    assert result["content"] == "# Requirements\nSome content"
    assert result["version"] == 1


@pytest.mark.asyncio
async def test_get_document_invalid_project_returns_unauthorized(db_session, tmp_docs_root):
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.get_document("nonexistent-id", "some/design")

    assert result["error"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# get_my_updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_my_updates_returns_unread_notifications(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    # Manually insert an unread notification
    notif = Notification(
        id=str(uuid.uuid4()),
        project_space_id=space.id,
        recipient_project_id=sp.id,
        document_id=f"{sp.id}/design",
        version=1,
        status="unread",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(notif)
    db_session.flush()

    result = await handler.get_my_updates(sp.id)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["doc_id"] == f"{sp.id}/design"
    assert result[0]["version"] == 1


@pytest.mark.asyncio
async def test_get_my_updates_invalid_project_returns_unauthorized(db_session, tmp_docs_root):
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.get_my_updates("nonexistent-id")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["error"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# ack_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ack_update_marks_notification_as_read(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    notif = Notification(
        id=str(uuid.uuid4()),
        project_space_id=space.id,
        recipient_project_id=sp.id,
        document_id=f"{sp.id}/design",
        version=1,
        status="unread",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(notif)
    db_session.flush()

    result = await handler.ack_update(sp.id, notif.id)

    assert result["status"] == "ok"
    assert result["update_id"] == notif.id

    # Verify it no longer appears in get_my_updates
    updates = await handler.get_my_updates(sp.id)
    assert len(updates) == 0


@pytest.mark.asyncio
async def test_ack_update_invalid_project_returns_unauthorized(db_session, tmp_docs_root):
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.ack_update("nonexistent-id", str(uuid.uuid4()))

    assert result["error"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_ack_update_archived_space_returns_space_archived(db_session, tmp_docs_root):
    space = make_space(db_session, status="archived")
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.ack_update(sp.id, str(uuid.uuid4()))

    assert result["error"] == "SPACE_ARCHIVED"


# ---------------------------------------------------------------------------
# get_my_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_my_tasks_returns_pending_tasks(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    task = Task(
        id=str(uuid.uuid4()),
        project_space_id=space.id,
        assignee_project_id=sp.id,
        trigger_doc_id=f"{sp.id}/design",
        trigger_version=1,
        title="Review design doc",
        description="Please review the updated design document.",
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    db_session.flush()

    result = await handler.get_my_tasks(sp.id)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["title"] == "Review design doc"
    assert result[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_my_tasks_invalid_project_returns_unauthorized(db_session, tmp_docs_root):
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.get_my_tasks("nonexistent-id")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["error"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_config_returns_config_document(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    # Push a config document first
    doc_id = f"{sp.id}/config/dev"
    await handler.push_document(sp.id, doc_id, "# Dev Config\nkey=value")

    result = await handler.get_config(sp.id, "dev")

    assert "error" not in result
    assert result["content"] == "# Dev Config\nkey=value"
    assert result["doc_id"] == doc_id


@pytest.mark.asyncio
async def test_get_config_invalid_project_returns_unauthorized(db_session, tmp_docs_root):
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.get_config("nonexistent-id", "dev")

    assert result["error"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_config_invalid_stage_returns_error(db_session, tmp_docs_root):
    space = make_space(db_session)
    sp = make_subproject(db_session, space.id)
    handler = make_handler(db_session, tmp_docs_root)

    result = await handler.get_config(sp.id, "staging")

    assert result["error"] == "INVALID_STAGE"
