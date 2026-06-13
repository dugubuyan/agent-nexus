"""
Main entry point for the Doc Exchange Center.

Starts the MCP server in HTTP mode so multiple agents can connect simultaneously.
Also serves the Web Dashboard at http://{HOST}:{PORT}/.

Default: http://0.0.0.0:10086/mcp
Configure via env vars:
  DOC_EXCHANGE_DB_URL           (default: sqlite:///doc_exchange.db)
  DOC_EXCHANGE_DOCS_ROOT        (default: ./workspace)
  DOC_EXCHANGE_HOST             (default: 0.0.0.0)
  DOC_EXCHANGE_PORT             (default: 10086)
"""

import os

# Read config BEFORE importing server.py, because server.py creates the
# FastMCP instance at import time and reads host/port from env vars.
HOST = os.environ.get("DOC_EXCHANGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("DOC_EXCHANGE_PORT", "10086"))

from doc_exchange.models import Base
from doc_exchange.mcp.dependencies import make_engine
from doc_exchange.mcp.server import mcp

DB_URL = os.environ.get("DOC_EXCHANGE_DB_URL", "sqlite:///doc_exchange.db")
DOCS_ROOT = os.environ.get("DOC_EXCHANGE_DOCS_ROOT", "./workspace")


def main() -> None:
    # 1. Set up database
    engine = make_engine(DB_URL)
    Base.metadata.create_all(engine)

    # 2. Initialize FTS5 full-text search table (idempotent, backfills historical data)
    from doc_exchange.search.fts import ensure_fts_table, rebuild_fts_index_if_empty
    ensure_fts_table(engine)
    rebuild_fts_index_if_empty(engine)

    # 3. Ensure docs root exists (used by document filesystem storage)
    os.makedirs(DOCS_ROOT, exist_ok=True)

    # 4. Start MCP server
    # Use stdio transport when MCP_TRANSPORT=stdio (e.g. Glama introspection)
    # Otherwise use streamable-HTTP for multi-agent use
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        print(f"Doc Exchange Center running at http://{HOST}:{PORT}/mcp")
        print(f"Web Dashboard at http://{HOST}:{PORT}/")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
