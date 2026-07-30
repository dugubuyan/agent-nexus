"""
Dumper: export a project space to a portable zip archive.

Archive layout
--------------
    <space_id>.zip
    ├── manifest.json          ← space / subprojects / subscriptions metadata
    │                            each document version lists a `content_file`
    │                            path instead of inline content
    └── docs/
        └── <subproject_id>/
            ├── requirement.md        ← latest (or versioned) published content
            ├── design.md
            ├── config_prod.md        ← variant encoded as <type>_<variant>.md
            └── requirement.v1.md     ← older versions when --include-history

Content source priority
-----------------------
1. ``document_version_contents`` table (primary)
2. Filesystem file at ``{docs_root}/{space_id}/docs/{subproject_id}/…``
   (fallback for the *latest* version when the DB row is missing or empty)

Usage (from project root):
    python tools/migration dump <space_id> [-o out.zip] [--docs-root ./workspace]
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from agent_nexus.models.entities import (
    Document,
    DocumentVersion,
    ProjectSpace,
    SubProject,
    Subscription,
)
from agent_nexus.services import blob_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _doc_filename(doc_type: str, doc_variant: Optional[str], version: int | None = None) -> str:
    """
    Return the filename for a document inside the zip archive.

    Latest version:   requirement.md  /  config_prod.md
    Older versions:   requirement.v1.md  /  config_prod.v1.md
    """
    base = f"{doc_type}_{doc_variant}" if doc_variant else doc_type
    if version is not None:
        return f"{base}.v{version}.md"
    return f"{base}.md"


def _fs_path(
    docs_root: str,
    space_id: str,
    subproject_id: str,
    doc_type: str,
    doc_variant: Optional[str],
) -> str:
    filename = f"{doc_type}_{doc_variant}.md" if doc_variant else f"{doc_type}.md"
    return os.path.join(docs_root, space_id, "docs", subproject_id, filename)


def _read_fs(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Core dump
# ---------------------------------------------------------------------------

def dump_space(
    db: Session,
    space_id: str,
    output_path: str,
    *,
    docs_root: str | None = None,
    include_history: bool = False,
    include_deleted: bool = False,
) -> None:
    """
    Export *space_id* to a zip archive at *output_path*.

    Parameters
    ----------
    db:             SQLAlchemy session on the source database.
    space_id:       The ProjectSpace.id to export.
    output_path:    Destination .zip file path.
    docs_root:      AGENT_NEXUS_DOCS_ROOT on the source instance.
                    Falls back to $AGENT_NEXUS_DOCS_ROOT, then ./workspace.
    include_history: Export every historical version (not just latest).
    include_deleted: Include soft-deleted documents.
    """
    effective_docs_root = (
        docs_root
        or os.environ.get("AGENT_NEXUS_DOCS_ROOT")
        or "./workspace"
    )

    # ── Validate space ────────────────────────────────────────────────────────
    space: ProjectSpace | None = (
        db.query(ProjectSpace).filter(ProjectSpace.id == space_id).first()
    )
    if space is None:
        raise ValueError(f"ProjectSpace '{space_id}' not found in source database.")

    manifest: dict[str, Any] = {
        "schema_version": "2",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "space": {
            "id": space.id,
            "name": space.name,
            "status": space.status,
            "created_at": _dt_iso(space.created_at),
        },
        "subprojects": [],
        "documents": [],
        "subscriptions": [],
    }

    # ── SubProjects ───────────────────────────────────────────────────────────
    subprojects: list[SubProject] = (
        db.query(SubProject)
        .filter(SubProject.project_space_id == space_id)
        .order_by(SubProject.created_at)
        .all()
    )
    for sp in subprojects:
        manifest["subprojects"].append({
            "id": sp.id,
            "name": sp.name,
            "type": sp.type,
            "stage": sp.stage,
            "stage_updated_at": _dt_iso(sp.stage_updated_at),
            "created_at": _dt_iso(sp.created_at),
        })

    # ── Documents + Versions ─────────────────────────────────────────────────
    doc_query = db.query(Document).filter(Document.project_space_id == space_id)
    if not include_deleted:
        doc_query = doc_query.filter(Document.status == "active")
    documents: list[Document] = doc_query.order_by(Document.created_at).all()

    missing_content: list[str] = []

    # Collect (zip_path, content_str) pairs to write into the archive
    file_entries: list[tuple[str, str]] = []

    for doc in documents:
        doc_entry: dict[str, Any] = {
            "id": doc.id,
            "subproject_id": doc.subproject_id,
            "doc_type": doc.doc_type,
            "doc_variant": doc.doc_variant,
            "latest_version": doc.latest_version,
            "status": doc.status,
            "created_at": _dt_iso(doc.created_at),
            "deleted_at": _dt_iso(doc.deleted_at),
            "versions": [],
        }

        version_query = (
            db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.status == "published",
            )
            .order_by(DocumentVersion.version)
        )
        if not include_history:
            version_query = version_query.filter(
                DocumentVersion.version == doc.latest_version
            )

        versions: list[DocumentVersion] = version_query.all()
        for ver in versions:
            # 1. Try content-addressed blob store
            content: str | None = blob_store.content_for_version(db, ver)

            # 2. Filesystem fallback (latest version only)
            if not content and ver.version == doc.latest_version:
                fs_path = _fs_path(
                    effective_docs_root,
                    space_id,
                    doc.subproject_id,
                    doc.doc_type,
                    doc.doc_variant,
                )
                content = _read_fs(fs_path)
                if content:
                    print(
                        f"[dump] WARNING: DB content missing for '{doc.id}' v{ver.version}"
                        f" — using filesystem fallback: {fs_path}",
                        file=sys.stderr,
                    )

            # 3. Still nothing — skip
            if not content:
                missing_content.append(f"{doc.id} v{ver.version}")
                print(
                    f"[dump] WARNING: no content for '{doc.id}' v{ver.version}"
                    f" — version skipped.",
                    file=sys.stderr,
                )
                continue

            # Determine the path inside the zip
            is_latest = (ver.version == doc.latest_version)
            if is_latest:
                zip_filename = _doc_filename(doc.doc_type, doc.doc_variant)
            else:
                zip_filename = _doc_filename(doc.doc_type, doc.doc_variant, ver.version)
            zip_path = f"docs/{doc.subproject_id}/{zip_filename}"

            file_entries.append((zip_path, content))

            doc_entry["versions"].append({
                "version": ver.version,
                "content_hash": ver.content_hash,
                "actor": ver.actor,
                "status": ver.status,
                "is_milestone": ver.is_milestone,
                "milestone_stage": ver.milestone_stage,
                "pushed_at": _dt_iso(ver.pushed_at),
                "published_at": _dt_iso(ver.published_at),
                "content_file": zip_path,   # ← pointer, not inline content
            })

        manifest["documents"].append(doc_entry)

    # ── Subscriptions ─────────────────────────────────────────────────────────
    subs: list[Subscription] = (
        db.query(Subscription)
        .filter(Subscription.project_space_id == space_id)
        .order_by(Subscription.created_at)
        .all()
    )
    for sub in subs:
        manifest["subscriptions"].append({
            "id": sub.id,
            "subscriber_project_id": sub.subscriber_project_id,
            "target_doc_id": sub.target_doc_id,
            "target_doc_type": sub.target_doc_type,
            "created_at": _dt_iso(sub.created_at),
        })

    if missing_content:
        manifest["_warnings"] = {"missing_content": missing_content}

    # ── Write zip ─────────────────────────────────────────────────────────────
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        for zip_path, content in file_entries:
            zf.writestr(zip_path, content)

    # ── Summary ───────────────────────────────────────────────────────────────
    doc_count = len(manifest["documents"])
    sub_count = len(manifest["subprojects"])
    ver_count = len(file_entries)
    warn_str = (
        f", {len(missing_content)} versions with missing content"
        if missing_content else ""
    )
    print(
        f"[dump] space '{space_id}' → {output_path}  "
        f"({sub_count} sub-projects, {doc_count} documents, {ver_count} files{warn_str})",
        file=sys.stderr,
    )
