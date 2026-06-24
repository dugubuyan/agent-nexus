"""
Unit tests for POST /api/documents — out-of-band full-content write endpoint.

Uses the same _MockMCP + Starlette TestClient pattern as test_web_dashboard.py.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_nexus.mcp.dependencies import ServiceContainer
from agent_nexus.mcp.tools import ToolHandler
from agent_nexus.models import Base
from agent_nexus.models.entities import Document, DocumentVersion, ProjectSpace, SubProject
from agent_nexus.search.fts import ensure_fts_table, search as fts_search
from agent_nexus.web.routes import register_web_routes


class _NoCloseSession:
    """Wrapper that delegates all session ops but ignores close() calls.

    Used in tests so the route handler's finally-close doesn't break the
    test fixture's rollback-based session.
    """
    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def close(self):
        pass  # swallow - test fixture manages lifecycle


# ---------------------------------------------------------------------------
# Test infrastructure (mirrors test_web_dashboard.py)
# ---------------------------------------------------------------------------

class _MockMCP:
    def __init__(self):
        self._routes = []

    def custom_route(self, path, methods):
        def decorator(fn):
            self._routes.append((path, methods, fn))
            return fn
        return decorator

    def build_starlette_app(self):
        routes = [Route(path, endpoint=fn, methods=methods)
                  for path, methods, fn in self._routes]
        return Starlette(routes=routes)


def _make_fts_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    ensure_fts_table(eng)
    return eng


@pytest.fixture()
def fts_engine():
    eng = _make_fts_engine()
    yield eng
    eng.dispose()


@pytest.fixture()
def test_session(fts_engine):
    connection = fts_engine.connect()
    transaction = connection.begin()
    SessionFactory = sessionmaker(bind=connection)
    session = SessionFactory()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(test_session, tmp_path):
    container = ServiceContainer(db_session=test_session, docs_root=str(tmp_path))
    handler = ToolHandler(container)

    def get_handler():
        # In tests, don't close the session so the test fixture can still use it
        return handler, _NoCloseSession(test_session)

    mock_mcp = _MockMCP()
    register_web_routes(mock_mcp, get_handler=get_handler)
    return TestClient(mock_mcp.build_starlette_app(), raise_server_exceptions=True)


def _make_space(session):
    space = ProjectSpace(
        id=str(uuid.uuid4()), name="test", status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()
    return space


def _make_subproject(session, space_id):
    sp = SubProject(
        id=str(uuid.uuid4()), project_space_id=space_id,
        name="svc", type="development", stage="design",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(sp)
    session.flush()
    return sp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_push_returns_version_and_status(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)

    resp = client.post("/api/documents", json={
        "project_id": sp.id,
        "doc_id": f"{sp.id}/requirement",
        "content": "# Requirements\n\nSupport OAuth2.",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert data["status"] == "published"
    assert data["doc_id"] == f"{sp.id}/requirement"


def test_second_push_increments_version(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    doc_id = f"{sp.id}/design"

    client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v1"
    })
    resp2 = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v2 updated"
    })
    assert resp2.status_code == 200
    assert resp2.json()["version"] == 2


def test_pushed_doc_fts_searchable(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)

    client.post("/api/documents", json={
        "project_id": sp.id,
        "doc_id": f"{sp.id}/requirement",
        "content": "Authentication via OAuth2 and JWT tokens.",
    })

    results = fts_search(test_session, space.id, "OAuth2")
    assert len(results) >= 1
    assert any(r["doc_id"] == f"{sp.id}/requirement" for r in results)


def test_missing_project_id_returns_400(client):
    resp = client.post("/api/documents", json={
        "doc_id": "proj/design", "content": "content"
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_FIELD"


def test_missing_doc_id_returns_400(client, test_session):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    resp = client.post("/api/documents", json={
        "project_id": sp.id, "content": "content"
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_FIELD"


def test_missing_content_returns_400(client, test_session):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    resp = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": f"{sp.id}/design"
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_FIELD"


def test_invalid_json_returns_400(client):
    resp = client.post(
        "/api/documents",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JSON"


def test_nonexistent_project_id_returns_error(client):
    resp = client.post("/api/documents", json={
        "project_id": "nonexistent-uuid",
        "doc_id": "nonexistent/design",
        "content": "# Content",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "UNAUTHORIZED"


def test_pushed_doc_readable_via_document_service(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    doc_id = f"{sp.id}/api"
    content = "# API Spec\n\nGET /users returns user list."

    client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": content
    })

    doc = test_session.query(Document).filter(Document.id == doc_id).first()
    assert doc is not None
    assert doc.latest_version == 1

    ver = test_session.query(DocumentVersion).filter(
        DocumentVersion.document_id == doc_id,
        DocumentVersion.version == 1,
    ).first()
    assert ver is not None
    assert ver.status == "published"


# ---------------------------------------------------------------------------
# base_version / fast-forward check
# ---------------------------------------------------------------------------


def test_push_with_matching_base_version_succeeds(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    doc_id = f"{sp.id}/requirement"

    # First push (no base_version needed)
    resp1 = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v1",
    })
    assert resp1.status_code == 200
    assert resp1.json()["version"] == 1

    # Second push with correct base_version
    resp2 = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v2", "base_version": 1,
    })
    assert resp2.status_code == 200
    assert resp2.json()["version"] == 2


def test_push_with_stale_base_version_returns_409(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    doc_id = f"{sp.id}/design"

    # Push v1 and v2
    client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v1",
    })
    client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v2",
    })

    # Push with stale base_version=1 (server is at v2)
    resp = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v3", "base_version": 1,
    })
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "VERSION_CONFLICT"
    assert data["details"]["server_version"] == 2
    assert data["details"]["base_version"] == 1


def test_push_with_base_version_on_new_doc_returns_409(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    doc_id = f"{sp.id}/api"

    # First push with base_version (doc doesn't exist yet)
    resp = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v1", "base_version": 0,
    })
    assert resp.status_code == 409
    assert resp.json()["error"] == "VERSION_CONFLICT"


def test_push_without_base_version_skips_check(client, test_session, tmp_path):
    space = _make_space(test_session)
    sp = _make_subproject(test_session, space.id)
    doc_id = f"{sp.id}/requirement"

    # Push v1
    client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v1",
    })

    # Push v2 without base_version (last writer wins)
    resp = client.post("/api/documents", json={
        "project_id": sp.id, "doc_id": doc_id, "content": "# v2 overwrite",
    })
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


# ---------------------------------------------------------------------------
# Archived space rejection
# ---------------------------------------------------------------------------


def test_archived_space_returns_400(client, test_session, tmp_path):
    space = ProjectSpace(
        id=str(uuid.uuid4()), name="archived", status="archived",
        created_at=datetime.now(timezone.utc),
    )
    test_session.add(space)
    test_session.flush()
    sp = _make_subproject(test_session, space.id)

    resp = client.post("/api/documents", json={
        "project_id": sp.id,
        "doc_id": f"{sp.id}/requirement",
        "content": "# Content",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "SPACE_ARCHIVED"
