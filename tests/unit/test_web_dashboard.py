"""
Unit tests for the Web Dashboard routes.

Strategy:
  1. Create a minimal Starlette app using a MockMCP shim that collects routes
     registered by register_web_routes.
  2. Use an in-memory SQLite DB (with FTS5) so the tests are fully isolated.
  3. Wire a real ServiceContainer + ToolHandler pointing at the test DB.
  4. Test all API endpoints and the dashboard HTML page.

Requirements covered: 1.1-1.8, 2.1-2.3, 5.2
"""

import json
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from agent_nexus.mcp.dependencies import ServiceContainer
from agent_nexus.mcp.tools import ToolHandler
from agent_nexus.models import Base
from agent_nexus.models.entities import ProjectSpace, SubProject
from agent_nexus.search.fts import ensure_fts_table, upsert_doc
from agent_nexus.services.schemas import PushRequest
from agent_nexus.web.routes import register_web_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockMCP:
    """Minimal stand-in for FastMCP — collects custom_route registrations."""

    def __init__(self):
        self._routes: list[tuple[str, list[str], object]] = []

    def custom_route(self, path: str, methods: list[str]):
        def decorator(fn):
            self._routes.append((path, methods, fn))
            return fn

        return decorator

    def build_starlette_app(self) -> Starlette:
        routes = [
            Route(path, endpoint=fn, methods=methods)
            for path, methods, fn in self._routes
        ]
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


def _make_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    SessionFactory = sessionmaker(bind=connection)
    session = SessionFactory()
    return session, connection, transaction


def _make_test_client(session, tmp_path, llm_client=None) -> TestClient:
    """Build a Starlette TestClient wired to the test DB session."""
    container = ServiceContainer(db_session=session, docs_root=str(tmp_path))
    if llm_client is not None:
        container.planner_service._llm = llm_client

    handler = ToolHandler(container)

    def get_handler():
        return handler, session

    mock_mcp = _MockMCP()
    register_web_routes(mock_mcp, get_handler=get_handler)

    app = mock_mcp.build_starlette_app()
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_space(session, name: str = "test-space") -> ProjectSpace:
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name=name,
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()
    return space


def _make_subproject(session, space_id: str, name: str = "svc-a") -> SubProject:
    sp = SubProject(
        id=str(uuid.uuid4()),
        project_space_id=space_id,
        name=name,
        type="development",
        stage="design",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(sp)
    session.flush()
    return sp


def _push_doc(session, tmp_path, space_id: str, project_id: str, doc_id: str, content: str, pushed_by: str | None = None) -> int:
    """Push a published document using DocumentService; returns version number."""
    from agent_nexus.services.audit_log_service import AuditLogService
    from agent_nexus.services.document_service import DocumentService

    audit = AuditLogService(session)
    svc = DocumentService(db=session, docs_root=str(tmp_path), audit_log_service=audit)
    req = PushRequest(
        doc_id=doc_id,
        content=content,
        pushed_by=pushed_by or project_id,
        project_space_id=space_id,
    )
    result = svc.push(req)
    return result.version


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine():
    eng = _make_fts_engine()
    yield eng
    eng.dispose()


@pytest.fixture()
def test_session(db_engine):
    session, connection, transaction = _make_session(db_engine)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(test_session, tmp_path):
    return _make_test_client(test_session, tmp_path)


@pytest.fixture()
def space(test_session):
    return _make_space(test_session)


@pytest.fixture()
def subproject(test_session, space):
    return _make_subproject(test_session, space.id)


# ---------------------------------------------------------------------------
# 1. GET /  →  HTML 200 containing expected text
# ---------------------------------------------------------------------------


def test_dashboard_returns_html_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "text/html" in content_type
    # The template should contain "Doc House" or "AgentNexus" or "Dashboard"
    body = resp.text
    assert any(kw in body for kw in ("Doc House", "AgentNexus", "Dashboard", "dashboard")), (
        f"Expected dashboard keyword in HTML body, got: {body[:200]}"
    )


# ---------------------------------------------------------------------------
# 2. GET /api/spaces  →  returns list of spaces
# ---------------------------------------------------------------------------


def test_api_spaces_returns_all_spaces(test_session, tmp_path):
    sp1 = _make_space(test_session, "alpha")
    sp2 = _make_space(test_session, "beta")
    c = _make_test_client(test_session, tmp_path)

    resp = c.get("/api/spaces")
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["space_id"] for item in data}
    assert sp1.id in ids
    assert sp2.id in ids


def test_api_spaces_empty_returns_empty_list(test_session, tmp_path):
    c = _make_test_client(test_session, tmp_path)
    resp = c.get("/api/spaces")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 3. GET /api/spaces/{sid}/projects  →  isolation between spaces
# ---------------------------------------------------------------------------


def test_api_projects_returns_projects_in_space(test_session, tmp_path, space):
    proj = _make_subproject(test_session, space.id, "svc-backend")
    # Project in a different space
    other_space = _make_space(test_session, "other")
    _make_subproject(test_session, other_space.id, "svc-other")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/spaces/{space.id}/projects")
    assert resp.status_code == 200
    data = resp.json()
    project_ids = [p["project_id"] for p in data]
    assert proj.id in project_ids
    # The other project must NOT appear
    for p in data:
        assert p["project_id"] != other_space.id


def test_api_projects_space_isolation(test_session, tmp_path):
    space_a = _make_space(test_session, "space-a")
    space_b = _make_space(test_session, "space-b")
    proj_a = _make_subproject(test_session, space_a.id, "proj-a")
    _make_subproject(test_session, space_b.id, "proj-b")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/spaces/{space_a.id}/projects")
    assert resp.status_code == 200
    ids = [p["project_id"] for p in resp.json()]
    assert proj_a.id in ids
    # proj-b must not appear in space-a results
    resp_b = c.get(f"/api/spaces/{space_b.id}/projects")
    ids_b = [p["project_id"] for p in resp_b.json()]
    assert proj_a.id not in ids_b


# ---------------------------------------------------------------------------
# 4. GET /api/projects/{pid}/documents  →  returns document list
# ---------------------------------------------------------------------------


def test_api_documents_lists_pushed_documents(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/requirement"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "# Requirement\nContent.")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/projects/{subproject.id}/documents")
    assert resp.status_code == 200
    data = resp.json()
    doc_ids = [d["doc_id"] for d in data]
    assert doc_id in doc_ids


def test_api_documents_empty_for_new_project(test_session, tmp_path, space, subproject):
    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/projects/{subproject.id}/documents")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 5. GET /api/documents/{doc_id}?space_id=  →  content / 400 / 404
# ---------------------------------------------------------------------------


def test_api_document_returns_content(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/design"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "# Design\nArch overview.")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/{doc_id}?space_id={space.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["doc_id"] == doc_id
    assert "Design" in data["content"]


def test_api_document_missing_space_id_returns_400(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/design"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "content")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_PARAM"


def test_api_document_nonexistent_returns_404(test_session, tmp_path, space):
    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/nonexistent/doc?space_id={space.id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. GET /api/documents/{doc_id}/versions?space_id=
# ---------------------------------------------------------------------------


def test_api_versions_returns_version_list(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/requirement"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "v1 content")
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "v2 content")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/{doc_id}/versions?space_id={space.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    versions = sorted(v["version"] for v in data)
    assert versions == [1, 2]


def test_api_versions_missing_space_id_returns_400(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/requirement"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "content")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/{doc_id}/versions")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 7. GET /api/documents/{doc_id}/diff?space_id=&from=1&to=2
# ---------------------------------------------------------------------------


def test_api_diff_returns_unified_diff(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/design"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "line one\nline two\n")
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "line one\nline three\n")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/{doc_id}/diff?space_id={space.id}&from=1&to=2")
    assert resp.status_code == 200
    data = resp.json()
    assert "diff" in data
    assert data["from"] == 1
    assert data["to"] == 2
    # Diff should mention the changed line
    assert "three" in data["diff"] or "-line two" in data["diff"]


def test_api_diff_missing_space_id_returns_400(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/design"
    _push_doc(test_session, tmp_path, space.id, subproject.id, doc_id, "content")

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/documents/{doc_id}/diff?from=1&to=1")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 8. GET /api/spaces/{sid}/search?q=
# ---------------------------------------------------------------------------


def test_api_search_returns_results(test_session, tmp_path, space, subproject):
    doc_id = f"{subproject.id}/requirement"
    _push_doc(
        test_session,
        tmp_path,
        space.id,
        subproject.id,
        doc_id,
        "User authentication must support OAuth2 and password login.",
    )
    # Also upsert into FTS (push_document does this for published docs)
    upsert_doc(
        db=test_session,
        doc_id=doc_id,
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="User authentication must support OAuth2 and password login.",
    )

    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/spaces/{space.id}/search?q=OAuth2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    doc_ids = [item["doc_id"] for item in data]
    assert doc_id in doc_ids


def test_api_search_missing_q_returns_400(test_session, tmp_path, space):
    c = _make_test_client(test_session, tmp_path)
    resp = c.get(f"/api/spaces/{space.id}/search")
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_PARAM"


def test_api_search_space_isolation(test_session, tmp_path):
    space_a = _make_space(test_session, "space-a")
    space_b = _make_space(test_session, "space-b")
    proj_a = _make_subproject(test_session, space_a.id)

    doc_id = f"{proj_a.id}/requirement"
    upsert_doc(
        db=test_session,
        doc_id=doc_id,
        project_space_id=space_a.id,
        subproject_id=proj_a.id,
        doc_type="requirement",
        content="Exclusive content only in space A.",
    )

    c = _make_test_client(test_session, tmp_path)
    # Should find in space A
    resp_a = c.get(f"/api/spaces/{space_a.id}/search?q=Exclusive")
    assert resp_a.status_code == 200
    assert len(resp_a.json()) >= 1

    # Must not find in space B
    resp_b = c.get(f"/api/spaces/{space_b.id}/search?q=Exclusive")
    assert resp_b.status_code == 200
    assert resp_b.json() == []


# ---------------------------------------------------------------------------
# 9. POST /api/chat  →  SSE stream
# ---------------------------------------------------------------------------


def _parse_sse(body: str) -> list[dict]:
    """Parse SSE text into list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in body.splitlines():
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            raw = line[len("data:"):].strip()
            try:
                current["data"] = json.loads(raw)
            except json.JSONDecodeError:
                current["data"] = raw
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def test_api_chat_no_llm_returns_sse_error_event(test_session, tmp_path, space):
    # Default client has no LLM configured (make_llm_client returns None
    # when env vars are absent)
    c = _make_test_client(test_session, tmp_path, llm_client=None)
    resp = c.post(
        "/api/chat",
        json={"space_id": space.id, "question": "What is OAuth2?"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    events = _parse_sse(resp.text)
    # Find the error event
    error_events = [e for e in events if e.get("event") == "error"]
    assert len(error_events) >= 1
    assert error_events[0]["data"]["error"] == "LLM_NOT_CONFIGURED"


def test_api_chat_with_mock_llm_returns_token_event(test_session, tmp_path, space):
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="Hello! This is a mock answer.")

    c = _make_test_client(test_session, tmp_path, llm_client=mock_llm)
    resp = c.post(
        "/api/chat",
        json={"space_id": space.id, "question": "Hello?"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)

    token_events = [e for e in events if e.get("data", {}) and "token" in e.get("data", {})]
    assert len(token_events) >= 1
    assert "mock answer" in token_events[0]["data"]["token"]

    # Should also have a "done" event
    done_events = [e for e in events if e.get("event") == "done"]
    assert len(done_events) >= 1


def test_api_chat_missing_space_id_returns_400(test_session, tmp_path):
    c = _make_test_client(test_session, tmp_path)
    resp = c.post("/api/chat", json={"question": "No space here"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_PARAM"


def test_api_chat_missing_question_returns_400(test_session, tmp_path, space):
    c = _make_test_client(test_session, tmp_path)
    resp = c.post("/api/chat", json={"space_id": space.id})
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_PARAM"


# ---------------------------------------------------------------------------
# 10. POST /api/plan  →  proposal JSON
# ---------------------------------------------------------------------------


def test_api_plan_no_llm_returns_error(test_session, tmp_path, space):
    c = _make_test_client(test_session, tmp_path, llm_client=None)
    resp = c.post(
        "/api/plan",
        json={"space_id": space.id, "description": "Build a payment service"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("error") == "LLM_NOT_CONFIGURED"


def test_api_plan_with_mock_llm_returns_proposal(test_session, tmp_path, space):
    proposals = [{"name": "payment-svc", "type": "development", "suggested_docs": ["requirement"]}]
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=json.dumps(proposals))

    c = _make_test_client(test_session, tmp_path, llm_client=mock_llm)
    resp = c.post(
        "/api/plan",
        json={"space_id": space.id, "description": "Build a payment service"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "proposals" in data
    assert data["proposals"][0]["name"] == "payment-svc"
    assert data["description"] == "Build a payment service"


def test_api_plan_does_not_persist_to_db(test_session, tmp_path, space):
    """plan endpoint must NOT create SubProjects or documents in the DB."""
    from agent_nexus.models.entities import SubProject

    proposals = [{"name": "ephemeral-svc", "type": "development", "suggested_docs": []}]
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=json.dumps(proposals))

    c = _make_test_client(test_session, tmp_path, llm_client=mock_llm)
    c.post(
        "/api/plan",
        json={"space_id": space.id, "description": "Ephemeral plan"},
    )

    # No new SubProject should have been created
    count = test_session.query(SubProject).filter(SubProject.name == "ephemeral-svc").count()
    assert count == 0


def test_api_plan_missing_space_id_returns_400(test_session, tmp_path):
    c = _make_test_client(test_session, tmp_path)
    resp = c.post("/api/plan", json={"description": "no space"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_PARAM"


def test_api_plan_missing_description_returns_400(test_session, tmp_path, space):
    c = _make_test_client(test_session, tmp_path)
    resp = c.post("/api/plan", json={"space_id": space.id})
    assert resp.status_code == 400
    assert resp.json()["error"] == "MISSING_PARAM"
