"""Tests for SDAOP version mechanism (v4 §15.3)."""

import json
import os
import tempfile

import pytest

from agent_nexus.mcp import sdaop


def test_compute_sdaop_version_is_deterministic():
    """Same templates → same version. Two consecutive calls must agree."""
    v1 = sdaop.compute_sdaop_version("kiro")
    v2 = sdaop.compute_sdaop_version("kiro")
    assert v1 == v2


def test_compute_sdaop_version_is_short_hex():
    """Version should be a short hex prefix suitable for inline use."""
    v = sdaop.compute_sdaop_version("kiro")
    assert isinstance(v, str)
    assert len(v) == 12
    assert all(c in "0123456789abcdef" for c in v)


def test_compute_sdaop_version_differs_across_client_types():
    """kiro and cursor have different steering templates → different versions."""
    v_kiro = sdaop.compute_sdaop_version("kiro")
    v_cursor = sdaop.compute_sdaop_version("cursor")
    assert v_kiro != v_cursor


def test_compute_sdaop_version_unknown_client_falls_back_to_default():
    """Unknown client_type uses the `default` config, doesn't raise."""
    v = sdaop.compute_sdaop_version("totally-unknown-client")
    assert isinstance(v, str)
    assert len(v) == 12


def test_known_client_types_includes_standard_clients():
    types = sdaop.known_client_types()
    for expected in ("kiro", "claude", "codex", "cursor", "default"):
        assert expected in types


def test_compute_sdaop_version_changes_when_template_changes(tmp_path, monkeypatch):
    """Editing any template file must change the version."""
    # Establish baseline
    baseline = sdaop.compute_sdaop_version("kiro")

    # Patch the read helper to simulate template content change for kiro
    real_read = sdaop._read
    def fake_read(path):
        content = real_read(path)
        if path.endswith("kiro.md"):
            content = content + "\n# extra line added\n"
        return content

    monkeypatch.setattr(sdaop, "_read", fake_read)
    changed = sdaop.compute_sdaop_version("kiro")
    assert changed != baseline


@pytest.mark.asyncio
async def test_generate_instruction_file_includes_version_in_response():
    """ToolHandler.generate_steering_file must include sdaop_version, push_tool_refresh, and nexus_state_update."""
    from agent_nexus.mcp.dependencies import ServiceContainer
    from agent_nexus.mcp.tools import ToolHandler
    from sqlalchemy import create_engine, event as sa_event
    from sqlalchemy.orm import sessionmaker
    from agent_nexus.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with tempfile.TemporaryDirectory() as tmp_root:
        container = ServiceContainer(db_session=session, docs_root=tmp_root)
        handler = ToolHandler(container)
        result = await handler.generate_steering_file(
            project_name="test-project",
            project_space_id="00000000-0000-0000-0000-000000000000",
            client_type="kiro",
        )

    assert "sdaop_version" in result
    assert result["sdaop_version"] == sdaop.compute_sdaop_version("kiro")
    assert "push_tool_refresh" in result
    assert result["push_tool_refresh"]["target_file"] == ".kiro/nexus_push.py"
    assert "url" in result["push_tool_refresh"]
    assert "nexus_state_update" in result
    assert result["nexus_state_update"]["entry"]["_sdaop_version"] == result["sdaop_version"]


@pytest.mark.asyncio
async def test_generate_instruction_file_prepends_frontmatter_for_kiro():
    """Kiro's template has no existing frontmatter → SDAOP fields prepended as a fresh block."""
    from agent_nexus.mcp.dependencies import ServiceContainer
    from agent_nexus.mcp.tools import ToolHandler
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from agent_nexus.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with tempfile.TemporaryDirectory() as tmp_root:
        container = ServiceContainer(db_session=session, docs_root=tmp_root)
        handler = ToolHandler(container)
        result = await handler.generate_steering_file(
            project_name="test-project",
            project_space_id="00000000-0000-0000-0000-000000000000",
            client_type="kiro",
        )

    content = result["file_content"]
    assert content.startswith("---\n")
    # The frontmatter must contain our SDAOP fields
    end = content.find("\n---\n", 4)
    assert end != -1
    frontmatter = content[4:end + 1]
    assert "sdaop_version:" in frontmatter
    assert "generated_at:" in frontmatter
    assert "client_type: kiro" in frontmatter


@pytest.mark.asyncio
async def test_generate_instruction_file_merges_into_cursor_frontmatter():
    """Cursor's template already starts with `---\\nalwaysApply: true\\n---`. SDAOP fields must merge into that block, not create a second one."""
    from agent_nexus.mcp.dependencies import ServiceContainer
    from agent_nexus.mcp.tools import ToolHandler
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from agent_nexus.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with tempfile.TemporaryDirectory() as tmp_root:
        container = ServiceContainer(db_session=session, docs_root=tmp_root)
        handler = ToolHandler(container)
        result = await handler.generate_steering_file(
            project_name="test-project",
            project_space_id="00000000-0000-0000-0000-000000000000",
            client_type="cursor",
        )

    content = result["file_content"]
    assert content.startswith("---\n")
    # Exactly one frontmatter block: the second `---\n` should be followed by content, not another `---`
    end = content.find("\n---\n", 4)
    assert end != -1
    frontmatter = content[4:end + 1]
    # Both cursor's original key and SDAOP fields must be present in the same block
    assert "alwaysApply:" in frontmatter
    assert "sdaop_version:" in frontmatter
    # Make sure we didn't leave a stray duplicate `---` block right after
    body = content[end + 5:]
    assert not body.lstrip().startswith("---")


def test_push_tool_template_read_base_version_skips_non_dict_entries(tmp_path):
    """The pushed nexus-state.json includes the reserved `_sdaop_version` string key.
    read_base_version must not crash on it."""
    # Inline-execute the helper from the template by importing it via runpy
    import runpy
    spec_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "spec", "push-tool.py",
    )

    # Replace PROJECT_ID and STATE_FILE so the module loads cleanly
    state_file = tmp_path / "nexus-state.json"
    with open(state_file, "w") as f:
        json.dump({
            "_sdaop_version": "abc123def456",
            "proj/doc": {"local_version": 7, "local_file_hint": "doc"},
        }, f)

    namespace = runpy.run_path(spec_path)
    read_base_version = namespace["read_base_version"]
    # Monkey-patch STATE_FILE in the loaded namespace by re-binding the module global
    # via the function's __globals__
    read_base_version.__globals__["STATE_FILE"] = str(state_file)

    # Non-dict reserved key → returns None, no crash
    assert read_base_version("_sdaop_version") is None
    # Normal doc → returns local_version
    assert read_base_version("proj/doc") == 7
    # Missing doc → returns None
    assert read_base_version("proj/missing") is None


# ---------------------------------------------------------------------------
# Public URL handling (single source of truth for outward-facing URL)
# ---------------------------------------------------------------------------


def test_get_public_url_uses_env_var(monkeypatch):
    monkeypatch.setenv("AGENT_NEXUS_PUBLIC_URL", "http://47.100.240.111:10086")
    assert sdaop.get_public_url() == "http://47.100.240.111:10086"


def test_get_public_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("AGENT_NEXUS_PUBLIC_URL", "https://nexus.example.com/")
    assert sdaop.get_public_url() == "https://nexus.example.com"


def test_get_public_url_falls_back_to_localhost(monkeypatch):
    monkeypatch.delenv("AGENT_NEXUS_PUBLIC_URL", raising=False)
    monkeypatch.delenv("AGENT_NEXUS_PORT", raising=False)
    assert sdaop.get_public_url() == "http://localhost:10086"


def test_get_public_url_respects_port_env(monkeypatch):
    monkeypatch.delenv("AGENT_NEXUS_PUBLIC_URL", raising=False)
    monkeypatch.setenv("AGENT_NEXUS_PORT", "20086")
    assert sdaop.get_public_url() == "http://localhost:20086"


def test_sdaop_version_changes_when_public_url_changes(monkeypatch):
    """Switching the server's outward URL must bump the version hash so
    existing clients re-onboard with a fresh URL."""
    monkeypatch.setenv("AGENT_NEXUS_PUBLIC_URL", "http://localhost:10086")
    v_local = sdaop.compute_sdaop_version("kiro")

    monkeypatch.setenv("AGENT_NEXUS_PUBLIC_URL", "http://47.100.240.111:10086")
    v_cloud = sdaop.compute_sdaop_version("kiro")

    assert v_local != v_cloud


@pytest.mark.asyncio
async def test_generate_instruction_file_renders_server_url(monkeypatch):
    """Steering file must use AGENT_NEXUS_PUBLIC_URL for any {{SERVER_URL}} occurrences."""
    from agent_nexus.mcp.dependencies import ServiceContainer
    from agent_nexus.mcp.tools import ToolHandler
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from agent_nexus.models import Base

    monkeypatch.setenv("AGENT_NEXUS_PUBLIC_URL", "http://47.100.240.111:10086")

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with tempfile.TemporaryDirectory() as tmp_root:
        container = ServiceContainer(db_session=session, docs_root=tmp_root)
        handler = ToolHandler(container)
        result = await handler.generate_steering_file(
            project_name="test-project",
            project_space_id="00000000-0000-0000-0000-000000000000",
            client_type="kiro",
        )

    content = result["file_content"]
    # No unrendered placeholder
    assert "{{SERVER_URL}}" not in content
    # Public URL appears in steering content (MCP endpoint line, curl examples)
    assert "http://47.100.240.111:10086" in content
    # push_tool_refresh.url uses public URL
    assert result["push_tool_refresh"]["url"] == "http://47.100.240.111:10086/api/templates/push-tool.py"
