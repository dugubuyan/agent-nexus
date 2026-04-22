"""
Main entry point for the Doc Exchange Center.

Sets up the SQLite database, creates all services, starts FileWatcherService
in a background thread, then starts the MCP server in HTTP mode so multiple
agents can connect simultaneously.

Default: http://0.0.0.0:10000/mcp
Configure via env vars:
  DOC_EXCHANGE_DB_URL           (default: sqlite:///doc_exchange.db)
  DOC_EXCHANGE_DOCS_ROOT        (default: ./workspace/docs)
  DOC_EXCHANGE_DEFAULT_SPACE_ID (default: default)
  DOC_EXCHANGE_HOST             (default: 0.0.0.0)
  DOC_EXCHANGE_PORT             (default: 10000)
"""

import os
import signal
import sys

# Read config BEFORE importing server.py, because server.py creates the
# FastMCP instance at import time and reads host/port from env vars.
HOST = os.environ.get("DOC_EXCHANGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("DOC_EXCHANGE_PORT", "10086"))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from doc_exchange.models import Base
from doc_exchange.mcp.dependencies import ServiceContainer
from doc_exchange.mcp.server import mcp
from doc_exchange.services.file_watcher_service import FileWatcherService

DB_URL = os.environ.get("DOC_EXCHANGE_DB_URL", "sqlite:///doc_exchange.db")
DOCS_ROOT = os.environ.get("DOC_EXCHANGE_DOCS_ROOT", "./workspace/docs")
DEFAULT_SPACE_ID = os.environ.get("DOC_EXCHANGE_DEFAULT_SPACE_ID", "default")


def main() -> None:
    # 1. Set up database
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # 2. Ensure docs root exists
    os.makedirs(DOCS_ROOT, exist_ok=True)

    # 3. Create services (for FileWatcher only; MCP tools create their own sessions)
    db_session = SessionLocal()
    container = ServiceContainer(db_session=db_session, docs_root=DOCS_ROOT)

    # 4. Start FileWatcherService in background thread
    watcher = FileWatcherService(
        docs_root=DOCS_ROOT,
        document_service=container.document_service,
        default_space_id=DEFAULT_SPACE_ID,
    )
    watcher.start()

    # 5. Graceful shutdown on SIGINT/SIGTERM
    def _shutdown(signum, frame):
        print("\nShutting down...")
        watcher.stop()
        db_session.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 6. Start MCP server in HTTP mode (multiple agents can connect simultaneously)
    print(f"Doc Exchange Center running at http://{HOST}:{PORT}/mcp")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
