"""
Restorer: import a zip snapshot into a target AgentNexus instance.

Reads the archive produced by dumper.py (schema_version "2").
Content is loaded from the ``docs/`` files inside the zip, not from
inline JSON strings.

Usage (from project root):
    python tools/migration restore space.zip [--new-space-id ...] [--db-url ...]
"""

from __future__ import annotations

import json
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from agent_nexus.mcp.dependencies import ServiceContainer
from agent_nexus.models.entities import ProjectSpace, Subscription
from agent_nexus.services.schemas import PushRequest


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class RestoreResult:
    def __init__(self) -> None:
        self.space_id: str = ""
        self.space_name: str = ""
        self.subprojects_created: int = 0
        self.documents_created: int = 0
        self.versions_pushed: int = 0
        self.subscriptions_created: int = 0
        self.skipped_unchanged: int = 0
        self.errors: list[str] = []

    def __str__(self) -> str:
        lines = [
            f"[restore] space '{self.space_name}' ({self.space_id})",
            f"  sub-projects : {self.subprojects_created}",
            f"  documents    : {self.documents_created}",
            f"  versions     : {self.versions_pushed} pushed"
            f", {self.skipped_unchanged} skipped (content unchanged)",
            f"  subscriptions: {self.subscriptions_created}",
        ]
        if self.errors:
            lines.append(f"  errors ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"    - {e}")
        return "\n".join(lines)


def restore_space(
    db: Session,
    docs_root: str,
    manifest: dict[str, Any],
    zip_file: zipfile.ZipFile,
    *,
    new_space_id: str | None = None,
    new_space_name: str | None = None,
    keep_ids: bool = False,
    dry_run: bool = False,
) -> RestoreResult:
    """
    Restore a snapshot into the target database.

    Parameters
    ----------
    db:             SQLAlchemy session for the target database.
    docs_root:      AGENT_NEXUS_DOCS_ROOT on the target instance.
    manifest:       Parsed manifest.json from the archive.
    zip_file:       Open ZipFile handle to read content files from.
    new_space_id:   Override restored space UUID (default: new UUID).
    new_space_name: Override restored space name.
    keep_ids:       Reuse original UUIDs (safe only when no collisions exist).
    dry_run:        Validate without writing anything.
    """
    schema_version = manifest.get("schema_version")
    if schema_version != "2":
        raise ValueError(
            f"Unsupported snapshot schema_version '{schema_version}'. Expected '2'."
        )

    result = RestoreResult()

    # ── Determine target space ID / name ──────────────────────────────────────
    src_space = manifest["space"]
    target_space_id = (new_space_id or src_space["id"]) if keep_ids else (new_space_id or str(uuid.uuid4()))
    target_space_name = new_space_name or src_space["name"]

    result.space_id = target_space_id
    result.space_name = target_space_name

    # ── Build subproject ID mapping ───────────────────────────────────────────
    subproject_id_map: dict[str, str] = {}
    for sp_data in manifest.get("subprojects", []):
        src_id = sp_data["id"]
        subproject_id_map[src_id] = src_id if keep_ids else str(uuid.uuid4())

    if dry_run:
        print(
            f"[dry-run] would restore space '{target_space_name}' ({target_space_id}) "
            f"with {len(manifest.get('subprojects', []))} sub-projects, "
            f"{len(manifest.get('documents', []))} documents.",
            file=sys.stderr,
        )
        return result

    # ── Create target ProjectSpace ────────────────────────────────────────────
    existing_space = db.query(ProjectSpace).filter(ProjectSpace.id == target_space_id).first()
    if existing_space is not None:
        print(
            f"[restore] WARNING: space '{target_space_id}' already exists — "
            "records will be merged/skipped as appropriate.",
            file=sys.stderr,
        )
    else:
        db.add(ProjectSpace(
            id=target_space_id,
            name=target_space_name,
            status=src_space.get("status", "active"),
            created_at=_parse_dt(src_space.get("created_at")) or datetime.now(timezone.utc),
        ))
        db.flush()

    # Ensure FTS virtual table exists in the target database
    from agent_nexus.search.fts import ensure_fts_table
    ensure_fts_table(db.get_bind())

    container = ServiceContainer(db_session=db, docs_root=docs_root)

    # ── Restore SubProjects ───────────────────────────────────────────────────
    from agent_nexus.models.entities import SubProject

    existing_ids: set[str] = {
        row.id
        for row in db.query(SubProject.id)
        .filter(SubProject.project_space_id == target_space_id)
        .all()
    }

    for sp_data in manifest.get("subprojects", []):
        target_id = subproject_id_map[sp_data["id"]]
        if target_id in existing_ids:
            print(
                f"[restore] sub-project '{sp_data['name']}' ({target_id}) already exists — skipped.",
                file=sys.stderr,
            )
            continue
        db.add(SubProject(
            id=target_id,
            project_space_id=target_space_id,
            name=sp_data["name"],
            type=sp_data["type"],
            stage=sp_data.get("stage", "design"),
            stage_updated_at=_parse_dt(sp_data.get("stage_updated_at")) or datetime.now(timezone.utc),
            created_at=_parse_dt(sp_data.get("created_at")) or datetime.now(timezone.utc),
        ))
        result.subprojects_created += 1

    db.flush()

    # ── Restore Documents ─────────────────────────────────────────────────────
    zip_names = set(zip_file.namelist())

    for doc_data in manifest.get("documents", []):
        src_subproject_id = doc_data["subproject_id"]
        target_subproject_id = subproject_id_map.get(src_subproject_id, src_subproject_id)

        doc_type = doc_data["doc_type"]
        doc_variant = doc_data.get("doc_variant")
        target_doc_id = (
            f"{target_subproject_id}/{doc_type}/{doc_variant}"
            if doc_variant
            else f"{target_subproject_id}/{doc_type}"
        )

        versions = doc_data.get("versions", [])
        if not versions:
            print(
                f"[restore] document '{doc_data['id']}' has no exportable versions — skipped.",
                file=sys.stderr,
            )
            continue

        doc_pushed = False
        for ver in versions:
            content_file = ver.get("content_file", "")

            # Read content from zip archive
            if content_file and content_file in zip_names:
                content = zip_file.read(content_file).decode("utf-8")
            else:
                print(
                    f"[restore] ERROR: content file '{content_file}' not found in archive"
                    f" for '{target_doc_id}' v{ver.get('version')} — skipped.",
                    file=sys.stderr,
                )
                result.errors.append(f"missing content file: {content_file}")
                continue

            if not content:
                continue

            pushed_by = ver.get("pushed_by", "migration")
            if pushed_by.startswith("agent:"):
                pushed_by = "migration"

            req = PushRequest(
                doc_id=target_doc_id,
                content=content,
                pushed_by=pushed_by,
                project_space_id=target_space_id,
                metadata={},
            )
            try:
                container.document_service.push(req)
                result.versions_pushed += 1
            except Exception as exc:
                err_code = getattr(exc, "error_code", None)
                if err_code == "CONTENT_UNCHANGED":
                    result.skipped_unchanged += 1
                else:
                    msg = f"push failed for '{target_doc_id}' v{ver.get('version')}: {exc}"
                    result.errors.append(msg)
                    print(f"[restore] ERROR: {msg}", file=sys.stderr)

            if not doc_pushed:
                result.documents_created += 1
                doc_pushed = True

    db.flush()

    # ── Restore Subscriptions ─────────────────────────────────────────────────
    for sub_data in manifest.get("subscriptions", []):
        src_subscriber_id = sub_data["subscriber_project_id"]
        target_subscriber_id = subproject_id_map.get(src_subscriber_id, src_subscriber_id)

        target_doc_id_sub = sub_data.get("target_doc_id")
        if target_doc_id_sub:
            parts = target_doc_id_sub.split("/", 1)
            if len(parts) == 2 and parts[0] in subproject_id_map:
                target_doc_id_sub = f"{subproject_id_map[parts[0]]}/{parts[1]}"

        try:
            container.subscription_service.add_rule(
                subscriber_project_id=target_subscriber_id,
                project_space_id=target_space_id,
                target_doc_id=target_doc_id_sub,
                target_doc_type=sub_data.get("target_doc_type"),
            )
            result.subscriptions_created += 1
        except Exception as exc:
            msg = f"subscription restore failed for '{target_subscriber_id}': {exc}"
            result.errors.append(msg)
            print(f"[restore] ERROR: {msg}", file=sys.stderr)

    db.flush()
    return result


def restore_from_file(
    db: Session,
    docs_root: str,
    input_path: str,
    *,
    new_space_id: str | None = None,
    new_space_name: str | None = None,
    keep_ids: bool = False,
    dry_run: bool = False,
) -> RestoreResult:
    """Open a zip snapshot and restore it."""
    with zipfile.ZipFile(input_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        return restore_space(
            db,
            docs_root,
            manifest,
            zf,
            new_space_id=new_space_id,
            new_space_name=new_space_name,
            keep_ids=keep_ids,
            dry_run=dry_run,
        )
