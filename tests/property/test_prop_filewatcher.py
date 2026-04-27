# Feature: doc-exchange-center, Property 30: FileWatcher 防抖与去重
# Feature: doc-exchange-center, Property 31: FileWatcher 推送标识
"""
Property-based tests for FileWatcherService debounce, dedup, and push metadata.

**Validates: Requirements 2.3, 11.1, 11.2, 3.4**
"""

import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.models import Base, ProjectSpace
from doc_exchange.models.entities import DocumentVersion
from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.file_watcher_service import FileWatcherService
from doc_exchange.services.schemas import PushRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_engine():
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
    return engine


def _make_session(engine):
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _make_space(session):
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="prop-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()
    return space


def _make_doc_service(session, docs_root):
    audit = AuditLogService(session)
    return DocumentService(db=session, docs_root=docs_root, audit_log_service=audit)


def _make_watcher(docs_root, doc_service, space_id="default"):
    return FileWatcherService(
        docs_root=docs_root,
        document_service=doc_service,
        default_space_id=space_id,
    )


def _write_file(docs_root, space_id, subproject_id, filename, content):
    """Write a .md file under docs_root/{space_id}/docs/{subproject_id}/{filename}."""
    dir_path = os.path.join(docs_root, space_id, "docs", subproject_id)
    os.makedirs(dir_path, exist_ok=True)
    file_path = os.path.join(dir_path, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return file_path


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid subproject IDs: simple alphanumeric + dash
valid_subproject_id = st.from_regex(r"[a-z][a-z0-9\-]{2,15}", fullmatch=True)

# Valid doc filenames
valid_doc_filename = st.sampled_from(
    ["requirement.md", "design.md", "api.md", "task.md",
     "config_dev.md", "config_test.md", "config_prod.md"]
)

# Content: non-empty markdown-like text
valid_content = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters="# \n-_.,!?",
    ),
    min_size=1,
    max_size=200,
)

# Number of rapid events (2–10)
rapid_event_count = st.integers(min_value=2, max_value=10)


# ---------------------------------------------------------------------------
# Property 30: FileWatcher 防抖与去重
# ---------------------------------------------------------------------------


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    subproject_id=valid_subproject_id,
    filename=valid_doc_filename,
    content=valid_content,
    n_events=rapid_event_count,
)
def test_prop_p30_debounce_multiple_events_trigger_one_push(
    subproject_id, filename, content, n_events
):
    """
    # Feature: doc-exchange-center, Property 30: FileWatcher 防抖与去重

    For any N rapid file-change events (N >= 2) on the same file within 500ms,
    FileWatcherService should trigger DocumentService.push() at most once.

    We verify by calling _on_file_changed() N times rapidly and checking that
    only one debounce timer is active (the timer is reset each time).

    **Validates: Requirements 2.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        docs_root = os.path.join(tmp_dir, "docs")
        os.makedirs(docs_root, exist_ok=True)

        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(docs_root, doc_service)

        space_id = "space1"
        file_path = _write_file(docs_root, space_id, subproject_id, filename, content)

        # Fire N rapid events on the same file
        for _ in range(n_events):
            watcher._on_file_changed(file_path)

        # After N rapid events, there should be exactly ONE active timer for this file
        with watcher._lock:
            assert file_path in watcher._debounce_timers, (
                "Expected a debounce timer to be active after rapid events"
            )
            active_timer_count = sum(
                1 for t in watcher._debounce_timers.values() if t.is_alive()
            )
            assert active_timer_count == 1, (
                f"Expected exactly 1 active debounce timer, got {active_timer_count}"
            )

        # Cancel the timer to avoid side effects
        with watcher._lock:
            for t in watcher._debounce_timers.values():
                t.cancel()
            watcher._debounce_timers.clear()

        # push() should NOT have been called yet (timer hasn't fired)
        doc_service.push.assert_not_called()


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    filename=valid_doc_filename,
    content=valid_content,
)
def test_prop_p30_hash_dedup_skips_push_when_content_unchanged(
    subproject_id, filename, content
):
    """
    # Feature: doc-exchange-center, Property 30: FileWatcher 防抖与去重 (hash dedup)

    If the file content's SHA-256 hash matches the latest version in the DB,
    _process_file() must NOT call DocumentService.push().

    **Validates: Requirements 2.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        docs_root = os.path.join(tmp_dir, "docs")
        os.makedirs(docs_root, exist_ok=True)

        content_hash = _sha256(content)

        doc_service = MagicMock()
        # Simulate: latest hash in DB matches the file content
        doc_service.get_latest_hash.return_value = content_hash

        watcher = _make_watcher(docs_root, doc_service)

        space_id = "space1"
        file_path = _write_file(docs_root, space_id, subproject_id, filename, content)

        # Call _process_file() directly (bypassing debounce timer)
        watcher._process_file(file_path)

        # push() must NOT be called when hash matches
        doc_service.push.assert_not_called()


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    filename=valid_doc_filename,
    content=valid_content,
)
def test_prop_p30_push_called_when_content_changed(subproject_id, filename, content):
    """
    # Feature: doc-exchange-center, Property 30: FileWatcher 防抖与去重 (changed content)

    If the file content's SHA-256 hash differs from the latest version in the DB
    (or no version exists), _process_file() MUST call DocumentService.push() exactly once.

    **Validates: Requirements 2.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        docs_root = os.path.join(tmp_dir, "docs")
        os.makedirs(docs_root, exist_ok=True)

        doc_service = MagicMock()
        # Simulate: no existing version (new document)
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(docs_root, doc_service)

        space_id = "space1"
        file_path = _write_file(docs_root, space_id, subproject_id, filename, content)

        watcher._process_file(file_path)

        # push() must be called exactly once
        doc_service.push.assert_called_once()


# ---------------------------------------------------------------------------
# Property 31: FileWatcher 推送标识
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    filename=valid_doc_filename,
    content=valid_content,
)
def test_prop_p31_pushed_by_is_system_llm(subproject_id, filename, content):
    """
    # Feature: doc-exchange-center, Property 31: FileWatcher 推送标识

    For any document push triggered by FileWatcherService via _process_file(),
    the PushRequest passed to DocumentService.push() must have pushed_by="system_llm".

    **Validates: Requirements 11.1, 11.2, 3.4**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        docs_root = os.path.join(tmp_dir, "docs")
        os.makedirs(docs_root, exist_ok=True)

        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(docs_root, doc_service)

        space_id = "space1"
        file_path = _write_file(docs_root, space_id, subproject_id, filename, content)

        watcher._process_file(file_path)

        doc_service.push.assert_called_once()
        push_req: PushRequest = doc_service.push.call_args[0][0]

        assert push_req.pushed_by == "system_llm", (
            f"Expected pushed_by='system_llm', got {push_req.pushed_by!r}"
        )


@settings(max_examples=100)
@given(
    subproject_id=valid_subproject_id,
    filename=valid_doc_filename,
    content=valid_content,
)
def test_prop_p31_real_push_produces_draft_status(subproject_id, filename, content):
    """
    # Feature: doc-exchange-center, Property 31: FileWatcher 推送标识 (real DB)

    Using a real DocumentService backed by in-memory SQLite, verify that
    a push triggered by FileWatcherService produces a DocumentVersion with
    status="draft" and pushed_by="system_llm".

    **Validates: Requirements 11.1, 11.2, 3.4**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        docs_root = os.path.join(tmp_dir, "docs")
        os.makedirs(docs_root, exist_ok=True)

        engine = _make_engine()
        session = _make_session(engine)

        try:
            space = _make_space(session)
            doc_service = _make_doc_service(session, docs_root)
            watcher = _make_watcher(docs_root, doc_service, space_id=space.id)

            space_id = space.id
            file_path = _write_file(docs_root, space_id, subproject_id, filename, content)

            # Trigger _process_file() directly
            watcher._process_file(file_path)

            # Determine expected doc_id from filename
            doc_id, parsed_space_id = watcher._parse_path(file_path)
            assert doc_id is not None, f"Could not parse doc_id from {file_path}"
            assert parsed_space_id == space_id

            # Query the resulting DocumentVersion
            doc_version = (
                session.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == doc_id,
                    DocumentVersion.project_space_id == space_id,
                    DocumentVersion.version == 1,
                )
                .first()
            )

            assert doc_version is not None, (
                f"Expected a DocumentVersion to be created for doc_id={doc_id!r}"
            )
            assert doc_version.pushed_by == "system_llm", (
                f"Expected pushed_by='system_llm', got {doc_version.pushed_by!r}"
            )
            assert doc_version.status == "draft", (
                f"Expected status='draft', got {doc_version.status!r}"
            )

        finally:
            session.close()
            engine.dispose()
