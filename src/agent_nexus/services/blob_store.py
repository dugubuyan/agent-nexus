"""Content-addressed blob storage helpers.

Document content lives in the ``blobs`` table, keyed by
``(project_space_id, content_hash)``. A DocumentVersion references its content
by ``content_hash`` (a column it already carries), so identical content — a
milestone snapshot of an unchanged doc, a revert to a prior version, shared
boilerplate across documents in a space — is stored exactly once and shared,
just like a Git blob.

This module centralises the read/write/GC logic so callers never embed a full
content copy per version.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from agent_nexus.models.entities import Blob, DocumentVersion

if TYPE_CHECKING:  # pragma: no cover
    pass


def put_blob(db: Session, project_space_id: str, content_hash: str, content: str) -> None:
    """Store content under (project_space_id, content_hash) if not already present.

    Idempotent: because the key is the content hash, re-storing identical content
    (e.g. a milestone snapshot, or the same text pushed to another doc) is a no-op
    and costs zero extra bytes.
    """
    exists = (
        db.query(Blob.content_hash)
        .filter(
            Blob.project_space_id == project_space_id,
            Blob.content_hash == content_hash,
        )
        .first()
    )
    if exists is not None:
        return
    db.add(
        Blob(
            project_space_id=project_space_id,
            content_hash=content_hash,
            content=content,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()


def get_blob_content(db: Session, project_space_id: str, content_hash: str) -> Optional[str]:
    """Return the content for (project_space_id, content_hash), or None if absent."""
    blob = (
        db.query(Blob)
        .filter(
            Blob.project_space_id == project_space_id,
            Blob.content_hash == content_hash,
        )
        .first()
    )
    return blob.content if blob is not None else None


def content_for_version(db: Session, version: DocumentVersion) -> Optional[str]:
    """Return the content for a DocumentVersion via its content_hash.

    Archived versions have had their content reclaimed by retention; they report
    no content regardless of whether an identical blob was later re-created by a
    fresh push of the same text.
    """
    if version.status == "archived":
        return None
    return get_blob_content(db, version.project_space_id, version.content_hash)


def delete_blob_if_unreferenced(db: Session, project_space_id: str, content_hash: str) -> bool:
    """Delete a blob only if no live (non-archived) version still references it.

    This is the Git-GC reachability rule: a blob shared by several versions must
    survive until every referencing version has been archived. Returns True if
    the blob was deleted.
    """
    still_referenced = (
        db.query(DocumentVersion.id)
        .filter(
            DocumentVersion.project_space_id == project_space_id,
            DocumentVersion.content_hash == content_hash,
            DocumentVersion.status != "archived",
        )
        .first()
    )
    if still_referenced is not None:
        return False

    deleted = (
        db.query(Blob)
        .filter(
            Blob.project_space_id == project_space_id,
            Blob.content_hash == content_hash,
        )
        .delete(synchronize_session=False)
    )
    return deleted > 0
