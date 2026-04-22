"""
Unit tests for the Analyzer module.

Covers:
- RuleEngineAnalyzer returns correct affected projects for requirement doc
- RuleEngineAnalyzer returns correct affected projects for api doc
- RuleEngineAnalyzer returns correct affected projects for config doc
- RuleEngineAnalyzer returns empty for task doc
- AnalyzerService falls back to rule engine when primary analyzer fails
- AnalyzerService logs failure to AuditLog when primary fails
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from doc_exchange.analyzer import (
    AnalyzerService,
    RuleEngineAnalyzer,
)
from doc_exchange.analyzer.base import AnalysisResult, Analyzer
from doc_exchange.services.audit_log_service import AuditLogService


# ---------------------------------------------------------------------------
# Helpers — use SimpleNamespace to avoid SQLAlchemy ORM instrumentation issues
# ---------------------------------------------------------------------------


def _make_doc(doc_type: str, space_id: str = "space-1"):
    return SimpleNamespace(
        id=f"subproj-1/{doc_type}",
        project_space_id=space_id,
        subproject_id="subproj-1",
        doc_type=doc_type,
        doc_variant=None,
        latest_version=1,
        created_at=datetime.now(timezone.utc),
    )


def _make_version(doc_id: str, version: int = 1, space_id: str = "space-1"):
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        project_space_id=space_id,
        version=version,
        content_hash="abc123",
        pushed_by="proj-a",
        status="published",
        is_milestone=False,
        milestone_stage=None,
        pushed_at=datetime.now(timezone.utc),
        published_at=datetime.now(timezone.utc),
    )


def _make_subproject(subproject_type: str, space_id: str = "space-1"):
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        project_space_id=space_id,
        name=f"{subproject_type}-project",
        type=subproject_type,
        stage="development",
        stage_updated_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# RuleEngineAnalyzer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_engine_requirement_affects_testing_and_development():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("requirement")
    version = _make_version(doc.id)

    testing_sp = _make_subproject("testing")
    dev_sp = _make_subproject("development")
    ops_sp = _make_subproject("ops")

    result = await analyzer.analyze(doc, version, [testing_sp, dev_sp, ops_sp])

    assert isinstance(result, AnalysisResult)
    assert result.doc_id == doc.id
    assert result.version == 1

    affected_ids = {ap.project_id for ap in result.affected_projects}
    assert testing_sp.id in affected_ids
    assert dev_sp.id in affected_ids
    assert ops_sp.id not in affected_ids


@pytest.mark.asyncio
async def test_rule_engine_requirement_task_titles():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("requirement")
    version = _make_version(doc.id)

    testing_sp = _make_subproject("testing")
    dev_sp = _make_subproject("development")

    result = await analyzer.analyze(doc, version, [testing_sp, dev_sp])

    for ap in result.affected_projects:
        assert len(ap.tasks) == 1
        assert ap.tasks[0].title == "Review requirement changes"


@pytest.mark.asyncio
async def test_rule_engine_api_affects_testing_and_development():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("api")
    version = _make_version(doc.id)

    testing_sp = _make_subproject("testing")
    dev_sp = _make_subproject("development")
    ops_sp = _make_subproject("ops")

    result = await analyzer.analyze(doc, version, [testing_sp, dev_sp, ops_sp])

    affected_ids = {ap.project_id for ap in result.affected_projects}
    assert testing_sp.id in affected_ids
    assert dev_sp.id in affected_ids
    assert ops_sp.id not in affected_ids


@pytest.mark.asyncio
async def test_rule_engine_api_task_titles():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("api")
    version = _make_version(doc.id)

    testing_sp = _make_subproject("testing")
    dev_sp = _make_subproject("development")

    result = await analyzer.analyze(doc, version, [testing_sp, dev_sp])

    for ap in result.affected_projects:
        assert ap.tasks[0].title == "Review API changes"


@pytest.mark.asyncio
async def test_rule_engine_config_affects_only_ops():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("config")
    version = _make_version(doc.id)

    testing_sp = _make_subproject("testing")
    dev_sp = _make_subproject("development")
    ops_sp = _make_subproject("ops")

    result = await analyzer.analyze(doc, version, [testing_sp, dev_sp, ops_sp])

    affected_ids = {ap.project_id for ap in result.affected_projects}
    assert ops_sp.id in affected_ids
    assert testing_sp.id not in affected_ids
    assert dev_sp.id not in affected_ids


@pytest.mark.asyncio
async def test_rule_engine_config_task_title():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("config")
    version = _make_version(doc.id)
    ops_sp = _make_subproject("ops")

    result = await analyzer.analyze(doc, version, [ops_sp])

    assert len(result.affected_projects) == 1
    assert result.affected_projects[0].tasks[0].title == "Review config changes"


@pytest.mark.asyncio
async def test_rule_engine_task_doc_returns_empty():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("task")
    version = _make_version(doc.id)

    testing_sp = _make_subproject("testing")
    dev_sp = _make_subproject("development")
    ops_sp = _make_subproject("ops")

    result = await analyzer.analyze(doc, version, [testing_sp, dev_sp, ops_sp])

    assert result.affected_projects == []


@pytest.mark.asyncio
async def test_rule_engine_empty_subprojects_returns_empty():
    analyzer = RuleEngineAnalyzer()
    doc = _make_doc("requirement")
    version = _make_version(doc.id)

    result = await analyzer.analyze(doc, version, [])

    assert result.affected_projects == []


# ---------------------------------------------------------------------------
# AnalyzerService fallback tests
# ---------------------------------------------------------------------------


class _FailingAnalyzer(Analyzer):
    """Always raises an exception to simulate a broken analyzer."""

    async def analyze(self, doc, new_version, all_subprojects):
        raise RuntimeError("Simulated analyzer failure")


@pytest.mark.asyncio
async def test_analyzer_service_falls_back_to_rule_engine_on_failure(db_session, default_space):
    doc = _make_doc("requirement", space_id=default_space.id)
    version = _make_version(doc.id, space_id=default_space.id)
    testing_sp = _make_subproject("testing", space_id=default_space.id)
    dev_sp = _make_subproject("development", space_id=default_space.id)

    audit_svc = AuditLogService(db=db_session)
    fallback = RuleEngineAnalyzer()
    service = AnalyzerService(
        analyzer=_FailingAnalyzer(),
        fallback=fallback,
        audit_log_service=audit_svc,
    )

    result = await service.analyze(doc, version, [testing_sp, dev_sp])

    # Should still return a valid result from the rule engine
    assert isinstance(result, AnalysisResult)
    affected_ids = {ap.project_id for ap in result.affected_projects}
    assert testing_sp.id in affected_ids
    assert dev_sp.id in affected_ids


@pytest.mark.asyncio
async def test_analyzer_service_logs_failure_to_audit_log(db_session, default_space):
    doc = _make_doc("api", space_id=default_space.id)
    version = _make_version(doc.id, space_id=default_space.id)

    audit_svc = AuditLogService(db=db_session)
    fallback = RuleEngineAnalyzer()
    service = AnalyzerService(
        analyzer=_FailingAnalyzer(),
        fallback=fallback,
        audit_log_service=audit_svc,
    )

    await service.analyze(doc, version, [])

    logs = audit_svc.query(project_space_id=default_space.id)
    assert len(logs) == 1
    log = logs[0]
    assert log.operation_type == "analyzer_failure"
    assert log.result == "failure"
    assert log.target_id == doc.id
    assert "Simulated analyzer failure" in log.detail


@pytest.mark.asyncio
async def test_analyzer_service_uses_primary_when_it_succeeds(db_session, default_space):
    """When the primary analyzer succeeds, its result is returned directly."""
    doc = _make_doc("requirement", space_id=default_space.id)
    version = _make_version(doc.id, space_id=default_space.id)

    custom_result = AnalysisResult(
        affected_projects=[],
        doc_id=doc.id,
        version=version.version,
    )

    class _SuccessAnalyzer(Analyzer):
        async def analyze(self, doc, new_version, all_subprojects):
            return custom_result

    audit_svc = AuditLogService(db=db_session)
    fallback = RuleEngineAnalyzer()
    service = AnalyzerService(
        analyzer=_SuccessAnalyzer(),
        fallback=fallback,
        audit_log_service=audit_svc,
    )

    result = await service.analyze(doc, version, [_make_subproject("testing")])

    assert result is custom_result
    # No audit log entries should be written on success
    logs = audit_svc.query(project_space_id=default_space.id)
    assert len(logs) == 0
