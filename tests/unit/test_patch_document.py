"""
Unit tests for DocumentService.patch() and the _apply_unified_diff helper.

Covers:
- Basic patch application (add/remove/context lines)
- PATCH_BASE_MISMATCH when base_version != latest
- DOC_NOT_FOUND when document doesn't exist
- PATCH_APPLY_FAILED when patch doesn't match base content
- CONTENT_UNCHANGED when patch produces identical content
- Round-trip: push then patch produces correct new version
"""

import difflib

import pytest

from doc_exchange.services.document_service import _apply_unified_diff
from doc_exchange.services.errors import DocExchangeError
from doc_exchange.services.schemas import PatchRequest, PushRequest


# ---------------------------------------------------------------------------
# _apply_unified_diff unit tests
# ---------------------------------------------------------------------------


def _make_diff(old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(difflib.unified_diff(old_lines, new_lines, fromfile="old", tofile="new"))


class TestApplyUnifiedDiff:
    def test_add_line(self):
        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"
        patch = _make_diff(old, new)
        assert _apply_unified_diff(old, patch) == new

    def test_remove_line(self):
        old = "line1\nline2\nline3\n"
        new = "line1\nline3\n"
        patch = _make_diff(old, new)
        assert _apply_unified_diff(old, patch) == new

    def test_modify_line(self):
        old = "# Title\n\nSome content here.\n"
        new = "# Title\n\nUpdated content here.\n"
        patch = _make_diff(old, new)
        assert _apply_unified_diff(old, patch) == new

    def test_empty_patch_returns_original(self):
        old = "unchanged content\n"
        assert _apply_unified_diff(old, "") == old
        assert _apply_unified_diff(old, "   ") == old

    def test_patch_mismatch_raises(self):
        old = "line1\nline2\n"
        wrong_base = "different\ncontent\n"
        patch = _make_diff(old, "line1\nline2\nline3\n")
        with pytest.raises(ValueError, match="does not match base content"):
            _apply_unified_diff(wrong_base, patch)

    def test_multiline_add_and_remove(self):
        old = "# API\n\n## GET /users\n\nReturns users.\n"
        new = "# API\n\n## GET /users\n\nReturns all users.\n\n## POST /users\n\nCreates a user.\n"
        patch = _make_diff(old, new)
        assert _apply_unified_diff(old, patch) == new


# ---------------------------------------------------------------------------
# DocumentService.patch() integration tests
# ---------------------------------------------------------------------------


def _make_doc_service(db_session, tmp_docs_root):
    from doc_exchange.services.audit_log_service import AuditLogService
    from doc_exchange.services.document_service import DocumentService
    audit = AuditLogService(db_session)
    return DocumentService(db=db_session, docs_root=tmp_docs_root, audit_log_service=audit)


def _push(svc, doc_id, content, pushed_by="agent-1", project_space_id="space-1"):
    return svc.push(PushRequest(
        doc_id=doc_id,
        content=content,
        pushed_by=pushed_by,
        project_space_id=project_space_id,
    ))


def _patch(svc, doc_id, base_version, patch, pushed_by="agent-1", project_space_id="space-1"):
    return svc.patch(PatchRequest(
        doc_id=doc_id,
        base_version=base_version,
        patch=patch,
        pushed_by=pushed_by,
        project_space_id=project_space_id,
    ))


class TestPatchDocument:
    def test_patch_produces_new_version(self, db_session, default_space, tmp_docs_root):
        svc = _make_doc_service(db_session, tmp_docs_root)
        old = "# Design\n\nInitial content.\n"
        new = "# Design\n\nUpdated content.\n"
        _push(svc, "sub1/design", old, project_space_id=default_space.id)

        patch = _make_diff(old, new)
        result = _patch(svc, "sub1/design", 1, patch, project_space_id=default_space.id)

        assert result.version == 2
        assert result.status == "published"

    def test_patch_content_is_correct(self, db_session, default_space, tmp_docs_root):
        svc = _make_doc_service(db_session, tmp_docs_root)
        old = "# API\n\n## GET /items\n\nReturns items.\n"
        new = "# API\n\n## GET /items\n\nReturns all items.\n\n## POST /items\n\nCreates item.\n"
        _push(svc, "sub1/api", old, project_space_id=default_space.id)

        patch = _make_diff(old, new)
        _patch(svc, "sub1/api", 1, patch, project_space_id=default_space.id)

        fetched = svc.get("sub1/api", default_space.id)
        assert fetched.content == new

    def test_patch_base_mismatch_raises(self, db_session, default_space, tmp_docs_root):
        svc = _make_doc_service(db_session, tmp_docs_root)
        old = "v1 content\n"
        _push(svc, "sub1/requirement", old, project_space_id=default_space.id)
        _push(svc, "sub1/requirement", "v2 content\n", project_space_id=default_space.id)

        patch = _make_diff(old, "v1 patched\n")
        with pytest.raises(DocExchangeError) as exc_info:
            _patch(svc, "sub1/requirement", 1, patch, project_space_id=default_space.id)
        assert exc_info.value.error_code == "PATCH_BASE_MISMATCH"
        assert exc_info.value.details["latest_version"] == 2

    def test_patch_doc_not_found_raises(self, db_session, default_space, tmp_docs_root):
        svc = _make_doc_service(db_session, tmp_docs_root)
        patch = _make_diff("old\n", "new\n")
        with pytest.raises(DocExchangeError) as exc_info:
            _patch(svc, "sub1/design", 1, patch, project_space_id=default_space.id)
        assert exc_info.value.error_code == "DOC_NOT_FOUND"

    def test_patch_apply_failed_raises(self, db_session, default_space, tmp_docs_root):
        svc = _make_doc_service(db_session, tmp_docs_root)
        _push(svc, "sub1/design", "actual content\n", project_space_id=default_space.id)

        # Patch generated against different base
        bad_patch = _make_diff("wrong base\n", "wrong base patched\n")
        with pytest.raises(DocExchangeError) as exc_info:
            _patch(svc, "sub1/design", 1, bad_patch, project_space_id=default_space.id)
        assert exc_info.value.error_code == "PATCH_APPLY_FAILED"

    def test_patch_identical_content_raises_content_unchanged(self, db_session, default_space, tmp_docs_root):
        svc = _make_doc_service(db_session, tmp_docs_root)
        content = "# Same\n\nNo changes.\n"
        _push(svc, "sub1/design", content, project_space_id=default_space.id)

        # Patch that results in identical content (empty diff)
        patch = _make_diff(content, content)
        with pytest.raises(DocExchangeError) as exc_info:
            _patch(svc, "sub1/design", 1, patch, project_space_id=default_space.id)
        assert exc_info.value.error_code == "CONTENT_UNCHANGED"
