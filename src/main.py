"""
Main entry point for the AgentNexus.

Starts the MCP server in HTTP mode so multiple agents can connect simultaneously.
Also serves the Web Dashboard at http://{HOST}:{PORT}/.

Default: http://0.0.0.0:10086/mcp
Configure via env vars:
  AGENT_NEXUS_DB_URL           (default: sqlite:///agent_nexus.db)
  AGENT_NEXUS_DOCS_ROOT        (default: ./workspace)
  AGENT_NEXUS_HOST             (default: 0.0.0.0)
  AGENT_NEXUS_PORT             (default: 10086)
  AGENT_NEXUS_SSL_CERTFILE     (optional: path to SSL certificate, e.g. /etc/letsencrypt/live/domain/fullchain.pem)
  AGENT_NEXUS_SSL_KEYFILE      (optional: path to SSL private key, e.g. /etc/letsencrypt/live/domain/privkey.pem)
"""

import os

from dotenv import load_dotenv

# Load .env before reading any config — must happen before server.py is imported,
# because server.py creates the FastMCP instance at import time and reads host/port.
load_dotenv()

# Read config BEFORE importing server.py, because server.py creates the
# FastMCP instance at import time and reads host/port from env vars.
HOST = os.environ.get("AGENT_NEXUS_HOST", "0.0.0.0")
PORT = int(os.environ.get("AGENT_NEXUS_PORT", "10086"))

from agent_nexus.models import Base
from agent_nexus.mcp.dependencies import make_engine
from agent_nexus.mcp.server import mcp

DB_URL = os.environ.get("AGENT_NEXUS_DB_URL", "sqlite:///agent_nexus.db")
DOCS_ROOT = os.environ.get("AGENT_NEXUS_DOCS_ROOT", "./workspace")


def main() -> None:
    # 1. Set up database
    engine = make_engine(DB_URL)
    Base.metadata.create_all(engine)

    # 2. Initialize FTS5 full-text search table (idempotent, backfills historical data)
    from agent_nexus.search.fts import ensure_fts_table, rebuild_fts_index_if_empty
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
        ssl_certfile = os.environ.get("AGENT_NEXUS_SSL_CERTFILE")
        ssl_keyfile = os.environ.get("AGENT_NEXUS_SSL_KEYFILE")

        if ssl_certfile and ssl_keyfile:
            protocol = "https"
            ssl_kwargs = {"ssl_certfile": ssl_certfile, "ssl_keyfile": ssl_keyfile}
        else:
            protocol = "http"
            ssl_kwargs = {}

        print(f"AgentNexus running at {protocol}://{HOST}:{PORT}/mcp")
        print(f"Web Dashboard at {protocol}://{HOST}:{PORT}/")
        mcp.run(transport="streamable-http", **ssl_kwargs)


if __name__ == "__main__":
    main()
