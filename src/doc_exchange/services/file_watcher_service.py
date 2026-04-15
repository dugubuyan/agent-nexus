"""
FileWatcherService: watches /docs/ directory for .md file changes and
auto-pushes to DocumentService with pushed_by="system_llm".

Covers Requirements 2.3, 3.4, 11.1, 11.2
"""

import hashlib
import os
import threading

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from doc_exchange.services.document_service import DocumentService
from doc_exchange.services.schemas import PushRequest


class _DocFileEventHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self._callback = callback

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._callback(event.src_path)

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._callback(event.src_path)


class FileWatcherService:
    """
    Watches a docs root directory for .md file changes and pushes them
    to DocumentService as system_llm drafts.

    - Debounce: 500ms timer reset on repeated events for the same file
    - Hash dedup: skips push if content hash matches latest version
    - Path parsing: {docs_root}/{space_id}/{subproject_id}/{filename}.md
    """

    def __init__(
        self,
        docs_root: str,
        document_service: DocumentService,
        default_space_id: str,
    ):
        self._docs_root = os.path.abspath(docs_root)
        self._document_service = document_service
        self._default_space_id = default_space_id
        self._observer = Observer()
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start file watching in background thread."""
        handler = _DocFileEventHandler(self._on_file_changed)
        self._observer.schedule(handler, self._docs_root, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    def _on_file_changed(self, file_path: str) -> None:
        """Debounce: reset timer if same file triggers within 500ms."""
        with self._lock:
            if file_path in self._debounce_timers:
                self._debounce_timers[file_path].cancel()
            timer = threading.Timer(0.5, self._process_file, args=[file_path])
            self._debounce_timers[file_path] = timer
            timer.start()

    def _process_file(self, file_path: str) -> None:
        """Read file, compare hash, push if changed."""
        with self._lock:
            self._debounce_timers.pop(file_path, None)

        doc_id, space_id = self._parse_path(file_path)
        if not doc_id:
            return

        try:
            content = open(file_path, encoding="utf-8").read()
        except OSError:
            return

        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Skip if content unchanged
        if self._document_service.get_latest_hash(doc_id, space_id) == content_hash:
            return

        self._document_service.push(
            PushRequest(
                doc_id=doc_id,
                content=content,
                pushed_by="system_llm",
                project_space_id=space_id,
            )
        )

    def _parse_path(self, file_path: str) -> tuple[str | None, str | None]:
        """
        Parse file path to (doc_id, space_id).

        Expected format: {docs_root}/{space_id}/{subproject_id}/{filename}.md

        Supported filenames:
          requirement.md  → doc_id = {subproject_id}/requirement
          design.md       → doc_id = {subproject_id}/design
          api.md          → doc_id = {subproject_id}/api
          task.md         → doc_id = {subproject_id}/task
          config_dev.md   → doc_id = {subproject_id}/config/dev
          config_test.md  → doc_id = {subproject_id}/config/test
          config_prod.md  → doc_id = {subproject_id}/config/prod

        Returns (None, None) if path doesn't match expected format.
        """
        abs_path = os.path.abspath(file_path)
        docs_root = self._docs_root

        # Must be under docs_root
        if not abs_path.startswith(docs_root + os.sep):
            return None, None

        rel = abs_path[len(docs_root) + 1:]  # strip docs_root/
        parts = rel.split(os.sep)

        # Expect exactly: space_id / subproject_id / filename.md
        if len(parts) != 3:
            return None, None

        space_id, subproject_id, filename = parts

        if not filename.endswith(".md"):
            return None, None

        stem = filename[:-3]  # strip .md

        # Config files: config_{stage}.md
        if stem.startswith("config_"):
            stage = stem[len("config_"):]
            if stage not in ("dev", "test", "prod"):
                return None, None
            doc_id = f"{subproject_id}/config/{stage}"
            return doc_id, space_id

        # Standard doc types
        if stem in ("requirement", "design", "api", "task"):
            doc_id = f"{subproject_id}/{stem}"
            return doc_id, space_id

        return None, None
