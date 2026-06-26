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
            hdrs = dict(response.headers)
            hdrs.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=hdrs,
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

        hdrs = dict(response.headers)
        hdrs.pop("content-length", None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=hdrs,
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
    """Return unread notifications with diff and full document content; call ack_update after processing each."""
    handler, session = _get_handler()
    try:
        return await handler.get_my_updates_with_context(project_id)
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
    """[DEPRECATED] Use get_document(project_id, f"{project_id}/config/{stage}") instead."""
    handler, session = _get_handler()
    try:
        return await handler.get_config(project_id, stage)
    finally:
        session.close()


@mcp.tool()
async def get_document_checklist(project_id: str) -> dict:
    """Return which documents the project still needs to create; call at session start to check completeness."""
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
    """Generate the AgentNexus instruction/steering file for your workspace. Call this first if no instruction file exists."""
    handler, session = _get_handler()
    try:
        return await handler.generate_steering_file(project_name, project_space_id, client_type)
    finally:
        session.close()


@mcp.tool()
async def get_sdaop_version(client_type: str = "kiro") -> dict:
    """Return the current SDAOP protocol version; compare with local nexus-state.json to detect if regeneration is needed."""
    from agent_nexus.mcp.sdaop import compute_sdaop_version
    return {
        "sdaop_version": compute_sdaop_version(client_type),
        "client_type": client_type,
    }


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
    """Register a new sub-project in the given project space. Stage is informational only (default "design")."""
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
    """Publish a draft document version (created by actor="system_llm"), triggering subscriber notifications."""
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
    """Soft-delete a document owned by project_id. Only the owning project may delete; version history is preserved."""
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
    """Full-text search (FTS5 syntax) across all published documents in a space, ranked by relevance."""
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
    """Ask the Planner a question with cross-service document context. Read-only; optionally scope to specific doc_ids."""
    handler, session = _get_handler()
    try:
        return await handler.planner_chat(space_id, question, doc_ids)
    finally:
        session.close()


@mcp.tool()
async def planner_plan(space_id: str, description: str) -> dict:
    """Propose a service decomposition (sub-projects + dependencies + docs). Read-only; does NOT persist anything."""
    handler, session = _get_handler()
    try:
        return await handler.planner_plan(space_id, description)
    finally:
        session.close()


@mcp.tool()
async def planner_overview(space_id: str | None = None) -> dict:
    """Read-only cross-subproject overview. If space_id is omitted, returns all spaces and their projects."""
    handler, session = _get_handler()
    try:
        return await handler.planner_overview(space_id)
    finally:
        session.close()


@mcp.tool()
async def planner_delete_project(project_id: str, space_id: str) -> dict:
    """Delete a sub-project by project_id; owned documents are retained for audit."""
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

