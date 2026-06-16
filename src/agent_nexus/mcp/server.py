"""
MCP Server registration for the AgentNexus.

Tool logic lives in ToolHandler (tools.py); this module only handles
MCP server setup and tool registration.

Runs in streamable-HTTP mode so multiple agents can connect simultaneously.
Default endpoint: http://0.0.0.0:10000/mcp
"""

import json
import os

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from agent_nexus.mcp.dependencies import ServiceContainer, make_session_factory
from agent_nexus.mcp.tools import ToolHandler

mcp = FastMCP(
    "agent-nexus",
    host=os.environ.get("AGENT_NEXUS_HOST", "0.0.0.0"),
    port=int(os.environ.get("AGENT_NEXUS_PORT", "10086")),
)


class _SessionErrorMiddleware(BaseHTTPMiddleware):
    """
    Intercept MCP -32600 'Missing session ID' responses on /mcp and enrich
    the error message with a pointer to the REST fallback endpoint.

    Agents that mistakenly POST raw JSON to /mcp instead of using the MCP
    tool-call protocol receive a clear redirect rather than a cryptic error.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Only inspect JSON responses on the /mcp path
        if request.url.path != "/mcp" or "application/json" not in response.headers.get("content-type", ""):
            return response

        # Buffer the body to inspect it
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            data = json.loads(body)
        except Exception:
            # Not valid JSON — return as-is
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        # Check for the specific session error
        error = data.get("error", {})
        if (
            isinstance(error, dict)
            and error.get("code") == -32600
            and "Missing session ID" in error.get("message", "")
        ):
            error["message"] = (
                "Bad Request: Missing session ID. "
                "Do not POST directly to /mcp — that requires an active MCP session. "
                "To push a document without a session, use the REST endpoint instead: "
                "POST /api/documents with JSON body {project_id, doc_id, content}."
            )
            body = json.dumps(data).encode()

        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


# Attach middleware to the underlying Starlette app after FastMCP is set up.
# We monkey-patch streamable_http_app to wrap it once on first access.
_original_streamable_http_app = mcp.streamable_http_app.__func__  # type: ignore[attr-defined]


def _patched_streamable_http_app(self):
    app = _original_streamable_http_app(self)
    app.add_middleware(_SessionErrorMiddleware)
    return app


import types  # noqa: E402

mcp.streamable_http_app = types.MethodType(_patched_streamable_http_app, mcp)

# ---------------------------------------------------------------------------
# Session factory (engine config lives in dependencies.make_engine)
# ---------------------------------------------------------------------------

_DOCS_ROOT = os.environ.get("AGENT_NEXUS_DOCS_ROOT", "./workspace")
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
    ** Call this first if you have no instruction file for this server. **

    If no AgentNexus instruction file exists in your workspace yet
    (.kiro/steering/agent-nexus.md, CLAUDE.md, AGENTS.md, or
    .cursor/rules/agent-nexus.mdc depending on your client), call this tool
    immediately. Write the returned file_content to the returned file_path,
    then reload it. Subsequent sessions will auto-load the file and you will
    know how to interact with this service without calling this again.

    This implements the Service-Driven Agent Onboarding Protocol (SDAOP): the
    service generates its own onboarding document so agents require zero manual
    configuration.

    client_type values and their target files:
      - "kiro"   → .kiro/steering/agent-nexus.md  (inclusion: auto frontmatter)
      - "claude" → CLAUDE.md                        (plain markdown)
      - "codex"  → AGENTS.md                        (plain markdown)
      - "cursor" → .cursor/rules/agent-nexus.mdc   (alwaysApply frontmatter)
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


@mcp.tool()
async def delete_document(project_id: str, doc_id: str) -> dict:
    """
    Soft-delete a document owned by project_id.

    The document is marked as deleted and disappears from list_documents,
    get_document, and search_documents. Version history is fully preserved
    (git-style: deletion is a record, not an erasure). Subscribers receive
    a notification with version=0 signalling that the document was removed.

    Only the owning project (the one whose project_id is the prefix of doc_id)
    may delete the document. Returns {"doc_id": ..., "status": "deleted"} on
    success, or an error dict with UNAUTHORIZED / DOC_NOT_FOUND.
    """
    handler, session = _get_handler()
    try:
        result = await handler.delete_document(project_id, doc_id)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@mcp.tool()
async def search_documents(
    project_space_id: str,
    query: str,
    doc_type: str | None = None,
    subproject_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Full-text search across all published documents in a project space.

    Supports FTS5 query syntax:
    - Keywords:  authentication
    - Phrases:   "user authentication"
    - Prefix:    auth*
    - Boolean:   authentication NOT oauth

    Results are ranked by BM25 relevance (most relevant first).
    Each result includes a snippet with matched terms highlighted using >>> / <<<.

    Optional filters:
    - doc_type: limit to a specific document type (requirement, design, api, etc.)
    - subproject_id: limit to a specific sub-project

    Returns [] when no matches are found.
    Returns {"error": "INVALID_QUERY", ...} on FTS5 syntax errors.
    """
    handler, session = _get_handler()
    try:
        return await handler.search_documents(
            project_space_id, query, doc_type, subproject_id, limit
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Planner tools (read-only AI inference + cross-project overview)
# ---------------------------------------------------------------------------


@mcp.tool()
async def planner_chat(
    space_id: str,
    question: str,
    doc_ids: list[str] | None = None,
) -> dict:
    """
    Ask the Planner a question with cross-service document context. Read-only.

    Returns {answer: str} on success, or {error: ...} when the LLM is not
    configured or a document/space error occurs.

    If doc_ids is specified, only those documents are loaded as context.
    Otherwise the Planner retrieves relevant documents via full-text search
    (supports FTS5 query syntax) across the entire space.

    Requirements 2.1, 4.1
    """
    handler, session = _get_handler()
    try:
        return await handler.planner_chat(space_id, question, doc_ids)
    finally:
        session.close()


@mcp.tool()
async def planner_plan(space_id: str, description: str) -> dict:
    """
    Propose a service decomposition (SubProjects + dependencies + initial document recommendations).

    Returns proposal only, does NOT persist to database.
    Human confirmation required before creating any SubProjects.

    The returned dict contains a list of suggested SubProjects with their
    types, dependencies, and recommended initial documents. Use the existing
    register_project and push_document tools to act on the proposal.

    Requirements 2.2, 4.1
    """
    handler, session = _get_handler()
    try:
        return await handler.planner_plan(space_id, description)
    finally:
        session.close()


@mcp.tool()
async def planner_overview(space_id: str | None = None) -> dict:
    """
    Cross-subproject overview. Read-only global view.

    space_id is OPTIONAL. If omitted, returns all spaces and their projects —
    no prior knowledge of space IDs needed. space_id may also be a space name.

    To get started with no context: call planner_overview() with no arguments.
    """
    handler, session = _get_handler()
    try:
        return await handler.planner_overview(space_id)
    finally:
        session.close()


@mcp.tool()
async def planner_delete_project(project_id: str, space_id: str) -> dict:
    """
    Delete a sub-project by project_id. space_id may be a UUID or space name.

    Documents owned by the project are NOT deleted — they are retained for
    audit purposes. Returns {"deleted": True} on success.
    """
    handler, session = _get_handler()
    try:
        result = await handler.planner_delete_project(project_id, space_id)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ---------------------------------------------------------------------------
# Web Dashboard routes
# ---------------------------------------------------------------------------

from agent_nexus.web.routes import register_web_routes  # noqa: E402

register_web_routes(mcp, get_handler=_get_handler)

# ---------------------------------------------------------------------------
# MCP Resources — static onboarding + per-client templates
#
# Two categories:
#   1. agent-nexus://onboarding  — INSTRUCTIONAL: agent must follow these steps
#   2. agent-nexus://templates/* — TEMPLATES: fill in placeholders before use
#
# Placeholders in templates use {{UPPER_CASE}} convention to make them obvious.
# ---------------------------------------------------------------------------


import json as _json
import os as _os

_SPEC_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__)))),
    "spec"
)
_INSTRUCTIONS_DIR = _os.path.join(_SPEC_DIR, "instructions")


def _load_onboarding() -> str:
    path = _os.path.join(_SPEC_DIR, "onboarding.md")
    with open(path) as f:
        return f.read()


def _load_steering_resource(client_key: str) -> str:
    """Load and render a steering template for MCP resource (placeholders kept for user to fill)."""
    clients_path = _os.path.join(_INSTRUCTIONS_DIR, "clients.json")
    with open(clients_path) as f:
        clients = _json.load(f)
    config = clients.get(client_key, clients["default"])

    common_path = _os.path.join(_INSTRUCTIONS_DIR, "common.md")
    with open(common_path) as f:
        common = f.read()

    template_path = _os.path.join(_INSTRUCTIONS_DIR, config["template"])
    with open(template_path) as f:
        template = f.read()

    return template.replace("{{COMMON}}", common)


def _load_push_tool() -> str:
    """Load the push tool script template."""
    path = _os.path.join(_SPEC_DIR, "push-tool.py")
    with open(path) as f:
        return f.read()


@mcp.resource(
    "agent-nexus://onboarding",
    name="AgentNexus Onboarding",
    description=(
        "INSTRUCTIONAL: Read this first if you have no local AgentNexus instruction file. "
        "Guides you through registering your project, creating your steering file, "
        "and pushing your first document."
    ),
    mime_type="text/markdown",
)
def resource_onboarding() -> str:
    return _load_onboarding()


@mcp.resource(
    "agent-nexus://templates/steering/kiro",
    name="[TEMPLATE] Kiro Steering File",
    description=(
        "[TEMPLATE] Kiro steering file for .kiro/steering/agent-nexus.md. "
        "Replace {{PROJECT_NAME}}, {{PROJECT_ID}}, {{PROJECT_SPACE_ID}} with your values, "
        "then write to .kiro/steering/agent-nexus.md in your workspace."
    ),
    mime_type="text/markdown",
)
def resource_steering_kiro() -> str:
    return _load_steering_resource("kiro")


@mcp.resource(
    "agent-nexus://templates/steering/claude",
    name="[TEMPLATE] Claude Instruction File",
    description=(
        "[TEMPLATE] Claude instruction file for CLAUDE.md. "
        "Replace {{PROJECT_NAME}}, {{PROJECT_ID}}, {{PROJECT_SPACE_ID}} with your values, "
        "then write to CLAUDE.md in your workspace root."
    ),
    mime_type="text/markdown",
)
def resource_steering_claude() -> str:
    return _load_steering_resource("claude")


@mcp.resource(
    "agent-nexus://templates/steering/codex",
    name="[TEMPLATE] Codex Agent File",
    description=(
        "[TEMPLATE] Codex/OpenAI agent file for AGENTS.md. "
        "Replace {{PROJECT_NAME}}, {{PROJECT_ID}}, {{PROJECT_SPACE_ID}} with your values, "
        "then write to AGENTS.md in your workspace root."
    ),
    mime_type="text/markdown",
)
def resource_steering_codex() -> str:
    return _load_steering_resource("codex")


@mcp.resource(
    "agent-nexus://templates/steering/cursor",
    name="[TEMPLATE] Cursor Rules File",
    description=(
        "[TEMPLATE] Cursor rules file for .cursor/rules/agent-nexus.mdc. "
        "Replace {{PROJECT_NAME}}, {{PROJECT_ID}}, {{PROJECT_SPACE_ID}} with your values, "
        "then write to .cursor/rules/agent-nexus.mdc in your workspace."
    ),
    mime_type="text/markdown",
)
def resource_steering_cursor() -> str:
    return _load_steering_resource("cursor")


@mcp.resource(
    "agent-nexus://templates/push-tool.py",
    name="[TEMPLATE] Push Tool Script",
    description=(
        "[TEMPLATE] Python script for pushing documents via HTTP (no MCP session required). "
        "Replace {{PROJECT_ID}} with your project UUID, then run: "
        "python nexus_push.py <doc_type> <file.md>. "
        "Also updates .kiro/nexus-state.json automatically after each push."
    ),
    mime_type="text/x-python",
)
def resource_push_tool() -> str:
    return _load_push_tool()

