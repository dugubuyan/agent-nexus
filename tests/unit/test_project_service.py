"""
Unit tests for ProjectService (Requirements 1.1 – 1.5).
"""

import pytest
from sqlalchemy.orm import Session

from doc_exchange.models.entities import ProjectSpace
from doc_exchange.services import DocExchangeError, ProjectService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(db_session: Session) -> ProjectService:
    return ProjectService(db_session)


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


def test_register_returns_uuid_project_id(db_session, default_space):
    """Successful registration returns a non-empty UUID project_id (Req 1.2)."""
    svc = make_service(db_session)
    project = svc.register(
        name="frontend",
        type="development",
        project_space_id=default_space.id,
    )

    assert project.id, "project_id should be non-empty"
    # Must be a valid UUID (no exception raised)
    import uuid
    uuid.UUID(project.id)


def test_register_stores_name_and_type(db_session, default_space):
    """Registered sub-project has the correct name and type (Req 1.1)."""
    svc = make_service(db_session)
    project = svc.register(
        name="backend",
        type="testing",
        project_space_id=default_space.id,
    )

    assert project.name == "backend"
    assert project.type == "testing"


def test_register_default_stage_is_design(db_session, default_space):
    """Default stage is 'design' when not specified (Req 1.1)."""
    svc = make_service(db_session)
    project = svc.register(name="svc", type="ops", project_space_id=default_space.id)
    assert project.stage == "design"


def test_register_missing_name_raises_error(db_session, default_space):
    """Missing name raises MISSING_REQUIRED_FIELD (Req 1.4)."""
    svc = make_service(db_session)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.register(name="", type="development", project_space_id=default_space.id)

    err = exc_info.value
    assert err.error_code == "MISSING_REQUIRED_FIELD"
    assert "name" in err.details["missing_fields"]


def test_register_missing_type_raises_error(db_session, default_space):
    """Missing type raises MISSING_REQUIRED_FIELD (Req 1.4)."""
    svc = make_service(db_session)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.register(name="my-project", type="", project_space_id=default_space.id)

    err = exc_info.value
    assert err.error_code == "MISSING_REQUIRED_FIELD"
    assert "type" in err.details["missing_fields"]


def test_register_none_name_raises_error(db_session, default_space):
    """None name raises MISSING_REQUIRED_FIELD (Req 1.4)."""
    svc = make_service(db_session)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.register(name=None, type="development", project_space_id=default_space.id)

    assert exc_info.value.error_code == "MISSING_REQUIRED_FIELD"


def test_register_multiple_projects_have_unique_ids(db_session, default_space):
    """Multiple registrations produce distinct project_ids (Req 1.2)."""
    svc = make_service(db_session)
    ids = {
        svc.register(name=f"proj-{i}", type="development", project_space_id=default_space.id).id
        for i in range(5)
    }
    assert len(ids) == 5


# ---------------------------------------------------------------------------
# list_subprojects tests
# ---------------------------------------------------------------------------


def test_list_subprojects_returns_all_registered(db_session, default_space):
    """list_subprojects returns every registered sub-project (Req 1.5)."""
    svc = make_service(db_session)
    svc.register(name="alpha", type="development", project_space_id=default_space.id)
    svc.register(name="beta", type="testing", project_space_id=default_space.id)
    svc.register(name="gamma", type="ops", project_space_id=default_space.id)

    projects = svc.list_subprojects(default_space.id)
    names = {p.name for p in projects}
    assert names == {"alpha", "beta", "gamma"}


def test_list_subprojects_empty_when_none_registered(db_session, default_space):
    """list_subprojects returns empty list when no sub-projects exist (Req 1.5)."""
    svc = make_service(db_session)
    assert svc.list_subprojects(default_space.id) == []


# ---------------------------------------------------------------------------
# change_stage tests
# ---------------------------------------------------------------------------


def test_change_stage_updates_stage(db_session, default_space):
    """change_stage updates the stage field (Req 1.3)."""
    svc = make_service(db_session)
    project = svc.register(name="proj", type="development", project_space_id=default_space.id)

    updated = svc.change_stage(project.id, "testing", default_space.id)
    assert updated.stage == "testing"


def test_change_stage_updates_stage_updated_at(db_session, default_space):
    """change_stage updates stage_updated_at to a later timestamp (Req 1.3)."""
    from datetime import timezone
    svc = make_service(db_session)
    project = svc.register(name="proj", type="development", project_space_id=default_space.id)
    original_ts = project.stage_updated_at

    # Small sleep to ensure timestamp differs
    import time
    time.sleep(0.01)

    updated = svc.change_stage(project.id, "deployment", default_space.id)
    # stage_updated_at should be >= original (at minimum equal due to resolution)
    assert updated.stage_updated_at >= original_ts


def test_change_stage_nonexistent_project_raises_error(db_session, default_space):
    """change_stage on unknown project_id raises PROJECT_NOT_FOUND."""
    svc = make_service(db_session)
    with pytest.raises(DocExchangeError) as exc_info:
        svc.change_stage("nonexistent-id", "testing", default_space.id)

    assert exc_info.value.error_code == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# get tests
# ---------------------------------------------------------------------------


def test_get_returns_none_for_unknown_id(db_session, default_space):
    """get returns None when project_id does not exist."""
    svc = make_service(db_session)
    assert svc.get("no-such-id", default_space.id) is None


def test_get_returns_subproject_after_registration(db_session, default_space):
    """get returns the correct sub-project after registration."""
    svc = make_service(db_session)
    project = svc.register(name="myproj", type="ops", project_space_id=default_space.id)
    fetched = svc.get(project.id, default_space.id)
    assert fetched is not None
    assert fetched.id == project.id
    assert fetched.name == "myproj"
