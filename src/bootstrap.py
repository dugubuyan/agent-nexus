"""
Bootstrap: scan workspace and import existing .md files into the database.

Usage:
    python src/bootstrap.py

This is idempotent — safe to run multiple times. For each .md file found under
DOCS_ROOT/{space_id}/docs/ it will:
  1. Ensure the ProjectSpace record exists (created from the directory name)
  2. Ensure the SubProject record exists (created from the directory name)
  3. Push the document content (skipped if content hash already matches DB)

Configure via env vars (same as main.py):
  DOC_EXCHANGE_DB_URL    (default: sqlite:///doc_exchange.db)
  DOC_EXCHANGE_DOCS_ROOT (default: ./workspace)
"""

import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from doc_exchange.mcp.dependencies import make_engine, make_session_factory, ServiceContainer
from doc_exchange.models import Base
from doc_exchange.models.entities import ProjectSpace, SubProject
from doc_exchange.services.file_watcher_service import FileWatcherService
from doc_exchange.services.schemas import PushRequest

DB_URL = os.environ.get("DOC_EXCHANGE_DB_URL", "sqlite:///doc_exchange.db")
DOCS_ROOT = os.environ.get("DOC_EXCHANGE_DOCS_ROOT", "./workspace")
DEFAULT_SPACE_ID = os.environ.get("DOC_EXCHANGE_DEFAULT_SPACE_ID", "default")


def _ensure_space(session, space_id: str) -> ProjectSpace:
    space = session.query(ProjectSpace).filter(ProjectSpace.id == space_id).first()
    if space is None:
        space = ProjectSpace(
            id=space_id,
            name=space_id,  # use id as name; can be renamed later via MCP
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        session.add(space)
        session.flush()
        print(f"  [create] ProjectSpace: {space_id}")
    return space


def _ensure_subproject(session, subproject_id: str, space_id: str) -> SubProject:
    sp = (
        session.query(SubProject)
        .filter(SubProject.id == subproject_id, SubProject.project_space_id == space_id)
        .first()
    )
    if sp is None:
        sp = SubProject(
            id=subproject_id,
            project_space_id=space_id,
            name=subproject_id,  # use id as name; can be renamed later
            type="development",
            stage="development",
            stage_updated_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        session.add(sp)
        session.flush()
        print(f"  [create] SubProject: {subproject_id} (space={space_id})")
    return sp


def scan_and_import():
    engine = make_engine(DB_URL)
    Base.metadata.create_all(engine)
    SessionLocal = make_session_factory(DB_URL)
    session = SessionLocal()
    container = ServiceContainer(db_session=session, docs_root=DOCS_ROOT)
    doc_service = container.document_service

    # Reuse FileWatcherService's path parser
    watcher = FileWatcherService(
        docs_root=DOCS_ROOT,
        document_service=doc_service,
        default_space_id=DEFAULT_SPACE_ID,
    )

    docs_root_abs = os.path.abspath(DOCS_ROOT)
    imported = 0
    skipped = 0
    errors = 0

    for dirpath, _, filenames in os.walk(docs_root_abs):
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue

            file_path = os.path.join(dirpath, filename)
            doc_id, space_id = watcher._parse_path(file_path)

            if not doc_id:
                print(f"  [skip] unrecognized path: {file_path}")
                skipped += 1
                continue

            # Extract subproject_id from doc_id (format: {subproject_id}/...)
            subproject_id = doc_id.split("/")[0]

            try:
                content = open(file_path, encoding="utf-8").read()
            except OSError as e:
                print(f"  [error] cannot read {file_path}: {e}")
                errors += 1
                continue

            content_hash = hashlib.sha256(content.encode()).hexdigest()
            if doc_service.get_latest_hash(doc_id, space_id) == content_hash:
                print(f"  [skip] unchanged: {doc_id}")
                skipped += 1
                continue

            # Ensure parent records exist before pushing
            _ensure_space(session, space_id)
            _ensure_subproject(session, subproject_id, space_id)

            try:
                doc_service.push(PushRequest(
                    doc_id=doc_id,
                    content=content,
                    pushed_by="bootstrap",
                    project_space_id=space_id,
                ))
                session.commit()
                print(f"  [ok]   imported: {doc_id}")
                imported += 1
            except Exception as e:
                session.rollback()
                print(f"  [error] {doc_id}: {e}")
                errors += 1

    session.close()
    print(f"\nDone. imported={imported}, skipped={skipped}, errors={errors}")


if __name__ == "__main__":
    scan_and_import()
