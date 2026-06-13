"""
Unit and integration tests for FTS5 full-text search.

Covers:
  - ensure_fts_table idempotency
  - upsert_doc write/update
  - search: keywords, phrases, prefix, boolean, filters, isolation
  - error handling: empty results, invalid query
  - rebuild_fts_index_if_empty
  - DocumentService integration: push_document and publish_draft
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from doc_exchange.models import Base
from doc_exchange.models.entities import (
    Document,
    DocumentVersion,
    DocumentVersionContent,
    ProjectSpace,
    SubProject,
)
from doc_exchange.search.fts import (
    ensure_fts_table,
    rebuild_fts_index_if_empty,
    search,
    upsert_doc,
)
from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.errors import DocExchangeError
from doc_exchange.services.schemas import PushRequest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fts_engine():
    """In-memory SQLite engine with ORM tables AND FTS5 table."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    ensure_fts_table(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def fts_session(fts_engine):
    """Session that rolls back after each test."""
    connection = fts_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def space(fts_session):
    sp = ProjectSpace(
        id=str(uuid.uuid4()),
        name="test-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    fts_session.add(sp)
    fts_session.flush()
    return sp


@pytest.fixture()
def subproject(fts_session, space):
    sp = SubProject(
        id=str(uuid.uuid4()),
        project_space_id=space.id,
        name="backend-api",
        type="development",
        stage="design",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    fts_session.add(sp)
    fts_session.flush()
    return sp


def _make_doc_service(session, tmp_path, space_id):
    """Helper: build a DocumentService with minimal dependencies."""
    audit = AuditLogService(session)
    return DocumentService(
        db=session,
        docs_root=str(tmp_path),
        audit_log_service=audit,
    )


def _insert_published_doc(session, space_id, subproject_id, doc_type, content, doc_id=None):
    """Directly insert a published document into the DB (bypasses DocumentService)."""
    now = datetime.now(timezone.utc)
    if doc_id is None:
        doc_id = f"{subproject_id}/{doc_type}"

    doc = Document(
        id=doc_id,
        project_space_id=space_id,
        subproject_id=subproject_id,
        doc_type=doc_type,
        doc_variant=None,
        latest_version=1,
        created_at=now,
    )
    session.add(doc)
    session.flush()

    ver_id = str(uuid.uuid4())
    ver = DocumentVersion(
        id=ver_id,
        document_id=doc_id,
        project_space_id=space_id,
        version=1,
        content_hash="abc",
        pushed_by="test",
        status="published",
        is_milestone=False,
        pushed_at=now,
        published_at=now,
    )
    session.add(ver)

    content_rec = DocumentVersionContent(
        version_id=ver_id,
        project_space_id=space_id,
        content=content,
    )
    session.add(content_rec)
    session.flush()

    return doc


# ---------------------------------------------------------------------------
# ensure_fts_table
# ---------------------------------------------------------------------------


def test_ensure_fts_table_idempotent(fts_engine):
    """Calling ensure_fts_table twice must not raise."""
    ensure_fts_table(fts_engine)  # second call
    # no exception = pass


# ---------------------------------------------------------------------------
# upsert_doc
# ---------------------------------------------------------------------------


def test_upsert_doc_can_be_retrieved(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/requirement",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="User authentication must support OAuth2 and password login.",
    )
    results = search(fts_session, space.id, "authentication")
    assert len(results) == 1
    assert results[0]["doc_id"] == f"{subproject.id}/requirement"


def test_upsert_doc_update_replaces_old_content(fts_session, space, subproject):
    doc_id = f"{subproject.id}/requirement"

    upsert_doc(
        db=fts_session,
        doc_id=doc_id,
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="Old content about legacy password login.",
    )
    upsert_doc(
        db=fts_session,
        doc_id=doc_id,
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="New content about OAuth2 token refresh.",
    )

    # Old content no longer findable
    old_results = search(fts_session, space.id, "legacy")
    assert len(old_results) == 0

    # New content is findable
    new_results = search(fts_session, space.id, "OAuth2")
    assert len(new_results) == 1
    assert new_results[0]["doc_id"] == doc_id


# ---------------------------------------------------------------------------
# search — query syntax
# ---------------------------------------------------------------------------


def test_search_basic_keyword(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/design",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="design",
        content="The system uses Redis for caching session tokens.",
    )
    results = search(fts_session, space.id, "Redis")
    assert len(results) == 1


def test_search_phrase(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/api",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="api",
        content="POST /auth/login accepts user credentials and returns JWT token.",
    )
    # Phrase match
    results = search(fts_session, space.id, '"user credentials"')
    assert len(results) == 1

    # Non-matching phrase
    results_no = search(fts_session, space.id, '"user token"')
    assert len(results_no) == 0


def test_search_prefix(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/requirement",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="Authentication and authorization are handled by the auth service.",
    )
    results = search(fts_session, space.id, "auth*")
    assert len(results) == 1


def test_search_boolean_and_not(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/requirement",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="Authentication via OAuth2.",
    )
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/design",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="design",
        content="Authentication via legacy password system.",
    )
    # FTS5 boolean NOT syntax: "term NOT excluded_term" (no AND keyword before NOT)
    results = search(fts_session, space.id, "authentication NOT OAuth2")
    assert len(results) == 1
    assert "design" in results[0]["doc_id"]


# ---------------------------------------------------------------------------
# search — filters
# ---------------------------------------------------------------------------


def test_search_space_isolation(fts_session, fts_engine):
    """Documents in space A must not appear in results for space B."""
    now = datetime.now(timezone.utc)

    space_a = ProjectSpace(id=str(uuid.uuid4()), name="A", status="active", created_at=now)
    space_b = ProjectSpace(id=str(uuid.uuid4()), name="B", status="active", created_at=now)
    fts_session.add_all([space_a, space_b])
    fts_session.flush()

    upsert_doc(
        db=fts_session,
        doc_id="proj-a/requirement",
        project_space_id=space_a.id,
        subproject_id="proj-a",
        doc_type="requirement",
        content="Space A secret authentication token management.",
    )

    results_b = search(fts_session, space_b.id, "authentication")
    assert len(results_b) == 0

    results_a = search(fts_session, space_a.id, "authentication")
    assert len(results_a) == 1


def test_search_filter_by_doc_type(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/requirement",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="User authentication requirement.",
    )
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/design",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="design",
        content="Authentication design document.",
    )

    results = search(fts_session, space.id, "authentication", doc_type="requirement")
    assert len(results) == 1
    assert results[0]["doc_type"] == "requirement"


def test_search_filter_by_subproject_id(fts_session, space):
    now = datetime.now(timezone.utc)
    proj_a = SubProject(
        id=str(uuid.uuid4()), project_space_id=space.id,
        name="svc-a", type="development", stage="design",
        stage_updated_at=now, created_at=now,
    )
    proj_b = SubProject(
        id=str(uuid.uuid4()), project_space_id=space.id,
        name="svc-b", type="development", stage="design",
        stage_updated_at=now, created_at=now,
    )
    fts_session.add_all([proj_a, proj_b])
    fts_session.flush()

    upsert_doc(
        db=fts_session,
        doc_id=f"{proj_a.id}/requirement",
        project_space_id=space.id,
        subproject_id=proj_a.id,
        doc_type="requirement",
        content="Service A authentication flow.",
    )
    upsert_doc(
        db=fts_session,
        doc_id=f"{proj_b.id}/requirement",
        project_space_id=space.id,
        subproject_id=proj_b.id,
        doc_type="requirement",
        content="Service B authentication flow.",
    )

    results = search(fts_session, space.id, "authentication", subproject_id=proj_a.id)
    assert len(results) == 1
    assert results[0]["subproject_id"] == proj_a.id


def test_search_no_match_returns_empty(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/requirement",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="This document is about caching.",
    )
    results = search(fts_session, space.id, "authentication")
    assert results == []


def test_search_invalid_query_raises_error(fts_session, space):
    with pytest.raises(DocExchangeError) as exc_info:
        search(fts_session, space.id, "AND AND AND")
    assert exc_info.value.error_code == "INVALID_QUERY"


# ---------------------------------------------------------------------------
# search — snippet highlight
# ---------------------------------------------------------------------------


def test_search_snippet_contains_highlight_markers(fts_session, space, subproject):
    upsert_doc(
        db=fts_session,
        doc_id=f"{subproject.id}/requirement",
        project_space_id=space.id,
        subproject_id=subproject.id,
        doc_type="requirement",
        content="User authentication must support OAuth2 login.",
    )
    results = search(fts_session, space.id, "authentication")
    assert len(results) == 1
    snippet = results[0]["snippet"]
    assert ">>>" in snippet and "<<<" in snippet


# ---------------------------------------------------------------------------
# rebuild_fts_index_if_empty
# ---------------------------------------------------------------------------


def test_rebuild_fts_index_if_empty(fts_engine, fts_session, space, subproject):
    """After inserting published docs directly, rebuild should index them."""
    _insert_published_doc(
        fts_session,
        space.id,
        subproject.id,
        "requirement",
        "Rebuild test: distributed caching architecture.",
    )
    fts_session.flush()

    # Confirm FTS is empty before rebuild
    count_before = fts_session.execute(text("SELECT COUNT(*) FROM doc_fts")).fetchone()[0]
    assert count_before == 0

    # Commit so engine-level rebuild can see the data
    fts_session.commit()
    rebuild_fts_index_if_empty(fts_engine)

    # Now search should find it via a new session
    with fts_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT doc_id FROM doc_fts WHERE doc_fts MATCH 'caching'")
        ).fetchall()
    assert len(rows) == 1


def test_rebuild_fts_index_skips_if_not_empty(fts_engine):
    """rebuild_fts_index_if_empty must be a no-op when the table already has data."""
    # Insert directly via engine so it's immediately visible to rebuild
    with fts_engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO doc_fts(doc_id, project_space_id, subproject_id, doc_type, content)
            VALUES ('proj/requirement', 'space-x', 'proj', 'requirement', 'Already indexed content.')
        """))
        conn.commit()

    # Table is not empty → rebuild should be a no-op
    rebuild_fts_index_if_empty(fts_engine)

    with fts_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM doc_fts")).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# DocumentService integration
# ---------------------------------------------------------------------------


def test_push_published_document_is_searchable(fts_engine, tmp_path, space, subproject):
    """push_document with a regular project_id should update FTS index."""
    connection = fts_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    try:
        service = _make_doc_service(session, tmp_path, space.id)
        doc_id = f"{subproject.id}/requirement"
        req = PushRequest(
            doc_id=doc_id,
            content="Integration test: user authentication via JWT.",
            pushed_by=subproject.id,
            project_space_id=space.id,
        )
        result = service.push(req)
        assert result.status == "published"

        results = search(session, space.id, "JWT")
        assert len(results) == 1
        assert results[0]["doc_id"] == doc_id
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_push_draft_document_not_searchable(fts_engine, tmp_path, space, subproject):
    """push_document with pushed_by='agent:planner' creates draft — must NOT be in FTS."""
    connection = fts_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    try:
        service = _make_doc_service(session, tmp_path, space.id)
        doc_id = f"{subproject.id}/requirement"
        req = PushRequest(
            doc_id=doc_id,
            content="Draft content: secret architecture proposal.",
            pushed_by="agent:planner",
            project_space_id=space.id,
        )
        result = service.push(req)
        assert result.status == "draft"

        results = search(session, space.id, "architecture")
        assert len(results) == 0
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_publish_draft_makes_document_searchable(fts_engine, tmp_path, space, subproject):
    """publish_draft should update FTS so the content becomes searchable."""
    connection = fts_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    try:
        service = _make_doc_service(session, tmp_path, space.id)
        doc_id = f"{subproject.id}/requirement"

        # Push as draft
        req = PushRequest(
            doc_id=doc_id,
            content="Draft: event-driven microservice design with Kafka.",
            pushed_by="agent:planner",
            project_space_id=space.id,
        )
        push_result = service.push(req)
        assert push_result.status == "draft"

        # Not yet searchable
        assert search(session, space.id, "Kafka") == []

        # Publish
        service.publish_draft(doc_id=doc_id, version=push_result.version, project_space_id=space.id)

        # Now searchable
        results = search(session, space.id, "Kafka")
        assert len(results) == 1
        assert results[0]["doc_id"] == doc_id
    finally:
        session.close()
        transaction.rollback()
        connection.close()
