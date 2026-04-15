"""
VersionRetentionService: daily cleanup of old document versions.

Retention rules (Requirements 3.9, 3.10, 3.11, 3.12):
  Keep if ANY of:
    1. is_latest = True  (version == document.latest_version)
    2. version >= (latest_version - keep_recent_n + 1)  (most recent N versions)
    3. is_milestone = True  (milestone snapshots)

  Archive if ALL of:
    - Does NOT satisfy any keep condition
    - pushed_at < NOW() - retention_days

  Archive action:
    - Delete document_version_contents content
    - Mark document_versions.status = "archived"  (preserve metadata)
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from doc_exchange.models.entities import Document, DocumentVersion, DocumentVersionContent


class VersionRetentionService:
    """
    Scans document versions and archives those that exceed the retention policy.

    Args:
        db: SQLAlchemy session.
        keep_recent_n: Number of most-recent versions to always keep (default 10).
        retention_days: Versions older than this many days are eligible for archival (default 90).
    """

    def __init__(self, db: Session, keep_recent_n: int = 10, retention_days: int = 90):
        self._db = db
        self._keep_recent_n = keep_recent_n
        self._retention_days = retention_days

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_cleanup(self, project_space_id: Optional[str] = None) -> dict:
        """
        Run the retention cleanup scan.

        Args:
            project_space_id: If provided, only scan documents in this space.
                              If None, scan all project spaces.

        Returns:
            dict with keys:
              - "scanned": total number of versions examined
              - "archived": number of versions archived
        """
        query = self._db.query(Document)
        if project_space_id is not None:
            query = query.filter(Document.project_space_id == project_space_id)

        documents = query.all()

        scanned = 0
        archived = 0

        for document in documents:
            latest_version = document.latest_version

            versions = (
                self._db.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.project_space_id == document.project_space_id,
                    # Skip already-archived versions
                    DocumentVersion.status != "archived",
                )
                .all()
            )

            for version in versions:
                scanned += 1
                if not self._should_keep(version, latest_version) and self._is_past_retention(version):
                    self._archive_version(version)
                    archived += 1

        self._db.flush()
        return {"scanned": scanned, "archived": archived}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_keep(self, version: DocumentVersion, latest_version: int) -> bool:
        """
        Return True if this version must be kept under the retention policy.

        Keep conditions (any one is sufficient):
          1. It is the latest version.
          2. It is within the most recent N versions.
          3. It is a milestone snapshot.
        """
        # Condition 1: latest version
        if version.version == latest_version:
            return True

        # Condition 2: within most recent N versions
        if version.version >= (latest_version - self._keep_recent_n + 1):
            return True

        # Condition 3: milestone snapshot
        if version.is_milestone:
            return True

        return False

    def _is_past_retention(self, version: DocumentVersion) -> bool:
        """Return True if the version's pushed_at is older than retention_days."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self._retention_days)
        pushed_at = version.pushed_at
        # Normalise to UTC-aware if naive (SQLite stores naive datetimes)
        if pushed_at.tzinfo is None:
            pushed_at = pushed_at.replace(tzinfo=timezone.utc)
        return pushed_at < cutoff

    def _archive_version(self, version: DocumentVersion) -> None:
        """
        Archive a version:
          1. Delete its content from document_version_contents.
          2. Mark document_versions.status = "archived".
        """
        content = (
            self._db.query(DocumentVersionContent)
            .filter(DocumentVersionContent.version_id == version.id)
            .first()
        )
        if content is not None:
            self._db.delete(content)

        version.status = "archived"
