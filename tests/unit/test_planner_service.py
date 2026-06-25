"""
Unit tests for PlannerService.

Covers:
  - 读能力：list_spaces / list_projects / read_document（无 project_id 限制）
  - search 复用 FTS 正常返回
  - 写能力：push_document 的 actor 标识正确、默认 as_draft 走 draft 状态
  - 跨边界写：写其他服务名下文档时为 draft，未自动 publish
  - AI 能力：mock LLMClient 验证 chat/plan 的 prompt 组装
  - llm_client=None 时返回 LLM_NOT_CONFIGURED
  - prompt injection：验证 system prompt 含 DATA-not-instructions 声明
  - plan：返回 proposal dict 且不落库（验证调用后 DB 无新增 SubProject）
  - MCP 工具：planner_chat / planner_plan / planner_overview 正确委托
  - 回归：现有全部测试仍通过（系统在无 Planner 介入时行为不变）

Requirements: 1.2, 1.3, 2.1, 2.2, 2.3, 3.3, 5.1, 7.1, 7.2, 7.3
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from agent_nexus.mcp.dependencies import ServiceContainer
from agent_nexus.mcp.tools import ToolHandler
from agent_nexus.models import Base
from agent_nexus.models.entities import (
    Document,
    DocumentVersion,
    SubProject,
    ProjectSpace,
)
from agent_nexus.planner.planner_service import PlannerService, SYSTEM_ACTOR, _DRAFT_ACTOR
from agent_nexus.search.fts import ensure_fts_table, upsert_doc


# ---------------------------------------------------------------------------
# Fixtures — engine and session with FTS
# ---------------------------------------------------------------------------


@pytest.fixture()
def fts_engine():
    """In-memory SQLite engine with ORM tables AND FTS5 table (mirrors test_fts_search.py)."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    ensure_fts_table(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def fts_session(fts_engine):
    """Session that rolls back after each test."""
    connection = fts_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_space(session, name: str = "test-space") -> ProjectSpace:
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name=name,
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()
    return space


def _make_subproject(session, space_id: str, name: str = "svc-a") -> SubProject:
    sp = SubProject(
        id=str(uuid.uuid4()),
        project_space_id=space_id,
        name=name,
        type="development",
        stage="design",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(sp)
    session.flush()
    return sp


def _make_container(session, tmp_path) -> ServiceContainer:
    return ServiceContainer(db_session=session, docs_root=str(tmp_path))


def _make_planner(session, tmp_path, llm_client=None) -> tuple[PlannerService, ServiceContainer]:
    container = _make_container(session, tmp_path)
    planner = PlannerService(container=container, llm_client=llm_client, require_review=True)
    return planner, container


# ---------------------------------------------------------------------------
# 读能力：list_spaces
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------


def test_list_spaces_returns_all_spaces(fts_session, tmp_path):
    """Planner 可列出所有 space，不受 project_id 限制。"""
    space_a = _make_space(fts_session, "space-a")
    space_b = _make_space(fts_session, "space-b")

    planner, _ = _make_planner(fts_session, tmp_path)
    result = planner.list_spaces()

    ids = [r["space_id"] for r in result]
    assert space_a.id in ids
    assert space_b.id in ids


def test_list_spaces_no_project_id_restriction(fts_session, tmp_path):
    """list_spaces 不要求传 project_id，系统级视角可直接调用。"""
    _make_space(fts_session, "global-space")
    planner, _ = _make_planner(fts_session, tmp_path)

    # No project_id argument — should work without any identity check
    result = planner.list_spaces()
    assert isinstance(result, list)
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# 读能力：list_projects
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------


def test_list_projects_returns_subprojects(fts_session, tmp_path):
    """Planner 可列出 space 下所有 SubProject，无需 project_id。"""
    space = _make_space(fts_session)
    sp1 = _make_subproject(fts_session, space.id, "backend")
    sp2 = _make_subproject(fts_session, space.id, "frontend")

    planner, _ = _make_planner(fts_session, tmp_path)
    result = planner.list_projects(space.id)

    ids = [r["project_id"] for r in result]
    assert sp1.id in ids
    assert sp2.id in ids


def test_list_projects_cross_space_isolation(fts_session, tmp_path):
    """list_projects 按 space_id 隔离，只返回该 space 下的项目。"""
    space_a = _make_space(fts_session, "space-a")
    space_b = _make_space(fts_session, "space-b")
    sp_a = _make_subproject(fts_session, space_a.id, "svc-in-a")
    _make_subproject(fts_session, space_b.id, "svc-in-b")

    planner, _ = _make_planner(fts_session, tmp_path)
    result = planner.list_projects(space_a.id)

    ids = [r["project_id"] for r in result]
    assert sp_a.id in ids
    assert len(result) == 1


# ---------------------------------------------------------------------------
# 读能力：read_document
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------


def test_read_document_returns_content(fts_session, tmp_path):
    """Planner 可读取任意文档，无 project_id 限制。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)
    container = _make_container(fts_session, tmp_path)

    # First push a document via DocumentService
    from agent_nexus.services.schemas import PushRequest
    req = PushRequest(
        doc_id=f"{sp.id}/design",
        content="# Design doc content",
        actor=sp.id,
        project_space_id=space.id,
    )
    container.document_service.push(req)

    planner = PlannerService(container=container, llm_client=None)
    result = planner.read_document(space.id, f"{sp.id}/design")

    assert result["doc_id"] == f"{sp.id}/design"
    assert result["content"] == "# Design doc content"


# ---------------------------------------------------------------------------
# search 复用 FTS 正常返回
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------


def test_search_delegates_to_fts(fts_session, tmp_path):
    """Planner.search 复用 FTS5，跨 space 全文检索正常返回。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)

    # Insert document into FTS index directly
    upsert_doc(
        db=fts_session,
        doc_id=f"{sp.id}/requirement",
        project_space_id=space.id,
        subproject_id=sp.id,
        doc_type="requirement",
        content="The system must support distributed caching with Redis.",
    )

    planner, _ = _make_planner(fts_session, tmp_path)
    results = planner.search(space.id, "Redis")

    assert len(results) >= 1
    assert any(r["doc_id"] == f"{sp.id}/requirement" for r in results)


def test_search_returns_empty_for_no_match(fts_session, tmp_path):
    """search 无匹配时返回空列表。"""
    space = _make_space(fts_session)

    planner, _ = _make_planner(fts_session, tmp_path)
    results = planner.search(space.id, "nonexistent_term_xyz_12345")

    assert results == []


# ---------------------------------------------------------------------------
# 写能力：push_document — actor 标识与 draft 状态
# Validates: Requirements 1.3, 7.3
# ---------------------------------------------------------------------------


def test_push_document_as_draft_sets_agent_planner(fts_session, tmp_path):
    """as_draft=True 时 actor 应为 'agent:planner'，status 应为 'draft'。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)

    planner, _ = _make_planner(fts_session, tmp_path)
    result = planner.push_document(
        space_id=space.id,
        doc_id=f"{sp.id}/design",
        content="# Draft design",
        as_draft=True,
    )

    assert result["status"] == "draft"

    # Verify actor in DB
    ver = (
        fts_session.query(DocumentVersion)
        .filter(DocumentVersion.document_id == f"{sp.id}/design")
        .first()
    )
    assert ver is not None
    assert ver.actor == _DRAFT_ACTOR  # "agent:planner"
    assert ver.status == "draft"


def test_push_document_not_draft_sets_system_published(fts_session, tmp_path):
    """as_draft=False 时 actor 应为 'system'，status 应为 'published'。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)

    planner, _ = _make_planner(fts_session, tmp_path)
    result = planner.push_document(
        space_id=space.id,
        doc_id=f"{sp.id}/requirement",
        content="# Published content",
        as_draft=False,
    )

    assert result["status"] == "published"

    ver = (
        fts_session.query(DocumentVersion)
        .filter(DocumentVersion.document_id == f"{sp.id}/requirement")
        .first()
    )
    assert ver is not None
    assert ver.actor == SYSTEM_ACTOR  # "system"
    assert ver.status == "published"


def test_push_document_default_is_draft(fts_session, tmp_path):
    """push_document 默认 as_draft=True，不指定时应走 draft 路径。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)

    planner, _ = _make_planner(fts_session, tmp_path)
    result = planner.push_document(
        space_id=space.id,
        doc_id=f"{sp.id}/api",
        content="# API spec",
    )

    assert result["status"] == "draft"


# ---------------------------------------------------------------------------
# 跨边界写：写其他服务名下文档时为 draft，未自动 publish
# Validates: Requirements 7.3
# ---------------------------------------------------------------------------


def test_cross_boundary_write_is_draft_not_published(fts_session, tmp_path):
    """Planner 写另一个服务名下的文档时，版本状态必须为 draft，不得自动 publish。"""
    space = _make_space(fts_session)
    other_sp = _make_subproject(fts_session, space.id, "other-service")

    planner, _ = _make_planner(fts_session, tmp_path)
    # Planner pushes into other-service's document namespace (cross-boundary write)
    result = planner.push_document(
        space_id=space.id,
        doc_id=f"{other_sp.id}/design",
        content="Planner cross-boundary proposal",
        as_draft=True,
    )

    assert result["status"] == "draft", "Cross-boundary write must produce draft, not published"

    ver = (
        fts_session.query(DocumentVersion)
        .filter(DocumentVersion.document_id == f"{other_sp.id}/design")
        .first()
    )
    assert ver is not None
    assert ver.status == "draft"
    assert ver.published_at is None, "Draft version must not have published_at set"


def test_cross_boundary_draft_not_in_fts(fts_session, tmp_path):
    """跨边界写入的 draft 文档不应出现在 FTS 索引中（不可检索）。"""
    space = _make_space(fts_session)
    other_sp = _make_subproject(fts_session, space.id, "other-service")

    planner, _ = _make_planner(fts_session, tmp_path)
    planner.push_document(
        space_id=space.id,
        doc_id=f"{other_sp.id}/design",
        content="xboundaryproposal unique token for draft",
        as_draft=True,
    )

    # Use a single unique keyword to avoid FTS5 multi-word implicit phrase issues
    results = planner.search(space.id, "xboundaryproposal")
    assert results == [], "Draft document must not be searchable via FTS"


# ---------------------------------------------------------------------------
# AI 能力：llm_client=None 时返回 LLM_NOT_CONFIGURED
# Validates: Requirements 3.3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_returns_llm_not_configured_when_no_client(fts_session, tmp_path):
    """无 LLM client 时 chat 返回 LLM_NOT_CONFIGURED 错误。"""
    space = _make_space(fts_session)
    planner, _ = _make_planner(fts_session, tmp_path, llm_client=None)

    result = await planner.chat(space_id=space.id, question="What is the architecture?")

    assert isinstance(result, dict)
    assert result["error"] == "LLM_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_plan_returns_llm_not_configured_when_no_client(fts_session, tmp_path):
    """无 LLM client 时 plan 返回 LLM_NOT_CONFIGURED 错误。"""
    space = _make_space(fts_session)
    planner, _ = _make_planner(fts_session, tmp_path, llm_client=None)

    result = await planner.plan(space_id=space.id, description="Build an e-commerce platform")

    assert isinstance(result, dict)
    assert result["error"] == "LLM_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# AI 能力：mock LLMClient，验证 chat 的 prompt 组装
# Validates: Requirements 2.1, 5.1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_calls_llm_with_assembled_prompt(fts_session, tmp_path):
    """chat 正确组装 system prompt 并调用 LLM.complete。"""
    space = _make_space(fts_session)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="Here is my answer")

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    result = await planner.chat(space_id=space.id, question="How does caching work?")

    assert result == "Here is my answer"
    mock_llm.complete.assert_called_once()
    call_kwargs = mock_llm.complete.call_args
    # complete is called with (system_prompt, user_prompt)
    system_prompt = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("system_prompt", "")
    user_prompt = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("user_prompt", "")
    assert "How does caching work?" in user_prompt or "How does caching work?" in system_prompt


@pytest.mark.asyncio
async def test_chat_with_specific_doc_ids(fts_session, tmp_path):
    """chat 指定 doc_ids 时只加载指定文档的上下文。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)

    container = _make_container(fts_session, tmp_path)
    from agent_nexus.services.schemas import PushRequest
    req = PushRequest(
        doc_id=f"{sp.id}/design",
        content="# Design content for caching",
        actor=sp.id,
        project_space_id=space.id,
    )
    container.document_service.push(req)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="Answer with context")

    planner = PlannerService(container=container, llm_client=mock_llm)
    result = await planner.chat(
        space_id=space.id,
        question="Tell me about caching",
        doc_ids=[f"{sp.id}/design"],
    )

    assert result == "Answer with context"
    mock_llm.complete.assert_called_once()
    # Verify the design doc content was in the system prompt
    system_prompt = mock_llm.complete.call_args[0][0]
    assert "caching" in system_prompt


# ---------------------------------------------------------------------------
# AI 能力：mock LLMClient，验证 plan 的 prompt 组装
# Validates: Requirements 2.2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_calls_llm_and_returns_proposals(fts_session, tmp_path):
    """plan 正确调用 LLM 并解析返回的 JSON proposal。"""
    space = _make_space(fts_session)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='[{"name":"auth-service","type":"development","suggested_docs":["requirements","design"]}]'
    )

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    result = await planner.plan(
        space_id=space.id,
        description="Build an authentication service",
    )

    assert "proposals" in result
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["name"] == "auth-service"
    assert result["description"] == "Build an authentication service"


@pytest.mark.asyncio
async def test_plan_prompt_contains_description(fts_session, tmp_path):
    """plan 组装的 user_prompt 包含用户传入的 description。"""
    space = _make_space(fts_session)

    captured_prompts = {}

    async def capture_complete(system_prompt, user_prompt, **kwargs):
        captured_prompts["system"] = system_prompt
        captured_prompts["user"] = user_prompt
        return '[{"name":"svc","type":"development","suggested_docs":["requirements"]}]'

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    await planner.plan(
        space_id=space.id,
        description="A microservices platform for payments",
    )

    # Description should appear in either prompt
    full_prompt = captured_prompts.get("system", "") + captured_prompts.get("user", "")
    assert "payments" in full_prompt


# ---------------------------------------------------------------------------
# prompt injection：验证 system prompt 含 DATA-not-instructions 声明
# Validates: Requirements 5.1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_system_prompt_has_injection_protection(fts_session, tmp_path):
    """chat 的 system_prompt 必须声明文档内容是 DATA，不是指令。"""
    space = _make_space(fts_session)

    captured = {}

    async def capture_complete(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return "answer"

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    await planner.chat(space_id=space.id, question="Any question")

    system_prompt = captured["system_prompt"]
    # Must contain DATA-not-instructions declaration
    assert "DATA" in system_prompt, "system_prompt must declare document content is DATA"
    assert "never as instructions" in system_prompt or "not as instructions" in system_prompt, (
        "system_prompt must state documents are data, never instructions"
    )


@pytest.mark.asyncio
async def test_plan_system_prompt_has_injection_protection(fts_session, tmp_path):
    """plan 的 system_prompt 同样必须含有 injection 防护声明。"""
    space = _make_space(fts_session)

    captured = {}

    async def capture_complete(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return '[{"name":"svc","type":"development","suggested_docs":[]}]'

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    await planner.plan(space_id=space.id, description="Build something")

    system_prompt = captured["system_prompt"]
    assert "DATA" in system_prompt
    assert "never as instructions" in system_prompt or "not as instructions" in system_prompt


@pytest.mark.asyncio
async def test_chat_prompt_separates_context_and_user_sections(fts_session, tmp_path):
    """chat prompt 必须将文档内容（CONTEXT）与用户问题（USER）分区。"""
    space = _make_space(fts_session)

    captured = {}

    async def capture_complete(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return "answer"

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(side_effect=capture_complete)

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    await planner.chat(space_id=space.id, question="What is the design?")

    system_prompt = captured["system_prompt"]
    # Both CONTEXT and USER sections must be present
    assert "CONTEXT" in system_prompt
    assert "USER" in system_prompt


# ---------------------------------------------------------------------------
# plan：返回 proposal dict 且不落库
# Validates: Requirements 2.2, 2.3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_does_not_persist_subprojects(fts_session, tmp_path):
    """plan 只返回提议，不向 DB 写入新 SubProject。"""
    space = _make_space(fts_session)

    # Count SubProjects before plan()
    count_before = fts_session.query(SubProject).filter(
        SubProject.project_space_id == space.id
    ).count()

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='[{"name":"new-svc","type":"development","suggested_docs":["requirements"]}]'
    )

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    result = await planner.plan(
        space_id=space.id,
        description="Build a new service",
    )

    # Count SubProjects after plan() — must not have changed
    count_after = fts_session.query(SubProject).filter(
        SubProject.project_space_id == space.id
    ).count()

    assert count_before == count_after, (
        f"plan() must not persist SubProjects to DB. "
        f"count before={count_before}, after={count_after}"
    )
    # Result is still a proposal dict
    assert "proposals" in result
    assert result["proposals"][0]["name"] == "new-svc"


@pytest.mark.asyncio
async def test_plan_returns_proposal_dict_shape(fts_session, tmp_path):
    """plan 返回的提议包含 proposals 和 description 字段。"""
    space = _make_space(fts_session)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='[{"name":"svc","type":"development","suggested_docs":["requirements"]}]'
    )

    planner, _ = _make_planner(fts_session, tmp_path, llm_client=mock_llm)
    result = await planner.plan(space_id=space.id, description="My description")

    assert isinstance(result, dict)
    assert "proposals" in result
    assert "description" in result
    assert result["description"] == "My description"
    assert isinstance(result["proposals"], list)


# ---------------------------------------------------------------------------
# MCP 工具：planner_chat / planner_plan / planner_overview 正确委托
# Validates: Requirements 2.1, 2.2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_planner_chat_delegates_to_planner_service(fts_session, tmp_path):
    """MCP planner_chat 正确委托给 PlannerService.chat()。"""
    space = _make_space(fts_session)
    container = _make_container(fts_session, tmp_path)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="mcp chat answer")
    container.planner_service = PlannerService(container=container, llm_client=mock_llm)

    handler = ToolHandler(container)
    result = await handler.planner_chat(space_id=space.id, question="Cross-service question?")

    assert "error" not in result
    assert result["answer"] == "mcp chat answer"


@pytest.mark.asyncio
async def test_mcp_planner_chat_wraps_str_answer_in_dict(fts_session, tmp_path):
    """planner_chat 把字符串答案规范化为 {"answer": str}。"""
    space = _make_space(fts_session)
    container = _make_container(fts_session, tmp_path)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value="plain string answer")
    container.planner_service = PlannerService(container=container, llm_client=mock_llm)

    handler = ToolHandler(container)
    result = await handler.planner_chat(space_id=space.id, question="A question")

    assert isinstance(result, dict)
    assert "answer" in result
    assert result["answer"] == "plain string answer"


@pytest.mark.asyncio
async def test_mcp_planner_chat_no_llm_returns_error(fts_session, tmp_path):
    """planner_chat 无 LLM 时返回 LLM_NOT_CONFIGURED。"""
    space = _make_space(fts_session)
    container = _make_container(fts_session, tmp_path)
    container.planner_service = PlannerService(container=container, llm_client=None)

    handler = ToolHandler(container)
    result = await handler.planner_chat(space_id=space.id, question="Any question")

    assert result["error"] == "LLM_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_mcp_planner_plan_delegates_and_returns_proposal(fts_session, tmp_path):
    """MCP planner_plan 正确委托给 PlannerService.plan()，返回 proposal。"""
    space = _make_space(fts_session)
    container = _make_container(fts_session, tmp_path)

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='[{"name":"auth","type":"development","suggested_docs":["requirements"]}]'
    )
    container.planner_service = PlannerService(container=container, llm_client=mock_llm)

    handler = ToolHandler(container)
    result = await handler.planner_plan(
        space_id=space.id,
        description="Build an auth system",
    )

    assert "proposals" in result
    assert result["proposals"][0]["name"] == "auth"


@pytest.mark.asyncio
async def test_mcp_planner_plan_does_not_create_subprojects(fts_session, tmp_path):
    """MCP planner_plan 调用后 DB 中无新增 SubProject。"""
    space = _make_space(fts_session)
    container = _make_container(fts_session, tmp_path)

    count_before = fts_session.query(SubProject).filter(
        SubProject.project_space_id == space.id
    ).count()

    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(
        return_value='[{"name":"svc","type":"development","suggested_docs":[]}]'
    )
    container.planner_service = PlannerService(container=container, llm_client=mock_llm)

    handler = ToolHandler(container)
    await handler.planner_plan(space_id=space.id, description="Any description")

    count_after = fts_session.query(SubProject).filter(
        SubProject.project_space_id == space.id
    ).count()
    assert count_before == count_after


@pytest.mark.asyncio
async def test_mcp_planner_overview_returns_cross_project_view(fts_session, tmp_path):
    """planner_overview 返回 space 下所有子项目的文档总览。"""
    space = _make_space(fts_session)
    sp1 = _make_subproject(fts_session, space.id, "svc-alpha")
    sp2 = _make_subproject(fts_session, space.id, "svc-beta")

    container = _make_container(fts_session, tmp_path)
    # Push a document for sp1
    from agent_nexus.services.schemas import PushRequest
    container.document_service.push(PushRequest(
        doc_id=f"{sp1.id}/requirement",
        content="Requirements",
        actor=sp1.id,
        project_space_id=space.id,
    ))

    handler = ToolHandler(container)
    result = await handler.planner_overview(space_id=space.id)

    assert result["space_id"] == space.id
    project_ids = [p["project_id"] for p in result["projects"]]
    assert sp1.id in project_ids
    assert sp2.id in project_ids

    # sp1 should have one document listed
    sp1_entry = next(p for p in result["projects"] if p["project_id"] == sp1.id)
    assert len(sp1_entry["documents"]) == 1
    assert sp1_entry["documents"][0]["doc_id"] == f"{sp1.id}/requirement"


@pytest.mark.asyncio
async def test_mcp_planner_overview_empty_space(fts_session, tmp_path):
    """planner_overview 对空 space 返回 projects 为空列表。"""
    space = _make_space(fts_session)
    container = _make_container(fts_session, tmp_path)

    handler = ToolHandler(container)
    result = await handler.planner_overview(space_id=space.id)

    assert result["space_id"] == space.id
    assert result["projects"] == []


# ---------------------------------------------------------------------------
# 回归：Planner 不影响现有核心功能
# Validates: Requirements 7.1, 7.2
# ---------------------------------------------------------------------------


def test_service_container_creates_planner_service(fts_session, tmp_path):
    """ServiceContainer 初始化时已注入 planner_service（可能 llm_client=None）。"""
    container = _make_container(fts_session, tmp_path)
    assert hasattr(container, "planner_service")
    assert container.planner_service is not None
    assert isinstance(container.planner_service, PlannerService)


def test_document_service_push_still_works_without_planner(fts_session, tmp_path):
    """DocumentService.push 在无 Planner 介入时行为不变。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id)

    container = _make_container(fts_session, tmp_path)
    from agent_nexus.services.schemas import PushRequest
    req = PushRequest(
        doc_id=f"{sp.id}/requirement",
        content="# Requirement content",
        actor=sp.id,
        project_space_id=space.id,
    )
    result = container.document_service.push(req)

    assert result.status == "published"
    assert result.version == 1
    assert result.doc_id == f"{sp.id}/requirement"


def test_project_service_list_subprojects_works_without_planner(fts_session, tmp_path):
    """ProjectService.list_subprojects 在无 Planner 介入时行为不变。"""
    space = _make_space(fts_session)
    sp = _make_subproject(fts_session, space.id, "regression-svc")

    container = _make_container(fts_session, tmp_path)
    result = container.project_service.list_subprojects(space.id)

    assert any(s.id == sp.id for s in result)


def test_planner_not_registered_as_subproject(fts_session, tmp_path):
    """Planner 不在 SubProject 表中注册自身（不占用 SubProject 资源）。"""
    space = _make_space(fts_session)
    planner, container = _make_planner(fts_session, tmp_path)

    # After creating planner, verify no planner-owned subproject was inserted
    all_subprojects = fts_session.query(SubProject).filter(
        SubProject.project_space_id == space.id
    ).all()
    # There should be no subproject (none were added in this test)
    planner_owned = [sp for sp in all_subprojects if "planner" in sp.name.lower()]
    assert len(planner_owned) == 0


# ---------------------------------------------------------------------------
# Constants sanity check
# ---------------------------------------------------------------------------


def test_system_actor_and_draft_actor_constants():
    """SYSTEM_ACTOR == 'system', _DRAFT_ACTOR == 'agent:planner'。"""
    assert SYSTEM_ACTOR == "system"
    assert _DRAFT_ACTOR == "agent:planner"
