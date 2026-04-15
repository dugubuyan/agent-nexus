"""
Shared test fixtures for the Doc Exchange Center test suite.

Provides:
- in-memory SQLite engine and session (for fast unit/property tests)
- temporary file system directory (for file-system-related tests)
"""

import os
import tempfile
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from doc_exchange.models import Base, ProjectSpace


# ---------------------------------------------------------------------------
# Synchronous in-memory SQLite fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def engine():
    """Create a session-scoped in-memory SQLite engine with all tables."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # Enable foreign key enforcement for SQLite
    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    """Provide a transactional database session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection)
    session = SessionLocal()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Default Project_Space fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_space(db_session: Session) -> ProjectSpace:
    """Create and return a default active ProjectSpace for tests."""
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="default",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(space)
    db_session.flush()
    return space


# ---------------------------------------------------------------------------
# Temporary file system fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_docs_root(tmp_path):
    """Provide a temporary directory that mimics the /docs/ root."""
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    return str(docs_root)


# ---------------------------------------------------------------------------
# Async in-memory SQLite fixtures (for async service tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def async_db_url():
    """Return an async SQLite in-memory URL (file-based so multiple connections work)."""
    # Use a named temp file so aiosqlite can share it across connections
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    yield f"sqlite+aiosqlite:///{tmp.name}"
    os.unlink(tmp.name)
