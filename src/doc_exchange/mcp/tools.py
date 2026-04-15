"""
ToolHandler: implements all MCP tool logic in a testable class.

Each method validates project_id, checks ProjectSpace archive status for
write operations, then delegates to the appropriate service.

Covers Requirements 8.1-8.5, 10.6, 10.7.
"""

from doc_exchange.models.entities import ProjectSpace, SubProject
from doc_exchange.services.errors import DocExchangeError
from doc_exchange.services.schemas import PushRequest

from .dependencies import ServiceContainer

VALID_CONFIG_STAGES = {"dev", "test", "prod"}


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
        Raises DocExchangeError(UNAUTHORIZED) if not found.
        """
        subproject = self._get_subproject(project_id)
        if subproject is None:
            raise DocExchangeError(
                error_code="UNAUTHORIZED",
                message=f"project_id '{project_id}' does not exist.",
                details={"project_id": project_id},
            )
        return subproject

    def _check_not_archived(self, project_space_id: str) -> None:
        """
        Check that the ProjectSpace is not archived.

        Raises DocExchangeError(SPACE_ARCHIVED) if archived.
        Covers Requirements 10.6, 10.7.
        """
        space = self._get_space(project_space_id)
        if space is not None and space.status == "archived":
            raise DocExchangeError(
                error_code="SPACE_ARCHIVED",
                message="Project space is archived. Write operations are not allowed.",
                details={"project_space_id": project_space_id},
            )

    @staticmethod
    def _error_dict(exc: DocExchangeError) -> dict:
        return {"error": exc.error_code, "message": exc.message}

    # ------------------------------------------------------------------
    # Tool: push_document  (write — checks archive)
    # ------------------------------------------------------------------

    async def push_document(
        self,
        project_id: str,
        doc_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        """
        Push a new document version.

        Validates project_id, checks space not archived, then delegates to
        DocumentService.push().

        Requirements 8.1, 8.4, 10.6, 10.7
        """
        if metadata is None:
            metadata = {}
        try:
            subproject = self._validate_project(project_id)
            self._check_not_archived(subproject.project_space_id)

            req = PushRequest(
                doc_id=doc_id,
                content=content,
                pushed_by=project_id,
                project_space_id=subproject.project_space_id,
                metadata=metadata,
            )
            result = self._c.document_service.push(req)
            return result.model_dump()
        except DocExchangeError as exc:
            return self._error_dict(exc)

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
        except DocExchangeError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Tool: get_my_updates  (read)
    # ------------------------------------------------------------------

    async def get_my_updates(self, project_id: str) -> list[dict]:
        """
        Return all unread notifications for the given project_id.

        Requirements 8.1, 8.4
        """
        try:
            subproject = self._validate_project(project_id)
            notifications = self._c.notification_service.get_unread(
                project_id=project_id,
                project_space_id=subproject.project_space_id,
            )
            return [
                {
                    "id": n.id,
                    "doc_id": n.document_id,
                    "version": n.version,
                    "created_at": n.created_at.isoformat(),
                    "status": n.status,
                }
                for n in notifications
            ]
        except DocExchangeError as exc:
            return [self._error_dict(exc)]

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
        except DocExchangeError as exc:
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
        except DocExchangeError as exc:
            return [self._error_dict(exc)]

    # ------------------------------------------------------------------
    # Admin tool: generate_steering_file
    # ------------------------------------------------------------------

    async def generate_steering_file(
        self, project_name: str, project_space_id: str
    ) -> dict:
        """
        Generate the content for a .kiro/steering/doc-exchange.md file.
        The sub-project Kiro should create this file to enable auto doc-update checks.
        """
        content = f"""---
inclusion: auto
---

# 文档交换中心接入指南

本项目已接入团队文档交换中心（Doc Exchange Center）。

## 项目信息

- project_name: `{project_name}`
- project_space_id: `{project_space_id}`
- MCP 服务地址: `http://localhost:10086/mcp`

## 工作流程

### 每次开始工作时

1. 调用 `get_project_id_by_name(name="{project_name}", project_space_id="{project_space_id}")` 获取本项目的 project_id
2. 调用 `get_my_updates_with_context(project_id=<上一步返回的project_id>)` 检查是否有文档更新

返回结果包含：
- `update_id`：通知 ID，处理完后需调用 `ack_update` 标记已读
- `doc_type`：文档类型（requirement/design/api/config/task）
- `new_version`：新版本号
- `diff`：与上一版本的差异（unified diff 格式，`+` 为新增，`-` 为删除）
- `latest_content`：最新完整文档内容

**处理规则：**
- 有更新时：根据 `diff` 定位需要修改的代码位置，根据 `latest_content` 确认修改内容，完成后调用 `ack_update(project_id, update_id)` 标记已读
- 无更新时：直接继续正常工作

### 完成重要功能或文档变更时

先获取 project_id（调用 `get_project_id_by_name`），再调用 `push_document` 将本项目的最新文档推送到文档交换中心。

doc_id 格式：`{{project_id}}/{{doc_type}}`，例如：
- `{{project_id}}/requirement`
- `{{project_id}}/api`
- `{{project_id}}/design`
"""
        return {
            "steering_file_path": ".kiro/steering/doc-exchange.md",
            "steering_file_content": content,
            "instruction": "请将 steering_file_content 的内容写入 steering_file_path 文件。",
        }

    # ------------------------------------------------------------------
    # Admin tool: get_project_id_by_name
    # ------------------------------------------------------------------

    async def get_project_id_by_name(
        self, name: str, project_space_id: str
    ) -> dict:
        """Look up a sub-project's project_id by its name within a space."""
        from doc_exchange.models.entities import SubProject
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
        except DocExchangeError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Admin tool: create_space
    # ------------------------------------------------------------------

    async def create_space(self, name: str) -> dict:
        """Create a new Project Space and return its space_id."""
        import uuid
        from datetime import datetime, timezone
        from doc_exchange.models.entities import ProjectSpace

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
        except DocExchangeError as exc:
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
        except DocExchangeError as exc:
            return self._error_dict(exc)

    # ------------------------------------------------------------------
    # Admin tool: list_documents
    # ------------------------------------------------------------------

    async def list_documents(self, project_id: str) -> list[dict]:
        """List all documents belonging to the given sub-project."""
        try:
            subproject = self._validate_project(project_id)
            from doc_exchange.models.entities import Document
            docs = (
                self._c.db.query(Document)
                .filter(
                    Document.subproject_id == project_id,
                    Document.project_space_id == subproject.project_space_id,
                )
                .all()
            )
            return [
                {
                    "doc_id": d.id,
                    "doc_type": d.doc_type,
                    "latest_version": d.latest_version,
                    "config_stage": d.config_stage,
                    "created_at": d.created_at.isoformat(),
                }
                for d in docs
            ]
        except DocExchangeError as exc:
            return [self._error_dict(exc)]

    # ------------------------------------------------------------------
    # Tool: get_my_updates_with_context  (read — one-call update check)
    # ------------------------------------------------------------------

    async def get_my_updates_with_context(self, project_id: str) -> list[dict]:
        """
        Return all unread notifications with diff and latest document content included.

        Each item contains:
          - update_id: notification id (use with ack_update when done)
          - doc_id: which document changed
          - doc_type: type of document
          - new_version: the new version number
          - diff: text summary of what changed (line-level diff)
          - latest_content: full content of the latest version
        """
        try:
            subproject = self._validate_project(project_id)
            notifications = self._c.notification_service.get_unread(
                project_id=project_id,
                project_space_id=subproject.project_space_id,
            )

            if not notifications:
                return []

            from doc_exchange.models.entities import Document, DocumentVersion, DocumentVersionContent
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
                    content_rec = (
                        self._c.db.query(DocumentVersionContent)
                        .filter(DocumentVersionContent.version_id == latest_ver.id)
                        .first()
                    )
                    item["latest_content"] = content_rec.content if content_rec else ""

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
                        prev_content_rec = (
                            self._c.db.query(DocumentVersionContent)
                            .filter(DocumentVersionContent.version_id == prev_ver.id)
                            .first()
                        )
                        if prev_content_rec and item["latest_content"]:
                            old_lines = prev_content_rec.content.splitlines(keepends=True)
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
        except DocExchangeError as exc:
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

            if stage not in VALID_CONFIG_STAGES:
                raise DocExchangeError(
                    error_code="INVALID_STAGE",
                    message=f"stage '{stage}' is not valid. Must be one of: {sorted(VALID_CONFIG_STAGES)}.",
                    details={"valid_stages": sorted(VALID_CONFIG_STAGES)},
                )

            doc_id = f"{project_id}/config/{stage}"
            result = self._c.document_service.get(
                doc_id=doc_id,
                project_space_id=subproject.project_space_id,
            )
            return result.model_dump()
        except DocExchangeError as exc:
            return self._error_dict(exc)
