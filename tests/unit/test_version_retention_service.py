"""
Unit tests for VersionRetentionService.

Covers Requirements 3.9, 3.10, 3.11, 3.12.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from doc_exchange.models.entities import Document, DocumentVersion, DocumentVersionContent
from doc_exchange.services.version_retention_service import VersionRetentionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_document(db: Session, space_id: str, latest_version: int = 0) -> Document:
    doc = Document(
        id=f"{uuid.uuid4()}/requirement",
        project_space_id=space_id,
        subproject_id=str(uuid.uuid4()),
        doc_type="requirement",
        config_stage=None,
        latest_version=latest_version,
        created_at=datetime.now(timezone.utc),
    )
    db.add(doc)
    db.flush()
    return doc


def _make_version(
    db: Session,
    document: Document,
    version_num: int,
    pushed_at: datetime,
    is_milestone: bool = False,
    status: str = "published",
    with_content: bool = True,
) -> DocumentVersion:
    ver = DocumentVersion(
        id=str(uuid.uuid4()),
        document_id=document.id,
        project_space_id=document.project_space_id,
        version=version_num,
        content_hash=str(uuid.uuid4()),
        pushed_by="test-project",
        status=status,
        is_milestone=is_milestone,
        milestone_stage=None,
        pushed_at=pushed_at,
        published_at=pushed_at if status == "published" else None,
    )
    db.add(ver)
    db.flush()

    if with_content:
        content = DocumentVersionContent(
            version_id=ver.id,
            project_space_id=document.project_space_id,
            content=f"Content for version {version_num}",
        )
        db.add(content)
        db.flush()

    return ver


def _old(days: int = 100) -> datetime:
    """Return a naive UTC datetime that is `days` days in the past."""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _recent(days: int = 1) -> datetime:
    """Return a naive UTC datetime that is `days` days in the past (within retention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Tests for _should_keep
# ---------------------------------------------------------------------------


class TestShouldKeep:
    def test_latest_version_is_always_kept(self, db_session, default_space):
        svc = VersionRetentionService(db_session)
        doc = _make_document(db_session, default_space.id, latest_version=5)
        ver = _make_version(db_session, doc, version_num=5, pushed_at=_old())
        assert svc._should_keep(ver, latest_version=5) is True

    def test_recent_n_versions_are_kept(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=3)
        doc = _make_document(db_session, default_space.id, latest_version=10)
        # Versions 8, 9, 10 should be kept (latest=10, N=3 → keep >= 10-3+1=8)
        for v in [8, 9, 10]:
            ver = _make_version(db_session, doc, version_num=v, pushed_at=_old())
            assert svc._should_keep(ver, latest_version=10) is True

    def test_old_version_outside_recent_n_not_kept(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=3)
        doc = _make_document(db_session, default_space.id, latest_version=10)
        # Version 7 is outside the recent 3 (8,9,10) and not latest/milestone
        ver = _make_version(db_session, doc, version_num=7, pushed_at=_old())
        assert svc._should_keep(ver, latest_version=10) is False

    def test_milestone_version_is_always_kept(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=3)
        doc = _make_document(db_session, default_space.id, latest_version=20)
        # Version 1 is old and outside recent N, but is a milestone
        ver = _make_version(db_session, doc, version_num=1, pushed_at=_old(), is_milestone=True)
        assert svc._should_keep(ver, latest_version=20) is True

    def test_non_milestone_old_version_not_kept(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=3)
        doc = _make_document(db_session, default_space.id, latest_version=20)
        ver = _make_version(db_session, doc, version_num=1, pushed_at=_old(), is_milestone=False)
        assert svc._should_keep(ver, latest_version=20) is False


# ---------------------------------------------------------------------------
# Tests for _archive_version
# ---------------------------------------------------------------------------


class TestArchiveVersion:
    def test_archived_version_has_status_archived(self, db_session, default_space):
        svc = VersionRetentionService(db_session)
        doc = _make_document(db_session, default_space.id, latest_version=5)
        ver = _make_version(db_session, doc, version_num=1, pushed_at=_old())

        svc._archive_version(ver)

        assert ver.status == "archived"

    def test_archived_version_content_is_deleted(self, db_session, default_space):
        svc = VersionRetentionService(db_session)
        doc = _make_document(db_session, default_space.id, latest_version=5)
        ver = _make_version(db_session, doc, version_num=1, pushed_at=_old(), with_content=True)

        # Confirm content exists before archival
        content_before = (
            db_session.query(DocumentVersionContent)
            .filter(DocumentVersionContent.version_id == ver.id)
            .first()
        )
        assert content_before is not None

        svc._archive_version(ver)
        db_session.flush()

        content_after = (
            db_session.query(DocumentVersionContent)
            .filter(DocumentVersionContent.version_id == ver.id)
            .first()
        )
        assert content_after is None

    def test_archive_version_without_content_does_not_raise(self, db_session, default_space):
        svc = VersionRetentionService(db_session)
        doc = _make_document(db_session, default_space.id, latest_version=5)
        ver = _make_version(db_session, doc, version_num=1, pushed_at=_old(), with_content=False)

        # Should not raise even if there is no content record
        svc._archive_version(ver)
        assert ver.status == "archived"


# ---------------------------------------------------------------------------
# Tests for run_cleanup
# ---------------------------------------------------------------------------


class TestRunCleanup:
    def test_old_non_recent_non_milestone_version_is_archived(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=3, retention_days=90)
        doc = _make_document(db_session, default_space.id, latest_version=10)

        # Version 1: old, outside recent 3, not milestone → should be archived
        old_ver = _make_version(db_session, doc, version_num=1, pushed_at=_old(100))
        # Version 10: latest → should be kept
        latest_ver = _make_version(db_session, doc, version_num=10, pushed_at=_old(5))

        stats = svc.run_cleanup(project_space_id=default_space.id)

        assert stats["archived"] == 1
        assert old_ver.status == "archived"
        assert latest_ver.status != "archived"

    def test_version_within_retention_days_not_archived(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=3, retention_days=90)
        doc = _make_document(db_session, default_space.id, latest_version=10)

        # Version 1: outside recent N, not milestone, but only 10 days old → NOT archived
        recent_ver = _make_version(db_session, doc, version_num=1, pushed_at=_recent(10))
        # Version 10: latest
        _make_version(db_session, doc, version_num=10, pushed_at=_recent(1))

        stats = svc.run_cleanup(project_space_id=default_space.id)

        assert stats["archived"] == 0
        assert recent_ver.status != "archived"

    def test_latest_version_never_archived(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=1, retention_days=1)
        doc = _make_document(db_session, default_space.id, latest_version=1)
        ver = _make_version(db_session, doc, version_num=1, pushed_at=_old(200))

        stats = svc.run_cleanup(project_space_id=default_space.id)

        assert stats["archived"] == 0
        assert ver.status != "archived"

    def test_milestone_version_never_archived(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=1, retention_days=1)
        doc = _make_document(db_session, default_space.id, latest_version=5)

        milestone = _make_version(
            db_session, doc, version_num=1, pushed_at=_old(200), is_milestone=True
        )
        _make_version(db_session, doc, version_num=5, pushed_at=_old(1))

        stats = svc.run_cleanup(project_space_id=default_space.id)

        assert milestone.status != "archived"

    def test_run_cleanup_returns_correct_stats(self, db_session, default_space):
        svc = VersionRetentionService(db_session, keep_recent_n=2, retention_days=90)
        doc = _make_document(db_session, default_space.id, latest_version=5)

        # Versions 1, 2, 3: old, outside recent 2 (4,5), not milestone → archived
        for v in [1, 2, 3]:
            _make_version(db_session, doc, version_num=v, pushed_at=_old(100))
        # Versions 4, 5: recent N → kept
        for v in [4, 5]:
            _make_version(db_session, doc, version_num=v, pushed_at=_old(5))

        stats = svc.run_cleanup(project_space_id=default_space.id)

        assert stats["scanned"] == 5
        assert stats["archived"] == 3

    def test_run_cleanup_scans_all_spaces_when_no_filter(self, db_session, engine):
        """When project_space_id is None, all spaces are scanned."""
        from doc_exchange.models.entities import ProjectSpace

        space_a = ProjectSpace(
            id=str(uuid.uuid4()),
            name="space-a",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        space_b = ProjectSpace(
            id=str(uuid.uuid4()),
            name="space-b",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add_all([space_a, space_b])
        db_session.flush()

        svc = VersionRetentionService(db_session, keep_recent_n=1, retention_days=90)

        for space in [space_a, space_b]:
            doc = _make_document(db_session, space.id, latest_version=3)
            # Version 1: old, outside recent 1 (only v3 kept), not milestone → archived
            _make_version(db_session, doc, version_num=1, pushed_at=_old(100))
            # Version 3: latest → kept
            _make_version(db_session, doc, version_num=3, pushed_at=_old(5))

        stats = svc.run_cleanup(project_space_id=None)

        # 2 spaces × 2 versions each = 4 scanned, 2 archived (one per space)
        assert stats["scanned"] == 4
        assert stats["archived"] == 2

    def test_already_archived_versions_are_skipped(self, db_session, default_space):
        """Versions already marked archived are not double-counted."""
        svc = VersionRetentionService(db_session, keep_recent_n=1, retention_days=1)
        doc = _make_document(db_session, default_space.id, latest_version=3)

        # Pre-archived version
        _make_version(
            db_session, doc, version_num=1, pushed_at=_old(200), status="archived"
        )
        # Latest version
        _make_version(db_session, doc, version_num=3, pushed_at=_old(1))

        stats = svc.run_cleanup(project_space_id=default_space.id)

        # Only the latest (non-archived) version is scanned; nothing new to archive
        assert stats["scanned"] == 1
        assert stats["archived"] == 0
