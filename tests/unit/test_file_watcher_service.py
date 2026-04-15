"""
Unit tests for FileWatcherService.

Covers:
- _parse_path() for requirement.md, config_dev.md, and invalid paths
- _process_file() push/skip logic based on hash comparison
- Debounce: multiple rapid events on same file trigger only one push
"""

import hashlib
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from doc_exchange.services.file_watcher_service import FileWatcherService
from doc_exchange.services.schemas import PushRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_watcher(docs_root: str, document_service=None) -> FileWatcherService:
    if document_service is None:
        document_service = MagicMock()
        document_service.get_latest_hash.return_value = None
    return FileWatcherService(
        docs_root=docs_root,
        document_service=document_service,
        default_space_id="default",
    )


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# _parse_path tests
# ---------------------------------------------------------------------------


class TestParsePath:
    def test_requirement_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "requirement.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/requirement"
        assert space_id == "space1"

    def test_design_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "design.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/design"
        assert space_id == "space1"

    def test_api_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "api.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/api"
        assert space_id == "space1"

    def test_task_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "task.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/task"
        assert space_id == "space1"

    def test_config_dev_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "config_dev.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/config/dev"
        assert space_id == "space1"

    def test_config_test_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "config_test.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/config/test"
        assert space_id == "space1"

    def test_config_prod_md(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "config_prod.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id == "proj-a/config/prod"
        assert space_id == "space1"

    def test_invalid_path_too_shallow(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "requirement.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id is None
        assert space_id is None

    def test_invalid_path_too_deep(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "sub", "requirement.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id is None
        assert space_id is None

    def test_invalid_path_outside_docs_root(self, tmp_docs_root, tmp_path):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(str(tmp_path), "other", "proj-a", "requirement.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id is None
        assert space_id is None

    def test_invalid_filename_unknown_type(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "unknown.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id is None
        assert space_id is None

    def test_invalid_config_unknown_stage(self, tmp_docs_root):
        watcher = _make_watcher(tmp_docs_root)
        path = os.path.join(tmp_docs_root, "space1", "proj-a", "config_staging.md")
        doc_id, space_id = watcher._parse_path(path)
        assert doc_id is None
        assert space_id is None


# ---------------------------------------------------------------------------
# _process_file tests
# ---------------------------------------------------------------------------


class TestProcessFile:
    def test_calls_push_when_hash_differs(self, tmp_docs_root):
        """push() is called when file content hash differs from latest."""
        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = "old_hash_value"

        watcher = _make_watcher(tmp_docs_root, doc_service)

        # Create the file
        space_dir = os.path.join(tmp_docs_root, "space1", "proj-a")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "requirement.md")
        content = "# New content"
        with open(file_path, "w") as f:
            f.write(content)

        watcher._process_file(file_path)

        doc_service.push.assert_called_once()
        call_args = doc_service.push.call_args[0][0]
        assert isinstance(call_args, PushRequest)
        assert call_args.doc_id == "proj-a/requirement"
        assert call_args.content == content
        assert call_args.pushed_by == "system_llm"
        assert call_args.project_space_id == "space1"

    def test_skips_push_when_hash_same(self, tmp_docs_root):
        """push() is NOT called when file content hash matches latest."""
        content = "# Same content"
        content_hash = _sha256(content)

        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = content_hash

        watcher = _make_watcher(tmp_docs_root, doc_service)

        space_dir = os.path.join(tmp_docs_root, "space1", "proj-a")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "requirement.md")
        with open(file_path, "w") as f:
            f.write(content)

        watcher._process_file(file_path)

        doc_service.push.assert_not_called()

    def test_skips_push_for_invalid_path(self, tmp_docs_root):
        """push() is NOT called for files with unrecognized paths."""
        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(tmp_docs_root, doc_service)

        # Path too shallow — only one level under docs_root
        space_dir = os.path.join(tmp_docs_root, "space1")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "requirement.md")
        with open(file_path, "w") as f:
            f.write("content")

        watcher._process_file(file_path)

        doc_service.push.assert_not_called()

    def test_skips_push_when_no_latest_hash(self, tmp_docs_root):
        """push() IS called when get_latest_hash returns None (new document)."""
        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(tmp_docs_root, doc_service)

        space_dir = os.path.join(tmp_docs_root, "space1", "proj-a")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "design.md")
        with open(file_path, "w") as f:
            f.write("# Design doc")

        watcher._process_file(file_path)

        doc_service.push.assert_called_once()


# ---------------------------------------------------------------------------
# Debounce tests
# ---------------------------------------------------------------------------


class TestDebounce:
    def test_multiple_rapid_events_trigger_one_push(self, tmp_docs_root):
        """Multiple rapid file events on the same file result in only one push."""
        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(tmp_docs_root, doc_service)

        space_dir = os.path.join(tmp_docs_root, "space1", "proj-a")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "requirement.md")
        with open(file_path, "w") as f:
            f.write("# Content")

        # Fire 5 rapid events
        for _ in range(5):
            watcher._on_file_changed(file_path)

        # Wait for debounce timer to fire (500ms + buffer)
        time.sleep(0.8)

        assert doc_service.push.call_count == 1

    def test_events_separated_by_more_than_500ms_trigger_multiple_pushes(
        self, tmp_docs_root
    ):
        """Events separated by >500ms each trigger their own push."""
        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None

        watcher = _make_watcher(tmp_docs_root, doc_service)

        space_dir = os.path.join(tmp_docs_root, "space1", "proj-a")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "requirement.md")

        # First event
        with open(file_path, "w") as f:
            f.write("# Version 1")
        watcher._on_file_changed(file_path)
        time.sleep(0.7)  # let first debounce fire

        # Second event with different content
        with open(file_path, "w") as f:
            f.write("# Version 2")
        watcher._on_file_changed(file_path)
        time.sleep(0.7)  # let second debounce fire

        assert doc_service.push.call_count == 2

    def test_debounce_resets_timer_on_repeated_event(self, tmp_docs_root):
        """Repeated events within 500ms reset the timer (only one push total)."""
        push_times = []

        doc_service = MagicMock()
        doc_service.get_latest_hash.return_value = None
        doc_service.push.side_effect = lambda req: push_times.append(time.time())

        watcher = _make_watcher(tmp_docs_root, doc_service)

        space_dir = os.path.join(tmp_docs_root, "space1", "proj-a")
        os.makedirs(space_dir, exist_ok=True)
        file_path = os.path.join(space_dir, "requirement.md")
        with open(file_path, "w") as f:
            f.write("# Content")

        start = time.time()
        # Fire events at 0ms, 200ms, 400ms — all within 500ms of each other
        watcher._on_file_changed(file_path)
        time.sleep(0.2)
        watcher._on_file_changed(file_path)
        time.sleep(0.2)
        watcher._on_file_changed(file_path)

        # Wait for final debounce to fire
        time.sleep(0.7)

        assert len(push_times) == 1
        # Push should happen ~500ms after the last event (at ~900ms from start)
        assert push_times[0] - start >= 0.8
