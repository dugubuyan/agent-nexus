"""
Unit tests for DocumentService.

Covers:
- push() with valid doc_id returns version 1
- push() increments version on second push
- push() with same content returns CONTENT_UNCHANGED
- push() with invalid doc_id returns INVALID_DOC_ID
- push() config type without stage returns INVALID_STAGE
- push() with system_llm sets status=draft
- push() with external agent sets status=published
- push() writes file to correct path
- get() returns latest version content
- get() with specific version returns that version
- get() with non-existent doc_id returns DOC_NOT_FOUND
- get() with non-existent version returns VERSION_NOT_FOUND
- list_versions() returns all versions
- get_latest_hash() returns hash or None
"""

import hashlib
import os

import pytest

from doc_exchange.services.audit_log_service import AuditLogService
from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.errors import DocExchangeError
from doc_exchange.services.schemas import PushRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(db_session, tmp_docs_root):
    audit = AuditLogService(db=db_session)
    return DocumentService(db=db_session, docs_root=tmp_docs_root, audit_log_service=audit)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _push(svc, doc_id, content, pushed_by="agent-1", project_space_id="space-1", metadata=None):
    req = PushRequest(
        doc_id=doc_id,
        content=content,
        pushed_by=pushed_by,
        project_space_id=project_space_id,
        metadata=metadata or {},
    )
    return svc.push(req)


# ---------------------------------------------------------------------------
# push() — basic version tracking
# ---------------------------------------------------------------------------


def test_push_valid_doc_id_returns_version_1(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    result = _push(svc, "sub1/requirement", "# Hello", project_space_id=default_space.id)
    assert result.version == 1
    assert result.doc_id == "sub1/requirement"


def test_push_increments_version_on_second_push(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/design", "# v1", project_space_id=default_space.id)
    result = _push(svc, "sub1/design", "# v2", project_space_id=default_space.id)
    assert result.version == 2


def test_push_same_content_returns_content_unchanged(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/api", "# API", project_space_id=default_space.id)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "sub1/api", "# API", project_space_id=default_space.id)
    assert exc_info.value.error_code == "CONTENT_UNCHANGED"


# ---------------------------------------------------------------------------
# push() — doc_id validation
# ---------------------------------------------------------------------------


def test_push_empty_doc_id_returns_invalid_doc_id(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "", "content", project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_DOC_ID"


def test_push_invalid_doc_type_returns_invalid_doc_id(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "sub1/unknown_type", "content", project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_DOC_ID"


def test_push_too_many_parts_returns_invalid_doc_id(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "sub1/requirement/extra/part", "content", project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_DOC_ID"


def test_push_single_part_returns_invalid_doc_id(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "requirement", "content", project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_DOC_ID"


def test_push_non_config_with_variant_succeeds(db_session, default_space, tmp_docs_root):
    """New design: any doc_type can have a variant."""
    svc = _make_service(db_session, tmp_docs_root)
    result = _push(svc, "sub1/requirement/v2", "content", project_space_id=default_space.id)
    assert result.version == 1


# ---------------------------------------------------------------------------
# push() — config variant validation
# ---------------------------------------------------------------------------


def test_push_config_without_variant_returns_invalid_doc_id(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "sub1/config", "content", project_space_id=default_space.id, metadata={})
    assert exc_info.value.error_code == "INVALID_DOC_ID"


def test_push_config_with_invalid_variant_returns_invalid_doc_id(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        _push(svc, "sub1/config/staging", "content", project_space_id=default_space.id)
    assert exc_info.value.error_code == "INVALID_DOC_ID"


def test_push_config_with_valid_variant_in_doc_id_succeeds(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    result = _push(svc, "sub1/config/dev", "# Config", project_space_id=default_space.id)
    assert result.version == 1


def test_push_config_with_stage_in_doc_id_succeeds(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    result = _push(svc, "sub1/config/prod", "# Prod Config", project_space_id=default_space.id)
    assert result.version == 1


# ---------------------------------------------------------------------------
# push() — status (draft vs published)
# ---------------------------------------------------------------------------


def test_push_system_llm_sets_status_draft(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    result = _push(svc, "sub1/design", "# Draft", pushed_by="system_llm", project_space_id=default_space.id)
    assert result.status == "draft"


def test_push_external_agent_sets_status_published(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    result = _push(svc, "sub1/design", "# Published", pushed_by="agent-42", project_space_id=default_space.id)
    assert result.status == "published"


# ---------------------------------------------------------------------------
# push() — file system
# ---------------------------------------------------------------------------


def test_push_writes_file_to_correct_path(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    content = "# Requirement doc"
    _push(svc, "sub1/requirement", content, project_space_id=default_space.id)

    expected_path = os.path.join(tmp_docs_root, default_space.id, "sub1", "requirement.md")
    assert os.path.exists(expected_path)
    with open(expected_path, "r", encoding="utf-8") as f:
        assert f.read() == content


def test_push_config_writes_file_with_stage_suffix(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    content = "# Dev config"
    _push(svc, "sub1/config/dev", content, project_space_id=default_space.id)

    expected_path = os.path.join(tmp_docs_root, default_space.id, "sub1", "config_dev.md")
    assert os.path.exists(expected_path)
    with open(expected_path, "r", encoding="utf-8") as f:
        assert f.read() == content


def test_push_updates_file_on_second_push(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/api", "# v1", project_space_id=default_space.id)
    _push(svc, "sub1/api", "# v2", project_space_id=default_space.id)

    expected_path = os.path.join(tmp_docs_root, default_space.id, "sub1", "api.md")
    with open(expected_path, "r", encoding="utf-8") as f:
        assert f.read() == "# v2"


# ---------------------------------------------------------------------------
# get() — basic retrieval
# ---------------------------------------------------------------------------


def test_get_returns_latest_version_content(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/requirement", "# v1", project_space_id=default_space.id)
    _push(svc, "sub1/requirement", "# v2", project_space_id=default_space.id)

    result = svc.get("sub1/requirement", default_space.id)
    assert result.content == "# v2"
    assert result.version == 2


def test_get_with_specific_version_returns_that_version(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/design", "# v1", project_space_id=default_space.id)
    _push(svc, "sub1/design", "# v2", project_space_id=default_space.id)

    result = svc.get("sub1/design", default_space.id, version=1)
    assert result.content == "# v1"
    assert result.version == 1


def test_get_returns_correct_metadata(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/task", "# Task", pushed_by="agent-99", project_space_id=default_space.id)

    result = svc.get("sub1/task", default_space.id)
    assert result.pushed_by == "agent-99"
    assert result.doc_id == "sub1/task"
    assert result.pushed_at is not None


# ---------------------------------------------------------------------------
# get() — error cases
# ---------------------------------------------------------------------------


def test_get_nonexistent_doc_id_returns_doc_not_found(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.get("sub1/requirement", default_space.id)
    assert exc_info.value.error_code == "DOC_NOT_FOUND"


def test_get_nonexistent_version_returns_version_not_found(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/api", "# v1", project_space_id=default_space.id)

    with pytest.raises(DocExchangeError) as exc_info:
        svc.get("sub1/api", default_space.id, version=99)
    assert exc_info.value.error_code == "VERSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# list_versions()
# ---------------------------------------------------------------------------


def test_list_versions_returns_all_versions(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/design", "# v1", project_space_id=default_space.id)
    _push(svc, "sub1/design", "# v2", project_space_id=default_space.id)
    _push(svc, "sub1/design", "# v3", project_space_id=default_space.id)

    versions = svc.list_versions("sub1/design", default_space.id)
    assert len(versions) == 3
    assert [v.version for v in versions] == [1, 2, 3]


def test_list_versions_nonexistent_doc_returns_doc_not_found(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.list_versions("sub1/requirement", default_space.id)
    assert exc_info.value.error_code == "DOC_NOT_FOUND"


def test_list_versions_contains_pushed_by_and_status(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/api", "# v1", pushed_by="system_llm", project_space_id=default_space.id)
    _push(svc, "sub1/api", "# v2", pushed_by="agent-1", project_space_id=default_space.id)

    versions = svc.list_versions("sub1/api", default_space.id)
    assert versions[0].pushed_by == "system_llm"
    assert versions[0].status == "draft"
    assert versions[1].pushed_by == "agent-1"
    assert versions[1].status == "published"


# ---------------------------------------------------------------------------
# get_latest_hash()
# ---------------------------------------------------------------------------


def test_get_latest_hash_returns_none_for_nonexistent_doc(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    result = svc.get_latest_hash("sub1/requirement", default_space.id)
    assert result is None


def test_get_latest_hash_returns_hash_after_push(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    content = "# Hello World"
    _push(svc, "sub1/requirement", content, project_space_id=default_space.id)

    result = svc.get_latest_hash("sub1/requirement", default_space.id)
    assert result == _sha256(content)


def test_get_latest_hash_updates_after_second_push(db_session, default_space, tmp_docs_root):
    svc = _make_service(db_session, tmp_docs_root)
    _push(svc, "sub1/design", "# v1", project_space_id=default_space.id)
    _push(svc, "sub1/design", "# v2", project_space_id=default_space.id)

    result = svc.get_latest_hash("sub1/design", default_space.id)
    assert result == _sha256("# v2")


# ---------------------------------------------------------------------------
# Task 16.6: Concurrent push — no data races, unique monotonically increasing versions
# ---------------------------------------------------------------------------


def test_concurrent_pushes_produce_unique_monotonic_versions(engine, tmp_docs_root):
    """
    Simulate N threads each pushing different content to the same doc_id.
    After all threads complete:
      - All returned version numbers must be unique
      - Version numbers must form the set {1, 2, ..., N}
      - No data races (no exceptions, no duplicate versions in DB)

    Covers Requirement 8.5: concurrent requests must not produce data races.
    """
    import threading
    import tempfile
    from sqlalchemy import create_engine as _create_engine, event as sa_event
    from sqlalchemy.orm import sessionmaker
    from doc_exchange.models import Base
    from doc_exchange.models.entities import ProjectSpace, DocumentVersion, Document
    from datetime import datetime, timezone
    import uuid

    N = 5  # number of concurrent threads

    # Use a file-based SQLite so multiple connections share the same DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conc_engine = _create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @sa_event.listens_for(conc_engine, "connect")
        def set_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        Base.metadata.create_all(conc_engine)
        SessionFactory = sessionmaker(bind=conc_engine)

        # Create a shared space
        space_id = str(uuid.uuid4())
        setup_session = SessionFactory()
        space = ProjectSpace(
            id=space_id,
            name="concurrent-test-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        setup_session.add(space)
        setup_session.commit()
        setup_session.close()

        doc_id = "concurrent-sub/design"
        results = []
        errors = []
        lock = threading.Lock()

        def push_thread(i: int):
            session = SessionFactory()
            try:
                audit = AuditLogService(db=session)
                svc = DocumentService(db=session, docs_root=tmp_docs_root, audit_log_service=audit)
                req = PushRequest(
                    doc_id=doc_id,
                    content=f"# Version from thread {i}\n\nUnique content {i}",
                    pushed_by=f"agent-{i}",
                    project_space_id=space_id,
                    metadata={},
                )
                result = svc.push(req)
                session.commit()
                with lock:
                    results.append(result.version)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                session.close()

        threads = [threading.Thread(target=push_thread, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should have occurred
        assert errors == [], f"Concurrent push errors: {errors}"

        # All N pushes must have succeeded
        assert len(results) == N, f"Expected {N} results, got {len(results)}: {results}"

        # All version numbers must be unique
        assert len(set(results)) == N, f"Duplicate versions found: {results}"

        # Version numbers must be exactly 1..N
        assert sorted(results) == list(range(1, N + 1)), f"Versions not monotonically 1..N: {sorted(results)}"

        # Verify DB state: document's latest_version == N
        verify_session = SessionFactory()
        try:
            doc = verify_session.query(Document).filter(
                Document.id == doc_id,
                Document.project_space_id == space_id,
            ).first()
            assert doc is not None
            assert doc.latest_version == N

            # All N version records must exist in DB
            versions_in_db = verify_session.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.project_space_id == space_id,
            ).all()
            assert len(versions_in_db) == N
            db_version_nums = sorted(v.version for v in versions_in_db)
            assert db_version_nums == list(range(1, N + 1))
        finally:
            verify_session.close()

        conc_engine.dispose()
    finally:
        import os as _os
        if _os.path.exists(db_path):
            _os.unlink(db_path)


def test_concurrent_pushes_to_different_docs_do_not_interfere(tmp_docs_root):
    """
    Concurrent pushes to different doc_ids must not interfere with each other.
    Each doc should independently get version 1.
    """
    import threading
    import tempfile
    from sqlalchemy import create_engine as _create_engine, event as sa_event
    from sqlalchemy.orm import sessionmaker
    from doc_exchange.models import Base
    from doc_exchange.models.entities import ProjectSpace
    from datetime import datetime, timezone
    import uuid

    N = 4

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        conc_engine = _create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @sa_event.listens_for(conc_engine, "connect")
        def set_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        Base.metadata.create_all(conc_engine)
        SessionFactory = sessionmaker(bind=conc_engine)

        space_id = str(uuid.uuid4())
        setup_session = SessionFactory()
        space = ProjectSpace(
            id=space_id,
            name="concurrent-multi-doc-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        setup_session.add(space)
        setup_session.commit()
        setup_session.close()

        results = {}
        errors = []
        lock = threading.Lock()

        def push_thread(i: int):
            session = SessionFactory()
            try:
                audit = AuditLogService(db=session)
                svc = DocumentService(db=session, docs_root=tmp_docs_root, audit_log_service=audit)
                doc_id = f"sub-{i}/requirement"
                req = PushRequest(
                    doc_id=doc_id,
                    content=f"# Doc {i}",
                    pushed_by=f"agent-{i}",
                    project_space_id=space_id,
                    metadata={},
                )
                result = svc.push(req)
                session.commit()
                with lock:
                    results[doc_id] = result.version
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                session.close()

        threads = [threading.Thread(target=push_thread, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent push to different docs: {errors}"
        assert len(results) == N

        # Each doc should have version 1 (first push)
        for doc_id, version in results.items():
            assert version == 1, f"Expected version 1 for {doc_id}, got {version}"

        conc_engine.dispose()
    finally:
        import os as _os
        if _os.path.exists(db_path):
            _os.unlink(db_path)
