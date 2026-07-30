"""
ToolHandler: implements all MCP tool logic in a testable class.

Each method validates project_id, checks ProjectSpace archive status for
write operations, then delegates to the appropriate service.

Covers Requirements 8.1-8.5, 10.6, 10.7.
"""

from agent_nexus.models.entities import ProjectSpace, SubProject
from agent_nexus.services.errors import AgentNexusError
from agent_nexus.services.schemas import PushRequest

from .dependencies import ServiceContainer

VALID_CONFIG_VARIANTS = {"dev", "test", "prod"}


class ToolHandler:
    """Contains all MCP tool logic, decoupled from the MCP server registration."""

    def __init__(self, container: ServiceContainer):
        self._c = container

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_subproject(self, project_id: str) -> SubProject | None:
        """Return the SubProject for project_id, searching across all spaces."""
        return (
            self._c.db.query(SubProject)
            .filter(SubProject.id == project_id)
            .first()
        )

    def _get_space(self, project_space_id: str) -> ProjectSpace | None:
        return (
            self._c.db.query(ProjectSpace)
            .filter(ProjectSpace.id == project_space_id)
            .first()
        )

    def _validate_project(self, project_id: str) -> SubProject:
        """
        Validate that project_id exists.

        Returns the SubProject on success.
        Raises AgentNexusError(UNAUTHORIZED) if not found.
        """
        subproject = self._get_subproject(project_id)
        if subproject is None:
            raise AgentNexusError(
                error_code="UNAUTHORIZED",
                message=f"project_id '{project_id}' does not exist.",
                details={"project_id": project_id},
            )
        return subproject

    def _check_not_archived(self, project_space_id: str) -> None:
        """
        Check that the ProjectSpace is not archived.

        Raises AgentNexusError(SPACE_ARCHIVED) if archived.
        Covers Requirements 10.6, 10.7.
        """
        space = self._get_space(project_space_id)
        if space is not None and space.status == "archived":
            raise AgentNexusError(
                error_code="SPACE_ARCHIVED",
                message="Project space is archived. Write operations are not allowed.",
                details={"project_space_id": project_space_id},
            )

    @staticmethod
    def _error_dict(exc: AgentNexusError) -> dict:
        return {"error": exc.error_code, "message": exc.message}

    # ------------------------------------------------------------------
    # Tool: get_document  (read)
    # ------------------------------------------------------------------

    async def get_document(
        self,
        project_id: str,
        doc_id: str,
        version: int | None = None,
    ) -> dict:
        """
        Retrieve a document (latest or specific version).

        Requirements 8.1, 8.4
        """
        try:
            subproject = self._validate_project(project_id)
            result = self._c.document_service.get(
                doc_id=doc_id,
                project_space_id=subproject.project_space_id,
                version=version,
            )
            return result.model_dump()
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Tool: ack_update  (write — checks archive)
    # ------------------------------------------------------------------

    async def ack_update(self, project_id: str, update_id: str) -> dict:
        """
        Acknowledge (mark as read) a notification.

        Requirements 8.1, 8.4, 10.6, 10.7
        """
        try:
            subproject = self._validate_project(project_id)
            self._check_not_archived(subproject.project_space_id)

            self._c.notification_service.ack(
                update_id=update_id,
                project_id=project_id,
                project_space_id=subproject.project_space_id,
            )
            return {"status": "ok", "update_id": update_id}
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Tool: get_my_tasks  (read)
    # ------------------------------------------------------------------

    async def get_my_tasks(self, project_id: str) -> list[dict]:
        """
        Return all pending/in-progress tasks for the given project_id.

        Requirements 8.1, 8.4
        """
        try:
            subproject = self._validate_project(project_id)
            tasks = self._c.task_service.get_pending(
                project_id=project_id,
                project_space_id=subproject.project_space_id,
            )
            return [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "status": t.status,
                    "trigger_doc_id": t.trigger_doc_id,
                    "trigger_version": t.trigger_version,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tasks
            ]
        except AgentNexusError as exc:
            return [self._error_dict(exc)]

    # ------------------------------------------------------------------
    # Tool: get_document_checklist  (read)
    # ------------------------------------------------------------------

    # Minimal stage-agnostic fallback — applies when no custom task/checklist
    # document exists for the project.
    #
    # Stage is NOT a document classifier. The authoritative "what documents
    # does this project need" answer comes from the project itself via a
    # task/checklist document (doc_id="{project_id}/task/checklist"). This
    # fallback is intentionally minimal — just enough to nudge a new project
    # toward writing its first requirement document.
    #
    # See §14 of docs/agentnexus-v4-ideas.md for the long-term direction.
    _BUILTIN_FALLBACK: dict = {
        "required": ["requirement"],
        "recommended": [],
    }

    _DOC_TYPE_DESCRIPTIONS: dict = {
        "requirement":       "Functional and non-functional requirements",
        "design":            "Architecture and technical design",
        "api":               "API contracts (REST, GraphQL, gRPC, etc.)",
        "config":            "Environment configuration (dev/test/prod)",
        "config/dev":        "Development environment configuration",
        "config/test":       "Test environment configuration",
        "config/prod":       "Production environment configuration",
        "schema":            "Database or message queue schema",
        "schema/db":         "Database schema",
        "runbook":           "Operational procedures",
        "runbook/deploy":    "Deployment procedure",
        "runbook/rollback":  "Rollback procedure",
        "changelog":         "Release notes and breaking changes",
        "changelog/notes":   "Cumulative release notes",
        "changelog/breaking":"Breaking changes for current release",
        "test-plan":         "Test strategy and test cases",
        "task":              "Work items and implementation plans",
        "task/checklist":    "Custom document checklist for this project",
    }

    @staticmethod
    def _parse_checklist_markdown(content: str) -> dict[str, list[dict]]:
        """
        Parse a custom checklist document in the agreed Markdown format:

            ## Required
            - doc_type: description

            ## Recommended
            - doc_type: description

        Returns {"required": [...], "recommended": [...]} where each item is
        {"doc_type": str, "description": str}.
        """
        import re
        result: dict[str, list[dict]] = {"required": [], "recommended": []}
        current_section: str | None = None

        for line in content.splitlines():
            stripped = line.strip()
            # Detect section headings
            if re.match(r"^#{1,3}\s+Required\s*$", stripped, re.IGNORECASE):
                current_section = "required"
            elif re.match(r"^#{1,3}\s+Recommended\s*$", stripped, re.IGNORECASE):
                current_section = "recommended"
            elif stripped.startswith("-") and current_section:
                # Parse "- doc_type: description" or "- doc_type"
                item = stripped.lstrip("- ").strip()
                if ":" in item:
                    doc_type, _, description = item.partition(":")
                    doc_type = doc_type.strip()
                    description = description.strip()
                else:
                    doc_type = item.strip()
                    description = ""
                if doc_type:
                    result[current_section].append({
                        "doc_type": doc_type,
                        "description": description,
                    })
        return result

    def _get_builtin_rules(self) -> dict:
        """Return the minimal universal fallback rules.

        Projects should declare what documents they need via a
        task/checklist document; this fallback only ensures a new project
        gets a signal to write its first requirement document.
        See v4-ideas §14 for the rationale.
        """
        return self._BUILTIN_FALLBACK

    async def get_document_checklist(self, project_id: str) -> dict:
        """
        Return the document completeness checklist for the given project.

        Priority:
          1. If a custom checklist document exists at {project_id}/task/checklist,
             parse and use it (Markdown list format — see HTTP POST /api/documents for the format).
          2. Otherwise fall back to the built-in rules for the project's (type, stage).

        Use this at session start to know what documents to create before proceeding.
        To define a custom checklist, push a document with:
          doc_id = "{project_id}/task/checklist"
          content = Markdown with ## Required and ## Recommended sections,
                    each containing "- doc_type: description" list items.
        """
        try:
            subproject = self._validate_project(project_id)

            from agent_nexus.models.entities import Document, DocumentVersion
            from agent_nexus.services import blob_store

            # Fetch all existing documents for this project
            existing_docs = (
                self._c.db.query(Document)
                .filter(
                    Document.subproject_id == project_id,
                    Document.project_space_id == subproject.project_space_id,
                )
                .all()
            )

            # Build lookup: prefix → {doc_id, latest_version}
            # Also store full variant paths for exact matching
            present_map: dict[str, dict] = {}  # keyed by doc_type prefix
            present_full_map: dict[str, dict] = {}  # keyed by "doc_type/variant" or "doc_type"
            custom_checklist_content: str | None = None
            checklist_doc_id = f"{project_id}/task/checklist"

            for doc in existing_docs:
                prefix = doc.doc_type
                full_key = f"{doc.doc_type}/{doc.doc_variant}" if doc.doc_variant else doc.doc_type
                entry = {"doc_id": doc.id, "latest_version": doc.latest_version}
                # Keep highest version per prefix
                if prefix not in present_map or doc.latest_version > present_map[prefix]["latest_version"]:
                    present_map[prefix] = entry
                present_full_map[full_key] = entry
                # Check for custom checklist
                if doc.id == checklist_doc_id and doc.latest_version > 0:
                    ver = (
                        self._c.db.query(DocumentVersion)
                        .filter(
                            DocumentVersion.document_id == doc.id,
                            DocumentVersion.version == doc.latest_version,
                        )
                        .first()
                    )
                    if ver:
                        content_rec = blob_store.content_for_version(self._c.db, ver)
                        if content_rec:
                            custom_checklist_content = content_rec

            # Determine rules: custom or built-in
            if custom_checklist_content:
                parsed = self._parse_checklist_markdown(custom_checklist_content)
                required_spec = parsed["required"]    # list of {"doc_type", "description"}
                recommended_spec = parsed["recommended"]
                checklist_source = "custom"
            else:
                rules = self._get_builtin_rules()
                required_spec = [
                    {"doc_type": dt, "description": self._DOC_TYPE_DESCRIPTIONS.get(dt, "")}
                    for dt in rules.get("required", [])
                ]
                recommended_spec = [
                    {"doc_type": dt, "description": self._DOC_TYPE_DESCRIPTIONS.get(dt, "")}
                    for dt in rules.get("recommended", [])
                ]
                checklist_source = "builtin"

            def _is_present(doc_type: str) -> dict | None:
                """Check presence using exact full path first, then prefix."""
                if doc_type in present_full_map:
                    return present_full_map[doc_type]
                prefix = doc_type.split("/")[0]
                if prefix in present_map:
                    return present_map[prefix]
                return None

            def _build_entries(spec: list[dict]) -> list[dict]:
                entries = []
                for item in spec:
                    dt = item["doc_type"]
                    desc = item.get("description") or self._DOC_TYPE_DESCRIPTIONS.get(dt, "")
                    found = _is_present(dt)
                    if found:
                        entries.append({
                            "doc_type": dt,
                            "status": "present",
                            "doc_id": found["doc_id"],
                            "latest_version": found["latest_version"],
                            "description": desc,
                        })
                    else:
                        entries.append({
                            "doc_type": dt,
                            "status": "missing",
                            "description": desc,
                            "suggested_doc_id": f"{project_id}/{dt}",
                        })
                return entries

            required_entries = _build_entries(required_spec)
            recommended_entries = _build_entries(recommended_spec)
            present_required = sum(1 for e in required_entries if e["status"] == "present")
            total_required = len(required_entries)

            result = {
                "project_id": project_id,
                "project_name": subproject.name,
                "project_type": subproject.type,
                "checklist_source": checklist_source,
                "required_docs": required_entries,
                "recommended_docs": recommended_entries,
                "completeness": f"{present_required}/{total_required} required docs present",
                "all_required_present": present_required == total_required,
            }
            if checklist_source == "builtin":
                from agent_nexus.mcp.sdaop import get_public_url
                public_url = get_public_url()
                result["hint"] = (
                    f"Using built-in fallback checklist (no custom task/checklist found for this project). "
                    f"To create missing documents, use the HTTP POST endpoint: "
                    f'curl -X POST {public_url}/api/documents -H "Content-Type: application/json" '
                    f'-d \'{{"project_id": "{project_id}", "doc_id": "<suggested_doc_id>", "content": "<content>"}}\'. '
                    f"To define your own checklist, push a document with doc_id='{checklist_doc_id}' "
                    "containing ## Required and ## Recommended Markdown sections."
                )
            return result
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Admin tool: generate_steering_file
    # ------------------------------------------------------------------

    async def generate_steering_file(
        self, project_name: str, project_space_id: str, client_type: str = "kiro"
    ) -> dict:
        """
        Generate an agent instruction file (SDAOP) for the given client type.
        Reads from spec/instructions/ template files.
        """
        import json
        import os
        from datetime import datetime, timezone

        from agent_nexus.mcp.sdaop import compute_sdaop_version, get_public_url

        public_url = get_public_url()

        spec_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "spec"
        )
        instructions_dir = os.path.join(spec_dir, "instructions")

        # Load client config
        clients_config_path = os.path.join(instructions_dir, "clients.json")
        with open(clients_config_path, "r", encoding="utf-8") as f:
            clients = json.load(f)

        client_key = client_type.lower()
        client_config = clients.get(client_key, clients["default"])

        # Load common template
        common_path = os.path.join(instructions_dir, "common.md")
        with open(common_path, "r", encoding="utf-8") as f:
            common_content = f.read()

        # Load client-specific template
        template_path = os.path.join(instructions_dir, client_config["template"])
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        push_script_path = client_config["push_script_path"]
        file_path = client_config["file_path"]

        # Render common content with dynamic values
        common_rendered = (
            common_content
            .replace("{{PROJECT_NAME}}", project_name)
            .replace("{{PROJECT_SPACE_ID}}", project_space_id)
            .replace("{{PROJECT_ID}}", f"<your_project_id>")
            .replace("{{PUSH_SCRIPT_PATH}}", push_script_path)
            .replace("{{SERVER_URL}}", public_url)
        )

        # Render client template, injecting common content
        content = template_content.replace("{{COMMON}}", common_rendered)
        # Also catch SERVER_URL occurrences in client-specific templates
        content = content.replace("{{SERVER_URL}}", public_url)

        # Inject SDAOP version metadata as YAML frontmatter so agents and
        # the version-check step at session start can read it. If the client
        # template already opens with a YAML frontmatter block (e.g. cursor's
        # `alwaysApply: true`), merge our fields into it; otherwise prepend
        # a new frontmatter block.
        sdaop_version = compute_sdaop_version(client_type)
        generated_at = datetime.now(timezone.utc).isoformat()
        sdaop_fields = (
            f"sdaop_version: {sdaop_version}\n"
            f"generated_at: {generated_at}\n"
            f"client_type: {client_type}\n"
        )
        if content.startswith("---\n"):
            # Find the closing `---` of the existing frontmatter
            end = content.find("\n---\n", 4)
            if end == -1:
                # Malformed; fall back to prepend
                content = f"---\n{sdaop_fields}---\n\n{content}"
            else:
                existing = content[4:end + 1]  # contents between the two --- lines
                rest = content[end + 5:]       # everything after the closing ---\n
                content = f"---\n{existing}{sdaop_fields}---\n{rest}"
        else:
            content = f"---\n{sdaop_fields}---\n\n{content}"

        # Load and render push script (SERVER_URL filled, PROJECT_ID left as placeholder)
        push_script_path_full = os.path.join(spec_dir, "push-tool.py")
        with open(push_script_path_full, "r", encoding="utf-8") as f:
            push_script_content = f.read()
        push_script_rendered = push_script_content.replace("{{SERVER_URL}}", public_url)

        return {
            "file_path": file_path,
            "file_content": content,
            "sdaop_version": sdaop_version,
            "push_script": {
                "instruction": (
                    f"Write push_script.content to {push_script_path} in your workspace. "
                    f"Then replace {{{{PROJECT_ID}}}} in that file with your actual project_id "
                    f"(the one returned by get_project_id_by_name in step 1 of the workflow)."
                ),
                "target_file": push_script_path,
                "content": push_script_rendered,
            },
            "nexus_state_update": {
                "instruction": (
                    "Persist the new sdaop_version in .kiro/nexus-state.json under "
                    "the special key `_sdaop_version`. Future sessions will use this "
                    "to detect when the service-side protocol has changed."
                ),
                "file": ".kiro/nexus-state.json",
                "operation": "merge",
                "entry": {"_sdaop_version": sdaop_version},
            },
            "instruction": (
                f"1. Write file_content to {file_path}. "
                f"2. Write push_script.content to {push_script_path} and replace "
                f"{{{{PROJECT_ID}}}} with your project_id."
            ),
        }

    # ------------------------------------------------------------------
    # Admin tool: get_project_id_by_name
    # ------------------------------------------------------------------

    async def get_project_id_by_name(
        self, name: str, project_space_id: str
    ) -> dict:
        """Look up a sub-project's project_id by its name within a space."""
        from agent_nexus.models.entities import SubProject
        subproject = (
            self._c.db.query(SubProject)
            .filter(
                SubProject.name == name,
                SubProject.project_space_id == project_space_id,
            )
            .first()
        )
        if subproject is None:
            return {"error": "PROJECT_NOT_FOUND", "message": f"No project named '{name}' in this space."}
        return {
            "project_id": subproject.id,
            "name": subproject.name,
            "type": subproject.type,
            "stage": subproject.stage,
        }

    # ------------------------------------------------------------------
    # Admin tool: add_subscription
    # ------------------------------------------------------------------

    async def add_subscription(
        self,
        subscriber_project_id: str,
        project_space_id: str,
        target_doc_id: str | None = None,
        target_doc_type: str | None = None,
    ) -> dict:
        """
        Add a subscription rule for a sub-project.
        Provide either target_doc_id (exact doc) or target_doc_type (all docs of that type).
        """
        try:
            rule = self._c.subscription_service.add_rule(
                subscriber_project_id=subscriber_project_id,
                project_space_id=project_space_id,
                target_doc_id=target_doc_id,
                target_doc_type=target_doc_type,
            )
            return {
                "rule_id": rule.id,
                "subscriber_project_id": rule.subscriber_project_id,
                "target_doc_id": rule.target_doc_id,
                "target_doc_type": rule.target_doc_type,
            }
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Admin tool: create_space
    # ------------------------------------------------------------------

    async def create_space(self, name: str) -> dict:
        """Create a new Project Space and return its space_id."""
        import uuid
        from datetime import datetime, timezone
        from agent_nexus.models.entities import ProjectSpace

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
        }

    # ------------------------------------------------------------------
    # Admin tool: register_project
    # ------------------------------------------------------------------

    async def register_project(
        self,
        name: str,
        type: str,
        project_space_id: str,
        stage: str = "design",
    ) -> dict:
        """
        Register a new sub-project in the given project space.

        type: development | testing | ops | infra | ...
        stage: design | development | testing | deployment | upgrade
        """
        try:
            subproject = self._c.project_service.register(
                name=name,
                type=type,
                project_space_id=project_space_id,
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
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Admin tool: list_projects
    # ------------------------------------------------------------------

    async def list_projects(self, project_space_id: str) -> list[dict]:
        """List all sub-projects in the given project space."""
        subprojects = self._c.project_service.list_subprojects(project_space_id)
        return [
            {
                "project_id": sp.id,
                "name": sp.name,
                "type": sp.type,
                "stage": sp.stage,
                "stage_updated_at": sp.stage_updated_at.isoformat(),
                "created_at": sp.created_at.isoformat(),
            }
            for sp in subprojects
        ]

    # ------------------------------------------------------------------
    # Admin tool: publish_draft
    # ------------------------------------------------------------------

    async def publish_draft(
        self,
        project_id: str,
        doc_id: str,
        version: int,
    ) -> dict:
        """
        Confirm a draft document version, publishing it and triggering notifications.

        Raises INVALID_STATUS_TRANSITION if version doesn't exist or is already published.
        """
        try:
            subproject = self._validate_project(project_id)
            result = self._c.document_service.publish_draft(
                doc_id=doc_id,
                version=version,
                project_space_id=subproject.project_space_id,
            )
            return result
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Admin tool: list_documents
    # ------------------------------------------------------------------

    async def list_documents(self, project_id: str) -> list[dict]:
        """List all documents belonging to the given sub-project."""
        try:
            subproject = self._validate_project(project_id)
            from agent_nexus.models.entities import Document
            docs = (
                self._c.db.query(Document)
                .filter(
                    Document.subproject_id == project_id,
                    Document.project_space_id == subproject.project_space_id,
                    Document.status == "active",
                )
                .all()
            )
            return [
                {
                    "doc_id": d.id,
                    "doc_type": d.doc_type,
                    "latest_version": d.latest_version,
                    "doc_variant": d.doc_variant,
                    "created_at": d.created_at.isoformat(),
                }
                for d in docs
            ]
        except AgentNexusError as exc:
            return [self._error_dict(exc)]

    # ------------------------------------------------------------------
    # Tool: delete_document
    # ------------------------------------------------------------------

    async def delete_document(self, project_id: str, doc_id: str) -> dict:
        """
        Soft-delete a document owned by project_id.

        The document is marked as deleted and removed from search results.
        Version history is fully preserved (git-style: deletion is recorded,
        not erased). Subscribers receive a notification with version=0 to
        signal that the document has been removed.

        Raises UNAUTHORIZED if the document does not belong to project_id.
        Raises DOC_NOT_FOUND if the document does not exist or is already deleted.
        """
        try:
            subproject = self._validate_project(project_id)
            return self._c.document_service.delete(
                doc_id=doc_id,
                project_id=project_id,
                project_space_id=subproject.project_space_id,
            )
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Tool: get_my_updates_with_context  (read — one-call update check)
    # ------------------------------------------------------------------

    async def get_my_updates_with_context(self, project_id: str) -> list[dict]:
        """
        Return all unread notifications with diff and optionally full document content.

        Each item contains:
          - update_id: notification id (use with ack_update when done)
          - doc_id: which document changed
          - doc_type: type of document
          - new_version: the new version number
          - diff: unified diff showing what changed (+ added, - removed)
          - latest_content: full content of the latest version (always included;
            use get_document if you need a specific older version)

        After processing each update, call ack_update(project_id, update_id).
        """
        try:
            subproject = self._validate_project(project_id)
            notifications = self._c.notification_service.get_unread(
                project_id=project_id,
                project_space_id=subproject.project_space_id,
            )

            if not notifications:
                return []

            from agent_nexus.models.entities import Document, DocumentVersion
            from agent_nexus.services import blob_store
            import difflib

            results = []
            for n in notifications:
                item: dict = {
                    "update_id": n.id,
                    "doc_id": n.document_id,
                    "new_version": n.version,
                    "diff": None,
                    "latest_content": None,
                }

                # Get doc type
                doc = (
                    self._c.db.query(Document)
                    .filter(Document.id == n.document_id)
                    .first()
                )
                item["doc_type"] = doc.doc_type if doc else "unknown"

                # Get latest content
                latest_ver = (
                    self._c.db.query(DocumentVersion)
                    .filter(
                        DocumentVersion.document_id == n.document_id,
                        DocumentVersion.version == n.version,
                    )
                    .first()
                )
                if latest_ver:
                    latest_content = blob_store.content_for_version(self._c.db, latest_ver)
                    item["latest_content"] = latest_content if latest_content is not None else ""

                # Get previous version content for diff
                if n.version > 1:
                    prev_ver = (
                        self._c.db.query(DocumentVersion)
                        .filter(
                            DocumentVersion.document_id == n.document_id,
                            DocumentVersion.version == n.version - 1,
                        )
                        .first()
                    )
                    if prev_ver:
                        prev_content = blob_store.content_for_version(self._c.db, prev_ver)
                        if prev_content and item["latest_content"]:
                            old_lines = prev_content.splitlines(keepends=True)
                            new_lines = item["latest_content"].splitlines(keepends=True)
                            diff_lines = list(difflib.unified_diff(
                                old_lines, new_lines,
                                fromfile=f"v{n.version - 1}",
                                tofile=f"v{n.version}",
                                lineterm="",
                            ))
                            item["diff"] = "".join(diff_lines) if diff_lines else "（内容无变化）"
                        else:
                            item["diff"] = "（旧版本内容已归档，无法生成 diff）"
                else:
                    item["diff"] = "（首次发布，无历史版本）"

                results.append(item)

            return results
        except AgentNexusError as exc:
            return [self._error_dict(exc)]

    # ------------------------------------------------------------------
    # Tool: get_config  (read)
    # ------------------------------------------------------------------

    async def get_config(self, project_id: str, stage: str) -> dict:
        """
        Return the config document for the given project_id and stage.

        The doc_id is constructed as {project_id}/config/{stage}.

        Requirements 8.1, 8.4, 6.2, 6.3
        """
        try:
            subproject = self._validate_project(project_id)

            if stage not in VALID_CONFIG_VARIANTS:
                raise AgentNexusError(
                    error_code="INVALID_STAGE",
                    message=f"stage '{stage}' is not valid. Must be one of: {sorted(VALID_CONFIG_VARIANTS)}.",
                    details={"valid_variants": sorted(VALID_CONFIG_VARIANTS)},
                )

            doc_id = f"{project_id}/config/{stage}"
            result = self._c.document_service.get(
                doc_id=doc_id,
                project_space_id=subproject.project_space_id,
            )
            return result.model_dump()
        except AgentNexusError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Tool: search_documents  (read — FTS5 full-text search)
    # ------------------------------------------------------------------

    async def search_documents(
        self,
        project_space_id: str,
        query: str,
        doc_type: str | None = None,
        subproject_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Full-text search across all published documents in a project space.

        Supports FTS5 query syntax:
          - Keywords:      authentication
          - Phrases:       "user authentication"
          - Prefix:        auth*
          - Boolean:       authentication NOT oauth  (use NOT, not AND NOT)

        Results are ranked by BM25 relevance (most relevant first).
        Each result includes a snippet with matched terms highlighted using >>> / <<<.

        Requirements 2.1-2.7
        """
        try:
            # Cap limit to 50
            limit = min(limit, 50)

            from agent_nexus.search.fts import search as fts_search
            results = fts_search(
                db=self._c.db,
                project_space_id=project_space_id,
                query=query,
                doc_type=doc_type,
                subproject_id=subproject_id,
                limit=limit,
            )

            # Enrich each result with latest_version from the documents table
            from agent_nexus.models.entities import Document
            enriched = []
            for r in results:
                doc = (
                    self._c.db.query(Document)
                    .filter(Document.id == r["doc_id"])
                    .first()
                )
                enriched.append({
                    "doc_id": r["doc_id"],
                    "subproject_id": r["subproject_id"],
                    "doc_type": r["doc_type"],
                    "latest_version": doc.latest_version if doc else None,
                    "snippet": r["snippet"],
                    "rank": r["rank"],
                })
            return enriched

        except AgentNexusError as exc:
            return [self._error_dict(exc)]

    # ------------------------------------------------------------------
    # Planner tools (read-only AI inference + cross-project overview)
    # ------------------------------------------------------------------

    async def planner_chat(
        self,
        space_id: str,
        question: str,
        doc_ids: list[str] | None = None,
    ) -> dict:
        """
        Ask the Planner a question with cross-service document context (read-only).

        Delegates to PlannerService.chat(). Returns {"answer": <str>} on
        success, or an error dict when the LLM is not configured or a
        AgentNexusError occurs.

        Requirements 2.1, 4.1
        """
        try:
            result = await self._c.planner_service.chat(
                space_id=space_id,
                question=question,
                doc_ids=doc_ids,
            )
            # Normalise: str answer → {"answer": str}; dict (error etc.) passes through
            if isinstance(result, str):
                return {"answer": result}
            return result
        except AgentNexusError as exc:
            return self._error_dict(exc)

    async def planner_plan(self, space_id: str, description: str) -> dict:
        """
        Propose a service decomposition (SubProjects + deps + draft docs).

        Returns a proposal dict only; does NOT persist anything to the database.
        The caller decides whether to act on the proposal.

        Delegates to PlannerService.plan().

        Requirements 2.2, 4.1
        """
        try:
            return await self._c.planner_service.plan(
                space_id=space_id,
                description=description,
            )
        except AgentNexusError as exc:
            return self._error_dict(exc)

    async def planner_overview(self, space_id: str | None = None) -> dict:
        """
        Cross-subproject overview. Read-only global view.

        space_id is optional: if omitted, returns ALL spaces and their projects.
        space_id may be a UUID or a space name (human-readable).
        """
        from agent_nexus.mcp.sdaop import get_public_url
        _public_url = get_public_url()
        _ONBOARDING = {
            "onboarding": {
                "note": (
                    "You are a NEW agent connecting to AgentNexus for the first time. "
                    "Follow these steps before doing any other work:"
                ),
                "steps": [
                    {
                        "step": 1,
                        "action": "register_project",
                        "description": (
                            "Register yourself as a sub-project. "
                            "Pick a short snake_case name for your service/agent (e.g. 'auth-service', 'data-pipeline'). "
                            "Use the space_id from the spaces list above."
                        ),
                        "example": {
                            "tool": "register_project",
                            "args": {
                                "name": "<your-service-name>",
                                "type": "development",
                                "project_space_id": "<space_id>",
                                "stage": "design",
                            },
                        },
                    },
                    {
                        "step": 2,
                        "action": "generate_instruction_file",
                        "description": (
                            "Generate and write your local instruction file so you remember "
                            "your project_id and workflow in future sessions. "
                            "Write the returned file_content to the returned file_path immediately."
                        ),
                        "example": {
                            "tool": "generate_instruction_file",
                            "args": {
                                "project_name": "<your-service-name>",
                                "project_space_id": "<space_id>",
                                "client_type": "kiro",
                            },
                        },
                    },
                    {
                        "step": 3,
                        "action": "push_via_http",
                        "description": (
                            "Push your first document via HTTP POST. doc_id MUST be prefixed with your project_id. "
                            "Format: '<project_id>/<doc_type>'. Start with a requirement document. "
                            "Write the document as a local file first, then POST its content — "
                            "this keeps document content out of LLM context (zero token cost)."
                        ),
                        "example": {
                            "method": "POST",
                            "url": f"{_public_url}/api/documents",
                            "headers": {"Content-Type": "application/json"},
                            "body": {
                                "project_id": "<project_id from step 1>",
                                "doc_id": "<project_id>/requirement",
                                "content": "## Requirements\n\n...",
                            },
                            "curl": (
                                f"curl -X POST {_public_url}/api/documents "
                                "-H 'Content-Type: application/json' "
                                "-d '{\"project_id\":\"<pid>\",\"doc_id\":\"<pid>/requirement\",\"content\":\"## Requirements\\n\\n...\"}'"
                            ),
                        },
                        "after_push": (
                            "The HTTP response contains a 'nexus_state_update' field. "
                            "Merge the 'entry' into .kiro/nexus-state.json immediately. "
                            "This is your local version anchor — like .git/refs — so future sessions "
                            "can detect drift and send base_version with subsequent pushes."
                        ),
                    },
                ],
                "doc_id_rule": "ALWAYS prefix doc_id with your project_id. e.g. if project_id='abc-123', doc_id must start with 'abc-123/'.",
            }
        }

        try:
            if space_id:
                resolved = self._c.planner_service._resolve_space_id(space_id)
                projects_raw = self._c.planner_service.list_projects(resolved)
                projects_out = []
                for proj in projects_raw:
                    pid = proj["project_id"]
                    docs = await self.list_documents(pid)
                    projects_out.append({
                        "project_id": pid,
                        "name": proj["name"],
                        "type": proj["type"],
                        "stage": proj["stage"],
                        "documents": docs,
                    })
                return {"space_id": resolved, "projects": projects_out}
            else:
                result = self._c.planner_service.global_overview()
                # Inject onboarding hint for agents with no prior context
                result.update(_ONBOARDING)
                return result
        except AgentNexusError as exc:
            return self._error_dict(exc)

    async def planner_attribution(
        self,
        space_id: str,
        project_id: str | None = None,
        principal: str | None = None,
    ) -> dict:
        """
        Read-only Principal attribution query — the "git log --author" of AgentNexus.

        Answers the two directions of the (role, boundary) matrix (v4-pre §8):
          - project_id given → which principals have written into this boundary
          - principal given  → which boundaries/docs this principal has acted on
          - neither          → a boundary × principal activity matrix for the space

        principal is self-attested; versions pushed without attestation (the
        degenerate single-actor case) appear under "(unattested)".
        """
        from agent_nexus.models.entities import Document, DocumentVersion

        try:
            resolved = self._c.planner_service._resolve_space_id(space_id)
        except AgentNexusError as exc:
            return self._error_dict(exc)

        q = (
            self._c.db.query(
                DocumentVersion.document_id,
                Document.subproject_id,
                Document.doc_type,
                DocumentVersion.version,
                DocumentVersion.pushed_principal,
                DocumentVersion.actor,
                DocumentVersion.status,
                DocumentVersion.pushed_at,
            )
            .join(Document, DocumentVersion.document_id == Document.id)
            .filter(DocumentVersion.project_space_id == resolved)
        )
        if project_id:
            q = q.filter(Document.subproject_id == project_id)
        if principal:
            q = q.filter(DocumentVersion.pushed_principal == principal)

        rows = q.order_by(
            Document.subproject_id,
            DocumentVersion.document_id,
            DocumentVersion.version,
        ).all()

        UNATTR = "(unattested)"
        records = [
            {
                "doc_id": doc_id,
                "subproject_id": sub,
                "doc_type": dtype,
                "version": ver,
                "principal": prin or UNATTR,
                "actor": actor,
                "status": status,
                "pushed_at": pushed_at.isoformat() if pushed_at else None,
            }
            for (doc_id, sub, dtype, ver, prin, actor, status, pushed_at) in rows
        ]

        # Direction 1: single boundary → which principals contributed
        if project_id and not principal:
            return {
                "boundary": project_id,
                "principals": sorted({r["principal"] for r in records}),
                "attributions": records,
                "note": (
                    "actor = owning SubProject of the write; principal = self-attested role who acted. "
                    "Multiple principals on one boundary = multi-actor collaboration, not multi-owner (v4-pre §8.4)."
                ),
            }

        # Direction 2: single principal → which boundaries it acted on
        if principal and not project_id:
            return {
                "principal": principal,
                "boundaries": sorted({r["subproject_id"] for r in records}),
                "activity": records,
            }

        # Both given: filtered list
        if principal and project_id:
            return {"boundary": project_id, "principal": principal, "attributions": records}

        # Neither: boundary × principal activity matrix
        matrix: dict[str, dict[str, int]] = {}
        for r in records:
            bucket = matrix.setdefault(r["subproject_id"], {})
            bucket[r["principal"]] = bucket.get(r["principal"], 0) + 1
        return {
            "space_id": resolved,
            "matrix": matrix,
            "note": "boundary → {principal: write_count}. The (role, boundary) activity distribution (v4 §18 matrix).",
        }

    async def planner_delete_project(self, project_id: str, space_id: str) -> dict:
        """Delete a sub-project. space_id may be UUID or name. Documents retained."""
        try:
            return self._c.planner_service.delete_project(project_id, space_id)
        except AgentNexusError as exc:
            return self._error_dict(exc)
