"""
DocumentService: push, get, list_versions, and get_latest_hash.

Covers Requirements 2.1-2.6, 3.1-3.8, 6.1, 6.5, 11.1, 11.4.
"""

import hashlib
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

from agent_nexus.models.entities import Document, DocumentVersion, DocumentVersionContent, SubProject
from agent_nexus.services.audit_log_service import AuditLogService
from agent_nexus.services.errors import AgentNexusError
from agent_nexus.services.schemas import (
    DocumentResult,
    PushRequest,
    PushResult,
    VersionMeta,
)

if TYPE_CHECKING:
    from agent_nexus.analyzer.analyzer_service import AnalyzerService
    from agent_nexus.services.notification_service import NotificationService
    from agent_nexus.services.subscription_service import SubscriptionService
    from agent_nexus.services.task_service import TaskService

# Module-level lock to serialize concurrent pushes to the same doc_id.
# This ensures version number monotonicity when using SQLite (which lacks
# row-level SELECT FOR UPDATE). For PostgreSQL, this can be replaced with
# database-level advisory locks.
_push_lock = threading.Lock()

VALID_DOC_TYPES = {
    "requirement", "design", "api", "config", "task",
    "schema", "runbook", "changelog", "test-plan",
}

# Variants that are open-ended (any string allowed) — validated loosely
# Variants for config must be one of these
VALID_CONFIG_VARIANTS = {"dev", "test", "prod"}


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_doc_id(doc_id: str) -> tuple[str, str, Optional[str]]:
    """
    Parse doc_id into (subproject_id, doc_type, doc_variant).

    Valid formats:
      {subproject_id}/{doc_type}              -> doc_variant = None
      {subproject_id}/{doc_type}/{variant}    -> doc_variant = variant string

    All doc_types support an optional variant. For 'config', variant is
    required and must be one of VALID_CONFIG_VARIANTS.

    Raises AgentNexusError(INVALID_DOC_ID) on any format violation.
    """
    if not doc_id or not doc_id.strip():
        raise AgentNexusError(
            error_code="INVALID_DOC_ID",
            message="doc_id must not be empty.",
            details={"format": "{subproject_id}/{doc_type}[/{variant}]"},
        )

    parts = doc_id.split("/")

    if len(parts) == 2:
        subproject_id, doc_type = parts
        doc_variant = None
    elif len(parts) == 3:
        subproject_id, doc_type, doc_variant = parts
    else:
        raise AgentNexusError(
            error_code="INVALID_DOC_ID",
            message=f"doc_id '{doc_id}' has invalid format. Expected {{subproject_id}}/{{doc_type}}[/{{variant}}].",
            details={"format": "{subproject_id}/{doc_type}[/{variant}]"},
        )

    if not subproject_id:
        raise AgentNexusError(
            error_code="INVALID_DOC_ID",
            message="subproject_id part of doc_id must not be empty.",
            details={"format": "{subproject_id}/{doc_type}[/{variant}]"},
        )

    if doc_type not in VALID_DOC_TYPES:
        raise AgentNexusError(
            error_code="INVALID_DOC_ID",
            message=f"doc_type '{doc_type}' is not valid. Must be one of: {sorted(VALID_DOC_TYPES)}.",
            details={"valid_doc_types": sorted(VALID_DOC_TYPES)},
        )

    # config requires a variant
    if doc_type == "config":
        if not doc_variant:
            raise AgentNexusError(
                error_code="INVALID_DOC_ID",
                message=f"doc_type 'config' requires a variant. Must be one of: {sorted(VALID_CONFIG_VARIANTS)}.",
                details={"valid_variants": sorted(VALID_CONFIG_VARIANTS)},
            )
        if doc_variant not in VALID_CONFIG_VARIANTS:
            raise AgentNexusError(
                error_code="INVALID_DOC_ID",
                message=f"config variant '{doc_variant}' is not valid. Must be one of: {sorted(VALID_CONFIG_VARIANTS)}.",
                details={"valid_variants": sorted(VALID_CONFIG_VARIANTS)},
            )

    # variant must not be empty string if provided
    if doc_variant is not None and not doc_variant.strip():
        raise AgentNexusError(
            error_code="INVALID_DOC_ID",
            message="doc_variant must not be empty if provided.",
            details={"format": "{subproject_id}/{doc_type}[/{variant}]"},
        )

    return subproject_id, doc_type, doc_variant


def _doc_filename(doc_type: str, doc_variant: Optional[str]) -> str:
    """Return the filename for a document (without directory)."""
    if doc_variant:
        return f"{doc_type}_{doc_variant}.md"
    return f"{doc_type}.md"


class DocumentService:
    def __init__(
        self,
        db: Session,
        docs_root: str,
        audit_log_service: AuditLogService,
        analyzer_service: Optional["AnalyzerService"] = None,
        subscription_service: Optional["SubscriptionService"] = None,
        notification_service: Optional["NotificationService"] = None,
        task_service: Optional["TaskService"] = None,
    ):
        self._db = db
        self._docs_root = docs_root
        self._audit = audit_log_service
        self._analyzer_service = analyzer_service
        self._subscription_service = subscription_service
        self._notification_service = notification_service
        self._task_service = task_service

    # ------------------------------------------------------------------
    # Task 4.1: push()
    # ------------------------------------------------------------------

    def push(self, req: PushRequest) -> PushResult:
        """
        Push a new document version.

        Validates doc_id format, config stage metadata, and content hash dedup.
        Writes DB records and filesystem file atomically.
        """
        # 1. Validate doc_id format
        subproject_id, doc_type, doc_variant = _parse_doc_id(req.doc_id)

        with _push_lock:
            return self._push_locked(req, subproject_id, doc_type, doc_variant)

    def _push_locked(
        self,
        req: PushRequest,
        subproject_id: str,
        doc_type: str,
        doc_variant: Optional[str],
    ) -> PushResult:
        """Execute the push under the module-level lock to prevent data races."""
        # 3. Compute content hash
        content_hash = _sha256(req.content)

        # 4. Check for content unchanged
        latest_hash = self.get_latest_hash(req.doc_id, req.project_space_id)
        if latest_hash is not None and latest_hash == content_hash:
            raise AgentNexusError(
                error_code="CONTENT_UNCHANGED",
                message="Document content is identical to the latest version.",
                details={"doc_id": req.doc_id},
            )

        # 5. Determine status
        now = datetime.now(timezone.utc)
        if req.pushed_by.startswith("agent:"):
            status = "draft"
            published_at = None
        else:
            status = "published"
            published_at = now

        # 6. Get or create Document record
        document = (
            self._db.query(Document)
            .filter(
                Document.id == req.doc_id,
                Document.project_space_id == req.project_space_id,
            )
            .first()
        )

        if document is None:
            document = Document(
                id=req.doc_id,
                project_space_id=req.project_space_id,
                subproject_id=subproject_id,
                doc_type=doc_type,
                doc_variant=doc_variant,
                latest_version=0,
                created_at=now,
            )
            self._db.add(document)
            self._db.flush()

        new_version_num = document.latest_version + 1

        # 7. Write file to filesystem (before DB commit, so we can roll back on DB failure)
        file_path = self._get_file_path(req.project_space_id, subproject_id, doc_type, doc_variant)
        old_content: Optional[str] = None
        file_existed = os.path.exists(file_path)

        if file_existed:
            with open(file_path, "r", encoding="utf-8") as f:
                old_content = f.read()

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(req.content)

        # 8. Write DB records (with rollback on failure)
        try:
            version_id = str(uuid.uuid4())
            doc_version = DocumentVersion(
                id=version_id,
                document_id=req.doc_id,
                project_space_id=req.project_space_id,
                version=new_version_num,
                content_hash=content_hash,
                pushed_by=req.pushed_by,
                status=status,
                is_milestone=False,
                milestone_stage=None,
                pushed_at=now,
                published_at=published_at,
            )
            self._db.add(doc_version)

            doc_content = DocumentVersionContent(
                version_id=version_id,
                project_space_id=req.project_space_id,
                content=req.content,
            )
            self._db.add(doc_content)

            document.latest_version = new_version_num
            self._db.flush()

        except Exception:
            # Roll back file system change
            if file_existed and old_content is not None:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(old_content)
            elif not file_existed and os.path.exists(file_path):
                os.remove(file_path)
            raise

        # 9. Write audit log
        self._audit.log(
            operation_type="push_document",
            operator_project_id=req.pushed_by,
            target_id=req.doc_id,
            result="success",
            project_space_id=req.project_space_id,
        )

        # 9b. Update FTS index for published versions (Req 1.2)
        if status == "published":
            from agent_nexus.search.fts import upsert_doc
            upsert_doc(
                db=self._db,
                doc_id=req.doc_id,
                project_space_id=req.project_space_id,
                subproject_id=subproject_id,
                doc_type=doc_type,
                content=req.content,
            )

        # 10. Trigger notification + task pipeline for published versions (Req 5.1, 7.1, 11.2, 11.3)
        if (
            status == "published"
            and self._analyzer_service is not None
            and self._subscription_service is not None
            and self._notification_service is not None
            and self._task_service is not None
        ):
            self._run_pipeline(req.doc_id, doc_type, doc_version, req.project_space_id)

        return PushResult(version=new_version_num, doc_id=req.doc_id, status=status)

    # ------------------------------------------------------------------
    # Task 4.2: get() and list_versions()
    # ------------------------------------------------------------------

    def get(
        self,
        doc_id: str,
        project_space_id: str,
        version: Optional[int] = None,
    ) -> DocumentResult:
        """
        Retrieve a document version.

        If version is None, returns the latest version.
        Raises DOC_NOT_FOUND or VERSION_NOT_FOUND as appropriate.
        """
        document = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
                Document.status == "active",
            )
            .first()
        )

        if document is None:
            raise AgentNexusError(
                error_code="DOC_NOT_FOUND",
                message=f"Document '{doc_id}' does not exist.",
                details={"doc_id": doc_id},
            )

        target_version = version if version is not None else document.latest_version

        doc_version = (
            self._db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.project_space_id == project_space_id,
                DocumentVersion.version == target_version,
            )
            .first()
        )

        if doc_version is None:
            raise AgentNexusError(
                error_code="VERSION_NOT_FOUND",
                message=f"Version {target_version} of document '{doc_id}' does not exist.",
                details={"doc_id": doc_id, "version": target_version},
            )

        content_record = (
            self._db.query(DocumentVersionContent)
            .filter(DocumentVersionContent.version_id == doc_version.id)
            .first()
        )
        content = content_record.content if content_record else ""

        return DocumentResult(
            doc_id=doc_id,
            content=content,
            version=doc_version.version,
            pushed_at=doc_version.pushed_at,
            pushed_by=doc_version.pushed_by,
            status=doc_version.status,
        )

    def list_versions(self, doc_id: str, project_space_id: str) -> list[VersionMeta]:
        """
        List all versions of a document.

        Raises DOC_NOT_FOUND if the document does not exist.
        """
        document = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
            )
            .first()
        )

        if document is None:
            raise AgentNexusError(
                error_code="DOC_NOT_FOUND",
                message=f"Document '{doc_id}' does not exist.",
                details={"doc_id": doc_id},
            )

        versions = (
            self._db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.project_space_id == project_space_id,
            )
            .order_by(DocumentVersion.version)
            .all()
        )

        return [
            VersionMeta(
                version=v.version,
                pushed_at=v.pushed_at,
                pushed_by=v.pushed_by,
                status=v.status,
            )
            for v in versions
        ]

    # ------------------------------------------------------------------
    # Task 4.3: get_latest_hash()
    # ------------------------------------------------------------------

    def get_latest_hash(self, doc_id: str, project_space_id: str) -> Optional[str]:
        """
        Return the content_hash of the latest version, or None if the document
        does not exist. Used by FileWatcherService for dedup.
        """
        document = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
                Document.status == "active",
            )
            .first()
        )

        if document is None or document.latest_version == 0:
            return None

        doc_version = (
            self._db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.project_space_id == project_space_id,
                DocumentVersion.version == document.latest_version,
            )
            .first()
        )

        return doc_version.content_hash if doc_version else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        doc_id: str,
        doc_type: str,
        new_version_obj: DocumentVersion,
        project_space_id: str,
    ) -> None:
        """
        Run the post-push pipeline for published versions:
          1. Query all subprojects in the space
          2. Analyze impact via AnalyzerService
          3. Generate notifications for subscribers
          4. Generate tasks for affected projects

        Requirements 5.1, 7.1, 11.2, 11.3
        """
        import asyncio

        # a. Query all subprojects in the space
        all_subprojects = (
            self._db.query(SubProject)
            .filter(SubProject.project_space_id == project_space_id)
            .all()
        )

        # b. Get the Document record for the analyzer
        document = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
            )
            .first()
        )
        if document is None:
            return

        # c. Run analysis (async → sync bridge)
        try:
            loop = asyncio.get_running_loop()
            # Running inside an async context — use a thread to avoid blocking
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self._analyzer_service.analyze(document, new_version_obj, all_subprojects),
                )
                analysis = future.result()
        except RuntimeError:
            # No running event loop — safe to use asyncio.run()
            analysis = asyncio.run(
                self._analyzer_service.analyze(document, new_version_obj, all_subprojects)
            )

        # d. Get subscribers and generate notifications
        subscriber_ids = self._subscription_service.get_subscribers(
            project_space_id=project_space_id,
            doc_id=doc_id,
            doc_type=doc_type,
        )
        self._notification_service.generate(
            doc_id=doc_id,
            version=new_version_obj.version,
            subscriber_ids=subscriber_ids,
            project_space_id=project_space_id,
        )

        # e. Generate tasks from analysis
        self._task_service.generate(
            analysis=analysis,
            project_space_id=project_space_id,
        )

    # ------------------------------------------------------------------
    # Task 14: publish_draft()
    # ------------------------------------------------------------------

    def publish_draft(self, doc_id: str, version: int, project_space_id: str) -> dict:
        """
        Confirm a draft version, updating its status to 'published' and
        triggering the subscription notification pipeline.

        Requirements 11.3, 11.6.

        Raises AgentNexusError(INVALID_STATUS_TRANSITION) if:
          - The version does not exist
          - The version is already published
        """
        doc_version = (
            self._db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.project_space_id == project_space_id,
                DocumentVersion.version == version,
            )
            .first()
        )

        if doc_version is None:
            raise AgentNexusError(
                error_code="INVALID_STATUS_TRANSITION",
                message="Version not found or already published",
                details={"doc_id": doc_id, "version": version},
            )

        if doc_version.status != "draft":
            raise AgentNexusError(
                error_code="INVALID_STATUS_TRANSITION",
                message=f"Version is already {doc_version.status}, cannot publish",
                details={"doc_id": doc_id, "version": version, "current_status": doc_version.status},
            )

        # Update status and published_at
        now = datetime.now(timezone.utc)
        doc_version.status = "published"
        doc_version.published_at = now
        self._db.flush()

        # Update FTS index now that the draft is published (Req 1.3)
        content_rec = (
            self._db.query(DocumentVersionContent)
            .filter(DocumentVersionContent.version_id == doc_version.id)
            .first()
        )
        document_for_fts = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
            )
            .first()
        )
        if content_rec is not None and document_for_fts is not None:
            from agent_nexus.search.fts import upsert_doc
            upsert_doc(
                db=self._db,
                doc_id=doc_id,
                project_space_id=project_space_id,
                subproject_id=document_for_fts.subproject_id,
                doc_type=document_for_fts.doc_type,
                content=content_rec.content,
            )

        # Trigger notification + task pipeline (Req 11.3)
        document = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
            )
            .first()
        )
        if (
            document is not None
            and self._analyzer_service is not None
            and self._subscription_service is not None
            and self._notification_service is not None
            and self._task_service is not None
        ):
            self._run_pipeline(doc_id, document.doc_type, doc_version, project_space_id)

        return {"doc_id": doc_id, "version": version, "status": "published"}

    # ------------------------------------------------------------------
    # Task 15.1: create_milestone_snapshot()
    # ------------------------------------------------------------------

    def create_milestone_snapshot(
        self,
        subproject_id: str,
        new_stage: str,
        triggered_by: str,
        project_space_id: str,
    ) -> dict:
        """
        Create milestone snapshots for all published documents of a subproject.

        For each document belonging to subproject_id:
          - Find the latest published version
          - If none exists: log to AuditLog and skip (Req 12.5)
          - If exists: create a new DocumentVersion with is_milestone=True,
            milestone_stage=new_stage, and copy the content (Req 12.1, 12.2, 12.3)

        Returns {"snapshots_created": int, "skipped": int}.
        """
        now = datetime.now(timezone.utc)
        documents = (
            self._db.query(Document)
            .filter(Document.subproject_id == subproject_id,
                    Document.project_space_id == project_space_id)
            .all()
        )

        snapshots_created = 0
        skipped = 0

        for document in documents:
            # Find the latest published version
            published_version = (
                self._db.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.project_space_id == project_space_id,
                    DocumentVersion.status == "published",
                )
                .order_by(DocumentVersion.version.desc())
                .first()
            )

            if published_version is None:
                # Req 12.5: skip and log
                self._audit.log(
                    operation_type="milestone_snapshot",
                    operator_project_id=triggered_by,
                    target_id=document.id,
                    result="skipped",
                    project_space_id=project_space_id,
                    detail="No published version, skipping snapshot",
                )
                skipped += 1
                continue

            # Copy content from source version
            source_content = (
                self._db.query(DocumentVersionContent)
                .filter(DocumentVersionContent.version_id == published_version.id)
                .first()
            )

            new_version_num = document.latest_version + 1
            new_version_id = str(uuid.uuid4())

            snapshot = DocumentVersion(
                id=new_version_id,
                document_id=document.id,
                project_space_id=project_space_id,
                version=new_version_num,
                content_hash=published_version.content_hash,
                pushed_by=triggered_by,
                status="published",
                is_milestone=True,
                milestone_stage=new_stage,
                pushed_at=now,
                published_at=now,
            )
            self._db.add(snapshot)

            if source_content is not None:
                snapshot_content = DocumentVersionContent(
                    version_id=new_version_id,
                    project_space_id=project_space_id,
                    content=source_content.content,
                )
                self._db.add(snapshot_content)

            document.latest_version = new_version_num
            snapshots_created += 1

        self._db.flush()
        return {"snapshots_created": snapshots_created, "skipped": skipped}

    # ------------------------------------------------------------------
    # delete()
    # ------------------------------------------------------------------

    def delete(self, doc_id: str, project_id: str, project_space_id: str) -> dict:
        """
        Soft-delete a document owned by project_id.

        - Sets Document.status = 'deleted', records deleted_at timestamp.
        - Removes the doc from the FTS index so it no longer appears in searches.
        - Sends a 'doc_deleted' notification to all current subscribers.
        - Writes an audit log entry.
        - Version history is fully preserved (git-style: deletion is a record,
          not an erasure).

        Raises:
          UNAUTHORIZED    — doc_id does not belong to project_id
          DOC_NOT_FOUND   — document does not exist (or already deleted)
        """
        now = datetime.now(timezone.utc)

        document = (
            self._db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.project_space_id == project_space_id,
                Document.status == "active",
            )
            .first()
        )

        if document is None:
            raise AgentNexusError(
                error_code="DOC_NOT_FOUND",
                message=f"Document '{doc_id}' does not exist or is already deleted.",
                details={"doc_id": doc_id},
            )

        if document.subproject_id != project_id:
            raise AgentNexusError(
                error_code="UNAUTHORIZED",
                message=f"project_id '{project_id}' does not own document '{doc_id}'.",
                details={"doc_id": doc_id, "owner": document.subproject_id},
            )

        # Mark as deleted
        document.status = "deleted"
        document.deleted_at = now
        self._db.flush()

        # Remove from FTS index
        from agent_nexus.search.fts import remove_doc
        remove_doc(db=self._db, doc_id=doc_id)

        # Notify subscribers (if pipeline is wired up)
        if self._subscription_service is not None and self._notification_service is not None:
            subscriber_ids = self._subscription_service.get_subscribers(
                project_space_id=project_space_id,
                doc_id=doc_id,
                doc_type=document.doc_type,
            )
            # Version 0 signals deletion to subscribers
            self._notification_service.generate(
                doc_id=doc_id,
                version=0,
                subscriber_ids=subscriber_ids,
                project_space_id=project_space_id,
            )

        # Audit log
        self._audit.log(
            operation_type="delete_document",
            operator_project_id=project_id,
            target_id=doc_id,
            result="success",
            project_space_id=project_space_id,
        )

        return {"doc_id": doc_id, "status": "deleted"}

    def _get_file_path(
        self,
        project_space_id: str,
        subproject_id: str,
        doc_type: str,
        doc_variant: Optional[str],
    ) -> str:
        filename = _doc_filename(doc_type, doc_variant)
        return os.path.join(self._docs_root, project_space_id, "docs", subproject_id, filename)


