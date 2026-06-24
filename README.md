# AgentNexus

**A service-boundary-aware coordination architecture for heterogeneous LLM code agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-281%20passing-brightgreen.svg)](tests/)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.20603176-blue)](https://doi.org/10.5281/zenodo.20603176)
[![agent-nexus MCP server](https://glama.ai/mcp/servers/dugubuyan/agent-nexus/badges/score.svg)](https://glama.ai/mcp/servers/dugubuyan/agent-nexus)
[![Available on CodeGuilds](https://img.shields.io/badge/Available_on-CodeGuilds-6366f1)](https://codeguilds.dev/packages/agent-nexus)
> *"Service boundaries, not agent roles, are the appropriate primitive for coordinating LLM agents in real software development."*

## Overview

Existing multi-agent frameworks (ChatDev, MetaGPT) organize agents around **roles** within a single simulated organization. AgentNexus takes a different approach: it coordinates agents at the **service** granularity, matching how real software systems are actually structured.

Each service registers as a sub-project, publishes versioned Markdown documents (requirements, design, API specs, config), and subscribes to documents from services it depends on. When a document changes, subscribers receive a diff-aware notification containing both the structured diff and the full latest content — enabling targeted, context-aware code modifications.

## Key Features

- **Versioned document store** — SHA-256 dedup, full version history, per-service namespacing
- **Publish-subscribe notifications** — subscribe by exact doc ID or doc type
- **Diff-aware updates** — `get_my_updates_with_context` returns unified diff + full content in one call
- **Lifecycle stage tracking** — explicit `design → development → testing → deployment → upgrade` per service, with milestone snapshots on transitions
- **Service-Driven Agent Onboarding (SDAOP)** — `generate_instruction_file` auto-generates IDE steering files (AGENTS.md, CLAUDE.md, Kiro steering, Cursor rules) for any connecting agent
- **MCP HTTP server** — streamable-HTTP transport, multiple agents connect simultaneously
- **Out-of-band write endpoint** — `POST /api/documents` accepts full content via HTTP body (zero LLM token cost)
- **FTS5 full-text search** — `search_documents` with BM25 ranking, phrase/prefix/boolean query support
- **Planner AI layer** — `planner_chat`, `planner_plan`, `planner_overview` MCP tools + configurable LLM backend
- **Web Dashboard** — browser-based UI to explore spaces, projects, and documents with full-text search
- **AI Chat** — built-in chat panel powered by Planner LLM for conversational document Q&A and service planning
- **281 tests** — unit + property-based (Hypothesis)

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

## How It Works

When a backend service updates its API document, the frontend agent is automatically notified with a structured diff — no human coordination needed:

```
Backend Agent              AgentNexus               Frontend Agent
      │                        │                          │
      │── POST /api/documents ▶│                          │
      │   (api, new version)   │── notification ─────────▶│
      │                        │                          │── get_my_updates_with_context()
      │                        │◀─────────────────────────│
      │                        │── diff + full content ──▶│
      │                        │                          │── apply targeted code changes
      │                        │                          │── ack_update() ────────────▶│
      │                        │                          │
```

The diff payload looks like:

```json
{
  "doc_id": "backend-service/api",
  "new_version": 5,
  "diff": "@@ -42,6 +42,12 @@\n+## PUT /admin/docs/{doc_id}\n+Update a document in-place...",
  "latest_content": "# API Spec\n\n..."
}
```

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Configure environment (copy example and fill in values)
cp .env.example .env

# Initialize database
python -m alembic upgrade head

# Start server (default: http://0.0.0.0:10086/mcp)
python src/main.py
```

### Connect from Kiro / any MCP client

```json
{
  "mcpServers": {
    "agent-nexus": {
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

# Push a document via HTTP POST (content stays out of LLM context)
# curl -X POST http://localhost:10086/api/documents -H 'Content-Type: application/json' \
#   -d '{"project_id":"<project_id>","doc_id":"<project_id>/api","content":"# API Spec..."}'

# Subscribe frontend to backend's API docs
add_subscription(subscriber_project_id="<frontend_id>", project_space_id="<space_id>", target_doc_id="<backend_id>/api")

# Check updates (returns diff + full content)
get_my_updates_with_context(project_id="<frontend_id>")
```

## Web Dashboard

Once the server is running, open **http://localhost:10086/** in your browser.

Features:
- **Browse** — navigate spaces, sub-projects, and documents in a tree view
- **Search** — full-text search across all documents in a space
- **AI Chat** — ask questions about your project documents using natural language

> **LLM configuration:** AI Chat requires `PLANNER_LLM_API_KEY` to be set. Set `PLANNER_LLM_PROVIDER` (`openai` or `anthropic`), `PLANNER_LLM_MODEL`, and optionally `PLANNER_LLM_BASE_URL` (for Azure / Ollama / compatible APIs) as needed. Leave the key unset to disable AI features while keeping all browse/search functionality.

## Out-of-Band Write Endpoint

For zero-token document ingestion (bypasses MCP tool-call LLM context), use the HTTP endpoint directly:

```bash
curl -X POST http://localhost:10086/api/documents \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project_id>",
    "doc_id": "<project_id>/requirement",
    "content": "# Requirements\n\nContent here..."
  }'
```

This is the primary document write path. Document content travels via HTTP body — never entering LLM context — making it practical for documents of any size. Supports optional `base_version` for optimistic concurrency control (fast-forward check).

## MCP Tools

| Tool | Description |
|------|-------------|
| `create_space` | Create a Project Space |
| `register_project` | Register a sub-project (service) |
| `list_projects` | List all sub-projects in a space |
| `list_documents` | List all documents in a sub-project |
| `get_document` | Retrieve a document (latest or specific version) |
| `get_my_updates_with_context` | Get unread notifications with diff + full content |
| `ack_update` | Mark a notification as read |
| `get_my_tasks` | Get pending tasks for a project |
| `get_config` | Get config document for a stage |
| `add_subscription` | Add a subscription rule |
| `publish_draft` | Confirm a draft document |
| `generate_instruction_file` | Generate IDE onboarding file (SDAOP) |
| `get_project_id_by_name` | Look up project_id by name |
| `search_documents` | Full-text search across documents in a space |
| `planner_chat` | Conversational Q&A with LLM over project documents (streaming) |
| `planner_plan` | Generate service-split proposal from a description |
| `planner_overview` | Get a high-level overview of a project space |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `AGENT_NEXUS_DB_URL` | `sqlite:///agent_nexus.db` | Database URL |
| `AGENT_NEXUS_DOCS_ROOT` | `./workspace` | Workspace root (docs live under `{root}/{space_id}/docs/`) |
| `AGENT_NEXUS_HOST` | `0.0.0.0` | Server bind host |
| `AGENT_NEXUS_PORT` | `10086` | Server port |
| `AGENT_NEXUS_DEFAULT_SPACE_ID` | `default` | Default space ID for bootstrap imports |
| `PLANNER_LLM_PROVIDER` | `openai` | LLM provider for Planner AI (`openai` \| `anthropic`) |
| `PLANNER_LLM_MODEL` | (provider default) | LLM model name |
| `PLANNER_LLM_API_KEY` | (none) | API key; leave empty to disable AI features |
| `PLANNER_LLM_BASE_URL` | (none) | Custom API endpoint for OpenAI-compatible APIs (Azure, Ollama, proxies) |

## Steering File Integration

Each sub-project's IDE agent uses an onboarding file (steering file, CLAUDE.md, AGENTS.md, etc.) to auto-check for updates at session start. Generate one with:

```
generate_instruction_file(project_name="my-service", project_space_id="<space_id>", client_type="kiro")
```

Supported `client_type` values: `kiro`, `claude`, `codex`, `cursor`.

This is the **Service-Driven Agent Onboarding Protocol (SDAOP)** — the MCP service generates the onboarding document itself, so agents require zero manual configuration. See the [v3 paper](paper/agentnexus-v3.md) for the formal protocol definition.

## Running Tests

```bash
python -m pytest tests/ -q
```

## Paper

The accompanying research papers are available in the [`paper/`](paper/) directory:

- [`paper/agentnexus-v3.md`](paper/agentnexus-v3.md) — v3 (current): adds SDAOP
- [`paper/agentnexus.md`](paper/agentnexus.md) — v2

> dugubuyan. *AgentNexus: A Service-Boundary-Aware Coordination Architecture for Heterogeneous LLM Code Agents (v3).* Zenodo, 2026. https://doi.org/10.5281/zenodo.20603176

## License

MIT
