# AgentNexus

**A service-boundary-aware coordination architecture for heterogeneous LLM code agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-191%20passing-brightgreen.svg)](tests/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19601563.svg)](https://doi.org/10.5281/zenodo.19601563)

> *"Service boundaries, not agent roles, are the appropriate primitive for coordinating LLM agents in real software development."*

## Overview

Existing multi-agent frameworks (ChatDev, MetaGPT) organize agents around **roles** within a single simulated organization. AgentNexus takes a different approach: it coordinates agents at the **service** granularity, matching how real software systems are actually structured.

Each service registers as a sub-project, publishes versioned Markdown documents (requirements, design, API specs, config), and subscribes to documents from services it depends on. When a document changes, subscribers receive a diff-aware notification containing both the structured diff and the full latest content — enabling targeted, context-aware code modifications.

## Key Features

- **Versioned document store** — SHA-256 dedup, full version history, per-service namespacing
- **Publish-subscribe notifications** — subscribe by exact doc ID or doc type
- **Diff-aware updates** — `get_my_updates_with_context` returns unified diff + full content in one call
- **Lifecycle stage tracking** — explicit `design → development → testing → deployment → upgrade` per service, with milestone snapshots on transitions
- **MCP HTTP server** — streamable-HTTP transport, multiple agents connect simultaneously
- **FileWatcher ingestion** — auto-ingest Markdown files from `/docs/` directory as draft documents
- **191 tests** — unit + property-based (Hypothesis)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Project Space                       │
│                                                      │
│  ┌──────────────┐    subscribe    ┌───────────────┐  │
│  │ search-      │ ──────────────► │ search-admin- │  │
│  │ service      │                 │ frontend      │  │
│  │              │  notification   │               │  │
│  │ api/v5 ──────┼────────────────►│               │  │
│  └──────────────┘                 └───────────────┘  │
│                                                      │
│              AgentNexus MCP Server                   │
│              http://0.0.0.0:10086/mcp                │
└─────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Initialize database
python -m alembic upgrade head

# Start server (default: http://0.0.0.0:10086/mcp)
python src/main.py
```

### Connect from Kiro / any MCP client

```json
{
  "mcpServers": {
    "doc-exchange": {
      "url": "http://localhost:10086/mcp"
    }
  }
}
```

### First steps

```
# Create a project space
create_space(name="my-project")

# Register a service
register_project(name="backend-api", type="development", project_space_id="<space_id>")

# Push a document
push_document(project_id="<project_id>", doc_id="<project_id>/api", content="# API Spec...")

# Subscribe frontend to backend's API docs
add_subscription(subscriber_project_id="<frontend_id>", project_space_id="<space_id>", target_doc_id="<backend_id>/api")

# Check updates (returns diff + full content)
get_my_updates_with_context(project_id="<frontend_id>")
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `create_space` | Create a Project Space |
| `register_project` | Register a sub-project (service) |
| `list_projects` | List all sub-projects in a space |
| `push_document` | Push a new document version |
| `get_document` | Retrieve a document (latest or specific version) |
| `get_my_updates_with_context` | Get unread notifications with diff + full content |
| `ack_update` | Mark a notification as read |
| `get_my_tasks` | Get pending tasks for a project |
| `get_config` | Get config document for a stage |
| `add_subscription` | Add a subscription rule |
| `publish_draft` | Confirm a draft document |
| `generate_steering_file` | Generate IDE steering file content |
| `get_project_id_by_name` | Look up project_id by name |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `DOC_EXCHANGE_DB_URL` | `sqlite:///doc_exchange.db` | Database URL |
| `DOC_EXCHANGE_DOCS_ROOT` | `./workspace` | Workspace root (docs live under `{root}/{space_id}/docs/`) |
| `DOC_EXCHANGE_HOST` | `0.0.0.0` | Server bind host |
| `DOC_EXCHANGE_PORT` | `10086` | Server port |
| `DOC_EXCHANGE_DEFAULT_SPACE_ID` | `default` | Default space for FileWatcher |

## Steering File Integration

Each sub-project's IDE agent uses a steering file to auto-check for updates. Generate one with:

```
generate_steering_file(project_name="my-service", project_space_id="<space_id>")
```

See [`doc-exchange-steering-template.md`](doc-exchange-steering-template.md) for the template.

## Running Tests

```bash
python -m pytest tests/ -q
```

## Paper

The accompanying research paper is available in [`paper/agentnexus.md`](paper/agentnexus.md).

> dugubuyan. *AgentNexus: A Service-Boundary-Aware Coordination Architecture for Heterogeneous LLM Code Agents.* 2026.

## License

MIT
