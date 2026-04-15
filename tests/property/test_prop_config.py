# Feature: doc-exchange-center, Property 17: get_config 按 stage 隔离
"""
Property-based tests for get_config stage isolation.

**Validates: Requirements 6.2**
"""

import asyncio
import tempfile
import uuid
from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.mcp.dependencies import ServiceContainer
from doc_exchange.mcp.tools import ToolHandler
from doc_exchange.models import Base, ProjectSpace
from doc_exchange.services.document_service import VALID_CONFIG_STAGES
from doc_exchange.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _make_session_space_and_handler(engine, docs_root: str):
    """Create session, a ProjectSpace, a registered SubProject, and a ToolHandler."""
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="prop-config-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()

    container = ServiceContainer(db_session=session, docs_root=docs_root)
    handler = ToolHandler(container)

    # Register a subproject so get_config can validate project_id
    project_svc = ProjectService(db=session)
    subproject = project_svc.register(
        name="config-test-sub",
        type="development",
        project_space_id=space.id,
    )

    return session, space.id, handler, subproject.id


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Three distinct non-empty content strings for dev/test/prod
three_distinct_contents = st.lists(
    st.text(
        alphabet=st.characters(blacklist_characters="\r"),
        min_size=1,
        max_size=200,
    ),
    min_size=3,
    max_size=3,
    unique=True,
)


# ---------------------------------------------------------------------------
# Property 17: get_config 按 stage 隔离
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(contents=three_distinct_contents)
def test_prop_get_config_stage_isolation(contents):
    """
    Property 17: get_config 按 stage 隔离

    For any subproject, if config documents are pushed for dev, test, and prod
    stages with different content, then get_config(project_id, stage) must
    return the content for the corresponding stage. Different stages must not
    contaminate each other.

    **Validates: Requirements 6.2**
    """
    dev_content, test_content, prod_content = contents
    stages = ["dev", "test", "prod"]
    stage_contents = {"dev": dev_content, "test": test_content, "prod": prod_content}

    engine = _make_engine()
    session = None
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id, handler, project_id = _make_session_space_and_handler(
                engine, docs_root
            )

            # Push config for all three stages
            for stage in stages:
                result = _run(
                    handler.push_document(
                        project_id=project_id,
                        doc_id=f"{project_id}/config/{stage}",
                        content=stage_contents[stage],
                    )
                )
                assert "error" not in result, (
                    f"push_document failed for stage={stage}: {result}"
                )

            # Verify each stage returns the correct content (no cross-stage contamination)
            for stage in stages:
                result = _run(handler.get_config(project_id=project_id, stage=stage))
                assert "error" not in result, (
                    f"get_config failed for stage={stage}: {result}"
                )
                assert result["content"] == stage_contents[stage], (
                    f"Stage {stage!r} content mismatch: "
                    f"expected {stage_contents[stage]!r}, got {result['content']!r}"
                )

            # Verify stages are truly isolated: each stage's content differs from others
            for i, stage_a in enumerate(stages):
                for stage_b in stages[i + 1:]:
                    result_a = _run(handler.get_config(project_id=project_id, stage=stage_a))
                    result_b = _run(handler.get_config(project_id=project_id, stage=stage_b))
                    assert result_a["content"] != result_b["content"], (
                        f"Stages {stage_a!r} and {stage_b!r} should have different content "
                        f"but both returned {result_a['content']!r}"
                    )

        finally:
            if session is not None:
                session.close()
            engine.dispose()


@settings(max_examples=100)
@given(
    content=st.text(
        alphabet=st.characters(blacklist_characters="\r"),
        min_size=1,
        max_size=200,
    ),
    queried_stage=st.sampled_from(sorted(VALID_CONFIG_STAGES)),
)
def test_prop_get_config_missing_stage_returns_doc_not_found(content, queried_stage):
    """
    Property 17 (no-config case): Querying a stage that has no config pushed
    must return DOC_NOT_FOUND.

    **Validates: Requirements 6.2**
    """
    # Push only to one stage, query a different stage
    other_stages = sorted(VALID_CONFIG_STAGES - {queried_stage})
    push_stage = other_stages[0]  # push to a different stage

    engine = _make_engine()
    session = None
    with tempfile.TemporaryDirectory() as docs_root:
        try:
            session, space_id, handler, project_id = _make_session_space_and_handler(
                engine, docs_root
            )

            # Push config only for push_stage
            push_result = _run(
                handler.push_document(
                    project_id=project_id,
                    doc_id=f"{project_id}/config/{push_stage}",
                    content=content,
                )
            )
            assert "error" not in push_result, (
                f"push_document failed for stage={push_stage}: {push_result}"
            )

            # Querying queried_stage (which has no config) must return DOC_NOT_FOUND
            result = _run(handler.get_config(project_id=project_id, stage=queried_stage))
            assert "error" in result, (
                f"Expected error for missing stage {queried_stage!r}, got {result}"
            )
            assert result["error"] == "DOC_NOT_FOUND", (
                f"Expected DOC_NOT_FOUND for missing stage {queried_stage!r}, got {result['error']!r}"
            )

        finally:
            if session is not None:
                session.close()
            engine.dispose()
