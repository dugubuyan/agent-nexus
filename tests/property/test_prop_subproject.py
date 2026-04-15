# Feature: doc-exchange-center, Property 1: 子项目注册 Round-Trip
"""
Property-based tests for sub-project registration.

**Validates: Requirements 1.1, 1.2, 1.5**
"""

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from doc_exchange.models import Base, ProjectSpace
from doc_exchange.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid subproject names: non-empty strings (printable, no leading/trailing whitespace)
valid_name = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Pc", "Pd"), whitelist_characters="-_"),
    min_size=1,
    max_size=64,
)

# Valid subproject types: non-empty strings
valid_type = st.sampled_from(["development", "testing", "ops", "design", "deployment", "upgrade", "frontend", "backend"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prop_db_session():
    """Isolated in-memory SQLite session for each property test invocation."""
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
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # Create a default project space
    space = ProjectSpace(
        id=str(uuid.uuid4()),
        name="prop-test-space",
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(space)
    session.flush()

    yield session, space.id

    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Property 1: 子项目注册 Round-Trip
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(name=valid_name, proj_type=valid_type)
def test_prop_subproject_registration_round_trip(name: str, proj_type: str):
    """
    Property 1: 子项目注册 Round-Trip

    For any valid subproject registration request (name and type),
    after registration the subproject can be retrieved by the returned
    project_id, and its name and type match what was registered.

    **Validates: Requirements 1.1, 1.2, 1.5**
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        space = ProjectSpace(
            id=str(uuid.uuid4()),
            name="prop-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(space)
        session.flush()

        svc = ProjectService(session)

        # Register the subproject
        registered = svc.register(name=name, type=proj_type, project_space_id=space.id)

        # project_id must be a valid UUID (Req 1.2)
        assert registered.id, "project_id must be non-empty"
        uuid.UUID(registered.id)  # raises ValueError if not a valid UUID

        # Round-trip: retrieve by project_id (Req 1.1, 1.5)
        fetched = svc.get(registered.id, space.id)
        assert fetched is not None, "registered subproject must be retrievable by project_id"
        assert fetched.name == name, f"name mismatch: expected {name!r}, got {fetched.name!r}"
        assert fetched.type == proj_type, f"type mismatch: expected {proj_type!r}, got {fetched.type!r}"

        # Also verify it appears in list_subprojects (Req 1.5)
        all_projects = svc.list_subprojects(space.id)
        ids_in_list = {p.id for p in all_projects}
        assert registered.id in ids_in_list, "registered subproject must appear in list_subprojects"
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100)
@given(
    entries=st.lists(
        st.tuples(valid_name, valid_type),
        min_size=2,
        max_size=10,
        unique_by=lambda x: x[0],  # unique names to avoid confusion
    )
)
def test_prop_multiple_registrations_unique_ids(entries):
    """
    Property 1 (uniqueness part): Multiple subproject registrations produce
    all-distinct project_ids.

    **Validates: Requirements 1.2, 1.5**
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        space = ProjectSpace(
            id=str(uuid.uuid4()),
            name="prop-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(space)
        session.flush()

        svc = ProjectService(session)

        registered_ids = []
        for name, proj_type in entries:
            proj = svc.register(name=name, type=proj_type, project_space_id=space.id)
            registered_ids.append(proj.id)

        # All project_ids must be unique (Req 1.2)
        assert len(registered_ids) == len(set(registered_ids)), (
            f"Duplicate project_ids found: {registered_ids}"
        )

        # list_subprojects must return all registered projects (Req 1.5)
        all_projects = svc.list_subprojects(space.id)
        assert len(all_projects) == len(entries), (
            f"Expected {len(entries)} subprojects, got {len(all_projects)}"
        )
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 2: 阶段变更更新属性
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 2: 阶段变更更新属性

valid_stage = st.sampled_from(["design", "development", "testing", "deployment", "upgrade"])


@settings(max_examples=100)
@given(target_stage=valid_stage)
def test_prop_stage_change_updates_stage(target_stage: str):
    """
    Property 2: 阶段变更更新属性

    For any registered subproject and any valid target stage, after calling
    change_stage(), the subproject's stage equals the target stage and
    stage_updated_at is >= the timestamp recorded before the change.

    **Validates: Requirements 1.3**
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        space = ProjectSpace(
            id=str(uuid.uuid4()),
            name="prop-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(space)
        session.flush()

        svc = ProjectService(session)

        # Register a subproject with an initial stage
        subproject = svc.register(
            name="test-subproject",
            type="development",
            project_space_id=space.id,
            stage="design",
        )

        # Record the timestamp before the change
        pre_change_timestamp = subproject.stage_updated_at

        # Perform the stage change
        updated = svc.change_stage(
            project_id=subproject.id,
            new_stage=target_stage,
            project_space_id=space.id,
        )

        # Assert stage equals the target stage
        assert updated.stage == target_stage, (
            f"Expected stage {target_stage!r}, got {updated.stage!r}"
        )

        # Assert stage_updated_at is >= pre-change timestamp
        assert updated.stage_updated_at >= pre_change_timestamp, (
            f"stage_updated_at {updated.stage_updated_at} should be >= {pre_change_timestamp}"
        )
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Property 3: 注册缺失字段返回错误
# ---------------------------------------------------------------------------

# Feature: doc-exchange-center, Property 3: 注册缺失字段返回错误

from doc_exchange.services.errors import DocExchangeError

# Strategy: generate a request missing name, type, or both
# We represent the request as (name_or_none, type_or_none)
missing_field_request = st.one_of(
    # missing name only
    st.tuples(st.none(), st.sampled_from(["development", "testing", "ops", "design"])),
    # missing type only
    st.tuples(valid_name, st.none()),
    # missing both
    st.tuples(st.none(), st.none()),
    # empty string name (also counts as missing)
    st.tuples(st.just(""), st.sampled_from(["development", "testing", "ops", "design"])),
    # empty string type
    st.tuples(valid_name, st.just("")),
)


@settings(max_examples=100)
@given(request=missing_field_request)
def test_prop_register_missing_fields_returns_error(request):
    """
    Property 3: 注册缺失字段返回错误

    For any registration request missing name or type (or both), the service
    must raise DocExchangeError with error_code MISSING_REQUIRED_FIELD, and
    the error details must mention the name of the missing field.

    **Validates: Requirements 1.4**
    """
    name_val, type_val = request

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        space = ProjectSpace(
            id=str(uuid.uuid4()),
            name="prop-space",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(space)
        session.flush()

        svc = ProjectService(session)

        # Determine which fields are missing
        missing_fields = []
        if not name_val:
            missing_fields.append("name")
        if not type_val:
            missing_fields.append("type")

        with pytest.raises(DocExchangeError) as exc_info:
            svc.register(
                name=name_val,
                type=type_val,
                project_space_id=space.id,
            )

        err = exc_info.value
        assert err.error_code == "MISSING_REQUIRED_FIELD", (
            f"Expected error_code MISSING_REQUIRED_FIELD, got {err.error_code!r}"
        )

        # The error details must mention each missing field name
        reported = err.details.get("missing_fields", []) if err.details else []
        for field in missing_fields:
            assert field in reported, (
                f"Expected missing field {field!r} to be reported in details, got {reported!r}"
            )
    finally:
        session.close()
        engine.dispose()
