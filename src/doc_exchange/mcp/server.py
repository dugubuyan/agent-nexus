"""
MCP Server registration for the Doc Exchange Center.

Tool logic lives in ToolHandler (tools.py); this module only handles
MCP server setup and tool registration.

Runs in streamable-HTTP mode so multiple agents can connect simultaneously.
Default endpoint: http://0.0.0.0:10000/mcp
"""

import os

from mcp.server.fastmcp import FastMCP

from doc_exchange.mcp.dependencies import ServiceContainer, make_session_factory
from doc_exchange.mcp.tools import ToolHandler

mcp = FastMCP(
    "doc-exchange-center",
    host=os.environ.get("DOC_EXCHANGE_HOST", "0.0.0.0"),
    port=int(os.environ.get("DOC_EXCHANGE_PORT", "10086")),
)

# ---------------------------------------------------------------------------
# Session factory (engine config lives in dependencies.make_engine)
# ---------------------------------------------------------------------------

_DOCS_ROOT = os.environ.get("DOC_EXCHANGE_DOCS_ROOT", "./workspace")
_SessionLocal = make_session_factory()


def _get_handler() -> tuple[ToolHandler, any]:
    """Return (handler, session) — caller must commit/close the session."""
    session = _SessionLocal()
    container = ServiceContainer(db_session=session, docs_root=_DOCS_ROOT)
    return ToolHandler(container), session


# ---------------------------------------------------------------------------
# Tool registrations — delegate to ToolHandler
# ---------------------------------------------------------------------------


@mcp.tool()
async def push_document(
    project_id: str,
    doc_id: str,
    content: str,
    metadata: dict = {},
) -> dict:
    """Push a new document version to the exchange center."""
    handler, session = _get_handler()
    try:
        result = await handler.push_document(project_id, doc_id, content, metadata)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def patch_document(
    project_id: str,
    doc_id: str,
    base_version: int,
    patch: str,
) -> dict:
    """
    Apply a unified diff patch to an existing document, producing a new version.

    Use instead of push_document when only part of the document changed.
    Suitable for large documents where push_document would exceed payload limits.

    IMPORTANT: The patch MUST be generated programmatically (e.g., via difflib.unified_diff
    on the actual file content), NOT hand-written. Hand-written diffs are prone to
    line-number errors and will fail with PATCH_APPLY_FAILED.

    Recommended workflow:
      1. Read the current document with get_document to get the exact stored content.
      2. Write the modified content to a local file.
      3. Use a code tool to compute difflib.unified_diff between the two versions.
      4. Pass the resulting diff string as the patch parameter.

    base_version must match the current latest version; if not, returns
    PATCH_BASE_MISMATCH — call get_document to fetch latest and regenerate the patch.
    """
    handler, session = _get_handler()
    try:
        result = await handler.patch_document(project_id, doc_id, base_version, patch)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def get_document(
    project_id: str,
    doc_id: str,
    version: int | None = None,
) -> dict:
    """Retrieve a document (latest or specific version)."""
    handler, session = _get_handler()
    try:
        return await handler.get_document(project_id, doc_id, version)
    finally:
        session.close()


@mcp.tool()
async def get_my_updates_with_context(project_id: str) -> list[dict]:
    """
    Return all unread notifications with diff and full latest document content.
    One call gives everything needed to understand what changed and act on it.
    After processing, call ack_update for each update_id to mark as read.
    """
    handler, session = _get_handler()
    try:
        return await handler.get_my_updates_with_context(project_id)
    finally:
        session.close()


@mcp.tool()
async def get_my_updates(project_id: str) -> list[dict]:
    """
    [DEPRECATED] Return unread notification IDs only — use get_my_updates_with_context instead.
    get_my_updates_with_context returns diff + full content in one call, making this redundant.
    """
    handler, session = _get_handler()
    try:
        return await handler.get_my_updates(project_id)
    finally:
        session.close()


@mcp.tool()
async def ack_update(project_id: str, update_id: str) -> dict:
    """Acknowledge (mark as read) a notification."""
    handler, session = _get_handler()
    try:
        result = await handler.ack_update(project_id, update_id)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def get_my_tasks(project_id: str) -> list[dict]:
    """Return all pending/in-progress tasks for the given project."""
    handler, session = _get_handler()
    try:
        return await handler.get_my_tasks(project_id)
    finally:
        session.close()


@mcp.tool()
async def get_config(project_id: str, stage: str) -> dict:
    """
    [DEPRECATED] Return the config document for the given project and stage.
    Use get_document(project_id, f"{project_id}/config/{stage}") directly instead — it is equivalent and more flexible.
    """
    handler, session = _get_handler()
    try:
        return await handler.get_config(project_id, stage)
    finally:
        session.close()


@mcp.tool()
async def get_document_checklist(project_id: str) -> dict:
    """
    Return the document completeness checklist for the given project.

    Based on the project's current lifecycle stage, reports which documents
    are required or recommended, and which are present or missing.

    Call this at session start alongside get_my_updates_with_context to know
    what documents need to be created before proceeding with work.

    Returns:
      - required_docs: documents that must exist for the current stage
      - recommended_docs: documents that are helpful but not mandatory
      - completeness: summary string (e.g. "1/3 required docs present")
      - all_required_present: boolean — false means action is needed
      - suggested_doc_id: the doc_id to use when calling push_document to create a missing doc
    """
    handler, session = _get_handler()
    try:
        return await handler.get_document_checklist(project_id)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Admin tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def generate_instruction_file(
    project_name: str,
    project_space_id: str,
    client_type: str = "kiro",
) -> dict:
    """
    Generate an agent instruction file for the given project and IDE client.

    This implements the Service-Driven Agent Onboarding Protocol (SDAOP): the MCP
    service generates the onboarding document itself, so connecting agents require
    zero manual configuration.

    The agent should write the returned file_content to the returned file_path.
    On subsequent sessions, the client will auto-load the file and the agent will
    know how to interact with this service.

    client_type values and their target files:
      - "kiro"   → .kiro/steering/doc-exchange.md  (inclusion: auto frontmatter)
      - "claude" → CLAUDE.md                        (plain markdown)
      - "codex"  → AGENTS.md                        (plain markdown)
      - "cursor" → .cursor/rules/doc-exchange.mdc   (alwaysApply frontmatter)
    """
    handler, session = _get_handler()
    try:
        return await handler.generate_steering_file(project_name, project_space_id, client_type)
    finally:
        session.close()


@mcp.tool()
async def generate_steering_file(project_name: str, project_space_id: str) -> dict:
    """
    [DEPRECATED] Use generate_instruction_file instead, which supports multiple client types.
    Generates a Kiro steering file — equivalent to generate_instruction_file with client_type="kiro".
    """
    handler, session = _get_handler()
    try:
        return await handler.generate_steering_file(project_name, project_space_id, "kiro")
    finally:
        session.close()


@mcp.tool()
async def get_project_id_by_name(name: str, project_space_id: str) -> dict:
    """Look up a sub-project's project_id by its human-readable name."""
    handler, session = _get_handler()
    try:
        return await handler.get_project_id_by_name(name, project_space_id)
    finally:
        session.close()


@mcp.tool()
async def add_subscription(
    subscriber_project_id: str,
    project_space_id: str,
    target_doc_id: str | None = None,
    target_doc_type: str | None = None,
) -> dict:
    """
    Add a subscription rule. Provide target_doc_id for exact doc or target_doc_type for all docs of that type.
    """
    handler, session = _get_handler()
    try:
        result = await handler.add_subscription(
            subscriber_project_id, project_space_id, target_doc_id, target_doc_type
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def create_space(name: str) -> dict:
    """Create a new Project Space. Returns the space_id needed for registering projects."""
    handler, session = _get_handler()
    try:
        result = await handler.create_space(name)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def register_project(
    name: str,
    type: str,
    project_space_id: str,
    stage: str = "design",
) -> dict:
    """
    Register a new sub-project in the given project space.

    type: development | testing | ops | infra | shared | ...
    stage: design | development | testing | deployment | upgrade
    """
    handler, session = _get_handler()
    try:
        result = await handler.register_project(name, type, project_space_id, stage)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def list_projects(project_space_id: str) -> list[dict]:
    """List all sub-projects in the given project space."""
    handler, session = _get_handler()
    try:
        return await handler.list_projects(project_space_id)
    finally:
        session.close()


@mcp.tool()
async def publish_draft(
    project_id: str,
    doc_id: str,
    version: int,
) -> dict:
    """
    Confirm a draft document version, publishing it and triggering notifications.

    Only applicable when a document was pushed with pushed_by="system_llm", which
    creates a draft instead of publishing immediately. In normal agent workflows
    (where the agent calls push_document with its own project_id), documents are
    published automatically and this tool is not needed.

    Use this tool when a human or orchestration system wants to review and approve
    an LLM-generated document before it propagates to subscribers.

    Raises INVALID_STATUS_TRANSITION if the version does not exist or is already published.
    """
    handler, session = _get_handler()
    try:
        result = await handler.publish_draft(project_id, doc_id, version)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def list_documents(project_id: str) -> list[dict]:
    """List all documents belonging to the given sub-project."""
    handler, session = _get_handler()
    try:
        return await handler.list_documents(project_id)
    finally:
        session.close()
