"""
PlannerService: optional global coordination layer for AgentNexus.

Provides:
  - System-level read access across all spaces (no project_id restriction)
  - AI inference (chat, plan) via pluggable LLMClient
  - Cross-boundary write via draft gate (pushed_by="agent:planner" for drafts)

Design principle: Planner is an observer / coordinator; it proposes but does
not unilaterally publish.  All document writes default to draft=True.

Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 7.3
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from doc_exchange.models.entities import Document, DocumentVersion, ProjectSpace
from doc_exchange.services.schemas import PushRequest

if TYPE_CHECKING:
    from doc_exchange.mcp.dependencies import ServiceContainer
    from doc_exchange.planner.llm_client import LLMClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Actor identifier for write operations originating from PlannerService.
# Used in audit logs as the operator.
SYSTEM_ACTOR = "system"

# pushed_by value that triggers draft status in DocumentService._push_locked.
# (The core logic checks pushed_by.startswith("agent:") to mark a version as
# draft.  All agent:* actors automatically produce draft versions.)
_DRAFT_PUSHED_BY = "agent:planner"

# Maximum documents loaded when doc_ids is not specified for chat()
_CHAT_MAX_DOCS = 20


# ---------------------------------------------------------------------------
# PlannerService
# ---------------------------------------------------------------------------


class PlannerService:
    """Optional global coordination layer over ServiceContainer.

    Can be instantiated at any time and attached to any existing space.
    Does NOT register itself as a SubProject, does NOT own any space.

    Args:
        container: The ServiceContainer that holds all core services.
        llm_client: Optional LLMClient.  When None, chat/plan return
            ``{"error": "LLM_NOT_CONFIGURED"}``.
        require_review: Flag that signals to callers (and future auth layers)
            that write operations require human review.  Currently all writes
            already go through the draft gate, so this flag is mostly a
            semantic marker for downstream integrations.
    """

    def __init__(
        self,
        container: "ServiceContainer",
        llm_client: "LLMClient | None" = None,
        require_review: bool = True,
    ) -> None:
        self._c = container
        self._llm = llm_client
        self.require_review = require_review

    # ------------------------------------------------------------------ #
    # Read capabilities (system-level, no project_id restriction)         #
    # ------------------------------------------------------------------ #

    def list_spaces(self) -> list[dict]:
        """Return all ProjectSpaces (system-level view).

        Requirement 1.2 — Planner can list all spaces without a project_id.
        """
        spaces = self._c.db.query(ProjectSpace).all()
        return [
            {
                "space_id": s.id,
                "name": s.name,
                "status": s.status,
                "created_at": s.created_at.isoformat(),
            }
            for s in spaces
        ]

    def list_projects(self, space_id: str) -> list[dict]:
        """Return all SubProjects in a given space.

        Requirement 1.2 — system-level, no project_id check.
        """
        subprojects = self._c.project_service.list_subprojects(space_id)
        return [
            {
                "project_id": sp.id,
                "name": sp.name,
                "type": sp.type,
                "stage": sp.stage,
                "project_space_id": sp.project_space_id,
                "stage_updated_at": sp.stage_updated_at.isoformat(),
                "created_at": sp.created_at.isoformat(),
            }
            for sp in subprojects
        ]

    def read_document(
        self,
        space_id: str,
        doc_id: str,
        version: int | None = None,
    ) -> dict:
        """Retrieve a document (latest or specific version).

        Requirement 1.2 — system-level read, no project_id restriction.
        Raises DocExchangeError (DOC_NOT_FOUND / VERSION_NOT_FOUND) on error.
        """
        result = self._c.document_service.get(
            doc_id=doc_id,
            project_space_id=space_id,
            version=version,
        )
        return result.model_dump()

    def search(
        self,
        space_id: str,
        query: str,
        doc_type: str | None = None,
        subproject_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Full-text search across published documents in a space.

        Delegates to doc_exchange.search.fts.search (FTS5).
        Requirement 1.2 — cross-space FTS without project_id restriction.
        """
        from doc_exchange.search.fts import search as fts_search

        return fts_search(
            db=self._c.db,
            project_space_id=space_id,
            query=query,
            doc_type=doc_type,
            subproject_id=subproject_id,
            limit=limit,
        )

    # ------------------------------------------------------------------ #
    # AI inference                                                         #
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        space_id: str,
        question: str,
        doc_ids: list[str] | None = None,
    ) -> "str | dict":
        """Answer a question with cross-service document context.

        Requirements 2.1, 3.3

        If doc_ids is given, load exactly those documents from space_id.
        Otherwise load the most recently pushed documents (up to
        _CHAT_MAX_DOCS) in the space.

        Returns a string answer, or ``{"error": "LLM_NOT_CONFIGURED"}``
        when no LLM client is available.
        """
        if self._llm is None:
            return {"error": "LLM_NOT_CONFIGURED", "message": "No LLM client configured."}

        # Load context documents
        context_docs = self._load_context_docs(space_id, doc_ids)

        from doc_exchange.planner.prompts import build_chat_prompt

        system_prompt, user_prompt = build_chat_prompt(context_docs, question)
        return await self._llm.complete(system_prompt, user_prompt)

    async def plan(self, space_id: str, description: str) -> dict:
        """Propose a service decomposition for the given description.

        Requirements 2.2, 2.3

        Returns ``{"proposals": [...], "description": <description>}`` where
        each proposal has the shape ``{"name", "type", "suggested_docs"}``.

        Does NOT persist anything to the database; the result is a proposal
        only.  The caller decides whether to create the sub-projects.

        Returns ``{"error": "LLM_NOT_CONFIGURED"}`` when no LLM is available.
        Returns ``{"error": "INVALID_RESPONSE", "raw": ...}`` when the LLM
        response cannot be parsed as JSON.
        """
        if self._llm is None:
            return {"error": "LLM_NOT_CONFIGURED", "message": "No LLM client configured."}

        existing_projects = self.list_projects(space_id)

        from doc_exchange.planner.prompts import build_plan_prompt

        system_prompt, user_prompt = build_plan_prompt(description, existing_projects)
        raw = await self._llm.complete(system_prompt, user_prompt)

        # Parse the JSON array returned by the LLM
        try:
            proposals = json.loads(raw)
            if not isinstance(proposals, list):
                # Wrap a dict response just in case
                proposals = [proposals]
        except (json.JSONDecodeError, TypeError):
            return {"error": "INVALID_RESPONSE", "raw": raw}

        return {"proposals": proposals, "description": description}

    # ------------------------------------------------------------------ #
    # Write capabilities (draft gate)                                     #
    # ------------------------------------------------------------------ #

    def create_space(self, name: str) -> dict:
        """Create a new ProjectSpace.

        Requirement 1.3 — writes marked with SYSTEM_ACTOR in spirit;
        ProjectSpace has no ``pushed_by`` field so we just create it directly.
        """
        space = ProjectSpace(
            id=str(uuid.uuid4()),
            name=name,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        self._c.db.add(space)
        self._c.db.flush()
        return {
            "space_id": space.id,
            "name": space.name,
            "status": space.status,
            "created_at": space.created_at.isoformat(),
        }

    def register_project(
        self,
        space_id: str,
        name: str,
        type: str,
        stage: str = "design",
    ) -> dict:
        """Register a new SubProject in a space.

        Requirement 1.3
        """
        subproject = self._c.project_service.register(
            name=name,
            type=type,
            project_space_id=space_id,
            stage=stage,
        )
        self._c.db.flush()
        return {
            "project_id": subproject.id,
            "name": subproject.name,
            "type": subproject.type,
            "stage": subproject.stage,
            "project_space_id": subproject.project_space_id,
        }

    def push_document(
        self,
        space_id: str,
        doc_id: str,
        content: str,
        as_draft: bool = True,
    ) -> dict:
        """Push a document version on behalf of the Planner.

        Requirements 1.3, 7.3

        When ``as_draft=True`` (default), ``pushed_by`` is set to
        ``"agent:planner"`` so that DocumentService._push_locked marks the
        version as ``draft``.  This preserves service-boundary autonomy
        (cross-boundary writes never auto-publish).

        When ``as_draft=False``, ``pushed_by`` is set to ``SYSTEM_ACTOR``
        (``"system"``), which results in a published version.  Use with care.
        """
        pushed_by = _DRAFT_PUSHED_BY if as_draft else SYSTEM_ACTOR

        req = PushRequest(
            doc_id=doc_id,
            content=content,
            pushed_by=pushed_by,
            project_space_id=space_id,
        )
        result = self._c.document_service.push(req)
        return result.model_dump()

    def add_subscription(
        self,
        space_id: str,
        subscriber_project_id: str,
        target_doc_id: str | None = None,
        target_doc_type: str | None = None,
    ) -> dict:
        """Add a subscription rule for a SubProject.

        Requirement 1.3
        """
        rule = self._c.subscription_service.add_rule(
            subscriber_project_id=subscriber_project_id,
            project_space_id=space_id,
            target_doc_id=target_doc_id,
            target_doc_type=target_doc_type,
        )
        return {
            "rule_id": rule.id,
            "subscriber_project_id": rule.subscriber_project_id,
            "target_doc_id": rule.target_doc_id,
            "target_doc_type": rule.target_doc_type,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _load_context_docs(
        self,
        space_id: str,
        doc_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Load documents for chat context.

        If ``doc_ids`` is provided, load exactly those documents.
        Otherwise load up to ``_CHAT_MAX_DOCS`` most recently pushed documents
        in the space (latest published version preferred).
        """
        from doc_exchange.models.entities import DocumentVersionContent

        results: list[dict[str, Any]] = []

        if doc_ids:
            for did in doc_ids:
                try:
                    dr = self._c.document_service.get(
                        doc_id=did,
                        project_space_id=space_id,
                    )
                    results.append(
                        {
                            "doc_id": dr.doc_id,
                            "content": dr.content,
                            "doc_type": "",  # not on DocumentResult; enrich below
                        }
                    )
                except Exception:
                    # Skip documents that cannot be found or read
                    pass
            # Enrich doc_type from DB for those we loaded
            for item in results:
                doc_row = (
                    self._c.db.query(Document)
                    .filter(
                        Document.id == item["doc_id"],
                        Document.project_space_id == space_id,
                    )
                    .first()
                )
                if doc_row:
                    item["doc_type"] = doc_row.doc_type
        else:
            # Load up to _CHAT_MAX_DOCS most recently pushed docs in the space
            # We join DocumentVersion to sort by pushed_at desc
            rows = (
                self._c.db.query(Document, DocumentVersion, DocumentVersionContent)
                .join(
                    DocumentVersion,
                    (DocumentVersion.document_id == Document.id)
                    & (DocumentVersion.version == Document.latest_version)
                    & (DocumentVersion.project_space_id == space_id),
                )
                .join(
                    DocumentVersionContent,
                    DocumentVersionContent.version_id == DocumentVersion.id,
                )
                .filter(Document.project_space_id == space_id)
                .order_by(DocumentVersion.pushed_at.desc())
                .limit(_CHAT_MAX_DOCS)
                .all()
            )
            for doc_row, ver_row, content_row in rows:
                results.append(
                    {
                        "doc_id": doc_row.id,
                        "content": content_row.content,
                        "doc_type": doc_row.doc_type,
                    }
                )

        return results
