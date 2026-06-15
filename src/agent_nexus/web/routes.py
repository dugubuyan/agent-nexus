"""
Web route registrations for the Doc Exchange Dashboard.

Call register_web_routes(mcp, get_handler=_get_handler) once from server.py
to attach all web routes to the existing FastMCP server (same process, same port).

The get_handler parameter avoids circular imports: _get_handler is defined in
server.py and passed in rather than imported here.
"""

import difflib
import json
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse

from agent_nexus.services.errors import AgentNexusError

# Jinja2 environment — loaded once at module import time
_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=True)


def _default_serializer(obj):
    """JSON serializer for objects not serializable by default json encoder."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _json_response(data) -> JSONResponse:
    """Return a JSONResponse, handling datetime serialization via isoformat."""
    return JSONResponse(
        content=json.loads(json.dumps(data, default=_default_serializer))
    )

# Error codes that map to 404 status
_NOT_FOUND_CODES = {"DOC_NOT_FOUND", "VERSION_NOT_FOUND"}


def _error_response(exc: AgentNexusError) -> JSONResponse:
    status_code = 404 if exc.error_code in _NOT_FOUND_CODES else 400
    return JSONResponse(
        {"error": exc.error_code, "message": exc.message},
        status_code=status_code,
    )


def _missing_param(name: str) -> JSONResponse:
    return JSONResponse(
        {"error": "MISSING_PARAM", "message": f"Missing required query parameter: '{name}'"},
        status_code=400,
    )


def register_web_routes(mcp, get_handler) -> None:
    """Register all web dashboard HTTP routes on the FastMCP instance."""

    # ------------------------------------------------------------------
    # GET /api/spaces  →  planner_service.list_spaces()
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/spaces", methods=["GET"])
    async def api_spaces(request: Request) -> JSONResponse:
        handler, session = get_handler()
        try:
            return _json_response(handler._c.planner_service.list_spaces())
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # GET /api/spaces/{space_id}/projects  →  planner_service.list_projects(space_id)
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/spaces/{space_id}/projects", methods=["GET"])
    async def api_projects(request: Request) -> JSONResponse:
        space_id = request.path_params["space_id"]
        handler, session = get_handler()
        try:
            return _json_response(handler._c.planner_service.list_projects(space_id))
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # GET /api/projects/{project_id}/documents  →  handler.list_documents(project_id) [async]
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/projects/{project_id}/documents", methods=["GET"])
    async def api_documents(request: Request) -> JSONResponse:
        project_id = request.path_params["project_id"]
        handler, session = get_handler()
        try:
            result = await handler.list_documents(project_id)
            return _json_response(result)
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # GET /api/spaces/{space_id}/search
    # query: q (required), doc_type (optional), subproject_id (optional), limit (int, default 10)
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/spaces/{space_id}/search", methods=["GET"])
    async def api_search(request: Request) -> JSONResponse:
        space_id = request.path_params["space_id"]
        q = request.query_params.get("q")
        if not q:
            return _missing_param("q")
        doc_type = request.query_params.get("doc_type") or None
        subproject_id = request.query_params.get("subproject_id") or None
        try:
            limit = int(request.query_params.get("limit", "10"))
        except (ValueError, TypeError):
            limit = 10

        handler, session = get_handler()
        try:
            results = handler._c.planner_service.search(
                space_id=space_id,
                query=q,
                doc_type=doc_type,
                subproject_id=subproject_id,
                limit=limit,
            )
            return _json_response(results)
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # NOTE: /versions and /diff routes must be registered BEFORE the
    # catch-all /api/documents/{doc_id:path} route to prevent "versions"
    # and "diff" from being captured as part of doc_id.
    # ------------------------------------------------------------------

    # GET /api/documents/{doc_id:path}/versions
    # query: space_id (required)

    @mcp.custom_route("/api/documents/{doc_id:path}/versions", methods=["GET"])
    async def api_doc_versions(request: Request) -> JSONResponse:
        doc_id = request.path_params["doc_id"]
        # Strip trailing /versions suffix added by the router
        if doc_id.endswith("/versions"):
            doc_id = doc_id[: -len("/versions")]
        space_id = request.query_params.get("space_id")
        if not space_id:
            return _missing_param("space_id")

        handler, session = get_handler()
        try:
            versions = handler._c.document_service.list_versions(doc_id, space_id)
            return JSONResponse([v.model_dump(mode="json") for v in versions])
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # GET /api/documents/{doc_id:path}/diff
    # query: space_id (required), from (int, required), to (int, required)

    @mcp.custom_route("/api/documents/{doc_id:path}/diff", methods=["GET"])
    async def api_doc_diff(request: Request) -> JSONResponse:
        doc_id = request.path_params["doc_id"]
        # Strip trailing /diff suffix added by the router
        if doc_id.endswith("/diff"):
            doc_id = doc_id[: -len("/diff")]
        space_id = request.query_params.get("space_id")
        if not space_id:
            return _missing_param("space_id")

        from_str = request.query_params.get("from")
        to_str = request.query_params.get("to")
        if not from_str:
            return _missing_param("from")
        if not to_str:
            return _missing_param("to")

        try:
            from_ver = int(from_str)
            to_ver = int(to_str)
        except ValueError:
            return JSONResponse(
                {"error": "INVALID_PARAM", "message": "'from' and 'to' must be integers"},
                status_code=400,
            )

        handler, session = get_handler()
        try:
            old_result = handler._c.document_service.get(doc_id, space_id, version=from_ver)
            new_result = handler._c.document_service.get(doc_id, space_id, version=to_ver)

            old_lines = old_result.content.splitlines(keepends=True)
            new_lines = new_result.content.splitlines(keepends=True)

            diff_lines = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"v{from_ver}",
                    tofile=f"v{to_ver}",
                    lineterm="",
                )
            )
            diff_text = "\n".join(diff_lines)

            return JSONResponse({"diff": diff_text, "from": from_ver, "to": to_ver})
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # GET /api/documents/{doc_id:path}
    # query: space_id (required), version (optional int)
    # Must be registered AFTER /versions and /diff routes.
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/documents/{doc_id:path}", methods=["GET"])
    async def api_document(request: Request) -> JSONResponse:
        doc_id = request.path_params["doc_id"]
        space_id = request.query_params.get("space_id")
        if not space_id:
            return _missing_param("space_id")

        version_str = request.query_params.get("version")
        version: int | None = None
        if version_str is not None:
            try:
                version = int(version_str)
            except ValueError:
                return JSONResponse(
                    {"error": "INVALID_PARAM", "message": "'version' must be an integer"},
                    status_code=400,
                )

        handler, session = get_handler()
        try:
            result = handler._c.planner_service.read_document(space_id, doc_id, version)
            return _json_response(result)
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # POST /api/documents  →  带外全量写入（零 LLM token，复用 push 流水线）
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/documents", methods=["POST"])
    async def http_push_document(request: Request) -> JSONResponse:
        """
        Out-of-band full-content document write endpoint.

        Accepts full document content via HTTP body — not MCP tool-call params —
        so large documents incur zero LLM token cost. Delegates to the same
        DocumentService.push pipeline used by the MCP push_document tool
        (same validation, archive check, FTS update, notification pipeline).
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON"},
                status_code=400,
            )

        missing = [k for k in ("project_id", "doc_id", "content") if not body.get(k)]
        if missing:
            return JSONResponse(
                {"error": "MISSING_FIELD", "message": f"Missing required fields: {missing}"},
                status_code=400,
            )

        handler, session = get_handler()
        try:
            result = await handler.push_document(
                body["project_id"],
                body["doc_id"],
                body["content"],
                body.get("metadata", {}),
            )
            session.commit()
            return JSONResponse(result)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # POST /api/chat  →  planner_service.chat() via SSE stream
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/chat", methods=["POST"])
    async def api_chat(request: Request) -> StreamingResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON"},
                status_code=400,
            )

        space_id = body.get("space_id")
        question = body.get("question")

        if not space_id:
            return JSONResponse(
                {"error": "MISSING_PARAM", "message": "Missing required field: 'space_id'"},
                status_code=400,
            )
        if not question:
            return JSONResponse(
                {"error": "MISSING_PARAM", "message": "Missing required field: 'question'"},
                status_code=400,
            )

        doc_ids = body.get("doc_ids") or None

        async def event_stream():
            handler, session = get_handler()
            try:
                result = await handler._c.planner_service.chat(space_id, question, doc_ids)
                if isinstance(result, dict) and "error" in result:
                    yield f"event: error\ndata: {json.dumps(result)}\n\n"
                else:
                    # result is a str (non-streaming first version)
                    token_text = result if isinstance(result, str) else str(result)
                    yield f"data: {json.dumps({'token': token_text})}\n\n"
                yield "event: done\ndata: {}\n\n"
            finally:
                session.close()

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # POST /api/plan  →  planner_service.plan() → JSON proposal (no DB write)
    # ------------------------------------------------------------------

    @mcp.custom_route("/api/plan", methods=["POST"])
    async def api_plan(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "INVALID_JSON", "message": "Request body must be valid JSON"},
                status_code=400,
            )

        space_id = body.get("space_id")
        description = body.get("description")

        if not space_id:
            return JSONResponse(
                {"error": "MISSING_PARAM", "message": "Missing required field: 'space_id'"},
                status_code=400,
            )
        if not description:
            return JSONResponse(
                {"error": "MISSING_PARAM", "message": "Missing required field: 'description'"},
                status_code=400,
            )

        handler, session = get_handler()
        try:
            result = await handler._c.planner_service.plan(space_id, description)
            return _json_response(result)
        except AgentNexusError as exc:
            return _error_response(exc)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # GET /  →  Jinja2-rendered dashboard page
    # ------------------------------------------------------------------

    @mcp.custom_route("/", methods=["GET"])
    async def dashboard_index(request: Request) -> HTMLResponse:
        """Render the main dashboard page using Jinja2."""
        template = _jinja_env.get_template("dashboard.html")
        return HTMLResponse(template.render())
