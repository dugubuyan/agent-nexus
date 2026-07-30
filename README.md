# AgentNexus

**The coordination layer for heterogeneous LLM code agents.**
Let a backend agent, a frontend agent, and an infra agent — running on *different* IDEs and *different* models — stay in sync automatically, with zero hand-written glue.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-337%20passing-brightgreen.svg)](tests/)
[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.21257426-blue)](https://doi.org/10.5281/zenodo.21257426)
[![agent-nexus MCP server](https://glama.ai/mcp/servers/dugubuyan/agent-nexus/badges/score.svg)](https://glama.ai/mcp/servers/dugubuyan/agent-nexus)
[![Available on CodeGuilds](https://img.shields.io/badge/Available_on-CodeGuilds-6366f1)](https://codeguilds.dev/packages/agent-nexus)

> An MCP server that coordinates AI code agents across service boundaries.
> *(Not affiliated with the MIT Lincoln Laboratory project of the same name.)*

---

## The problem

Your product isn't one codebase. It's a backend, a frontend, some infra, a test suite — each with its own repo, stack, and agent. When the backend changes its API, the frontend has to adapt. Today that coordination is done by humans copy-pasting specs into chat, or by hand-written `CLAUDE.md` / `AGENTS.md` files that go stale the moment the service changes.

Role-playing multi-agent frameworks (ChatDev, MetaGPT) don't help here: they assume all agents live in one simulated org, one codebase. Real systems are a mesh of independently-owned parts.

## What AgentNexus does

AgentNexus coordinates agents at the **service boundary** — the unit real systems are actually built from. Each boundary registers as a sub-project, publishes versioned Markdown documents (requirements, design, API specs, config), and subscribes to the documents it depends on. When a document changes, subscribers get a **diff-aware notification** — the exact change plus the full latest content — so an agent can make a targeted edit without a human in the loop.

```
Backend Agent              AgentNexus               Frontend Agent
  (Claude Code)                                        (Cursor)
      │                        │                          │
      │── POST /api/documents ▶│                          │
      │   (api spec, v5)       │── notification ─────────▶│
      │                        │                          │── get_my_updates_with_context()
      │                        │── diff + full content ──▶│
      │                        │                          │── applies targeted code change
      │                        │◀──────── ack_update() ───│
```

Two ideas make this practical, and they're the parts worth stealing even if you never run the server:

### 1. Content travels out-of-band → zero token cost

Coordination signals (what changed, who must react) are small and belong in the model's context. Document bodies are large and don't need to be *reasoned about* the moment they're written — they need to be stored and fetched on demand. So AgentNexus splits the two:

- **Control plane (MCP):** notifications, subscriptions, queries — a notification carries a version number and a unified diff, enough to decide *if* and *how* to react.
- **Data plane (plain HTTP `POST /api/documents`):** full document body, never passed as an MCP tool argument.

A document of any size costs **zero model tokens** on the write path. You pay tokens for *coordination*, not for *content*.

### 2. SDAOP — the service onboards the agent, not the other way around

`AGENTS.md`, `CLAUDE.md`, Cursor rules, Kiro steering — they all share one model: a human writes a static file, commits it, and the IDE loads it at startup. It goes stale, it drifts, and it's per-human busywork.

**Service-Driven Agent Onboarding Protocol (SDAOP)** flips this. The *service* generates and delivers client-specific onboarding at connection time. A new agent only needs the endpoint:

```
generate_instruction_file(project_name="my-service", project_space_id="<space_id>", client_type="kiro")
```

- Emits the right artifact for the client — `.kiro/steering/`, `CLAUDE.md`, `AGENTS.md`, or `.cursor/rules/` — plus a push-tool script with the server URL baked in.
- Each artifact is **content-hash versioned**. Change a convention on the server, or move the server to a new URL, and the version bumps — connected workspaces detect the drift and re-onboard. No stale files, no manual notification.
- The service is the single source of truth; the client-side file is a derived artifact that regenerates as the service evolves.

Supported clients: `kiro`, `claude`, `codex`, `cursor`.

## Quick Start

```bash
# 1. Install
pip install -e .

# 2. Start the server — auto-creates the database on first run
#    (default: http://0.0.0.0:10086/mcp, dashboard at http://0.0.0.0:10086/)
python -m agent_nexus.main
```

That's it — no separate DB migration or `.env` needed to get started. Everything
has sane defaults; copy `.env.example` to `.env` only when you want to change the
port, point at Postgres, or enable the Planner LLM.

### Run with Docker

```bash
docker build -t agent-nexus .
docker run -p 10086:10086 agent-nexus
```

To persist documents and the database across restarts, mount volumes:

```bash
docker run -p 10086:10086 \
  -v "$(pwd)/workspace:/app/workspace" \
  -v "$(pwd)/data:/app/data" \
  -e AGENT_NEXUS_DB_URL=sqlite:////app/data/agent_nexus.db \
  agent-nexus
```

Connect from Kiro / any MCP client:

```json
{
  "mcpServers": {
    "agent-nexus": {
      "url": "http://localhost:10086/mcp"
    }
  }
}
```

First steps:

```
# Create a project space
create_space(name="my-project")

# Register two boundaries
register_project(name="backend-api", type="development", project_space_id="<space_id>")
register_project(name="frontend",    type="development", project_space_id="<space_id>")

# Push a document via HTTP POST (content stays out of LLM context)
# curl -X POST http://localhost:10086/api/documents -H 'Content-Type: application/json' \
#   -d '{"project_id":"<backend_id>","doc_id":"<backend_id>/api","content":"# API Spec..."}'

# Subscribe the frontend to the backend's API docs
add_subscription(subscriber_project_id="<frontend_id>", project_space_id="<space_id>", target_doc_id="<backend_id>/api")

# Frontend checks for updates (returns diff + full content)
get_my_updates_with_context(project_id="<frontend_id>")
```

## Web Dashboard

Once the server is running, open **http://localhost:10086/** in your browser to browse spaces, sub-projects, and documents, run full-text search, and use the built-in **AI Chat** for conversational document Q&A and service planning.

> **LLM configuration:** AI Chat requires `PLANNER_LLM_API_KEY`. Set `PLANNER_LLM_PROVIDER` (`openai` or `anthropic`), `PLANNER_LLM_MODEL`, and optionally `PLANNER_LLM_BASE_URL` (Azure / Ollama / compatible APIs). Leave the key unset to disable AI features while keeping all browse/search functionality.

## Key Features

- **Versioned document store** — SHA-256 dedup, full version history, per-boundary namespacing
- **Publish-subscribe notifications** — subscribe by exact doc ID or doc type
- **Diff-aware updates** — `get_my_updates_with_context` returns unified diff + full content in one call
- **Control/data plane split** — coordination over MCP, content over out-of-band HTTP (zero LLM token cost)
- **SDAOP** — services auto-generate versioned, client-specific onboarding files for any connecting agent
- **Planner** — a read-only, boundary-spanning observer (`planner_chat`, `planner_plan`, `planner_overview`) that answers cross-boundary questions no single agent can
- **MCP HTTP server** — streamable-HTTP transport, multiple agents connect simultaneously
- **FTS5 full-text search** — `search_documents` with BM25 ranking, phrase/prefix/boolean queries
- **Web Dashboard + AI Chat** — browser UI over spaces, projects, and documents
- **337 tests** — unit + property-based (Hypothesis)

## Out-of-Band Write Endpoint

The primary document write path. Content travels via HTTP body — never entering LLM context — so it's practical for documents of any size. Supports optional `base_version` for optimistic concurrency control (fast-forward check).

```bash
curl -X POST http://localhost:10086/api/documents \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<project_id>",
    "doc_id": "<project_id>/requirement",
    "content": "# Requirements\n\nContent here..."
  }'
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `create_space` | Create a Project Space |
| `register_project` | Register a sub-project (boundary) |
| `list_projects` | List all sub-projects in a space |
| `list_documents` | List all documents in a sub-project |
| `get_document` | Retrieve a document (latest or specific version) |
| `get_my_updates_with_context` | Get unread notifications with diff + full content |
| `ack_update` | Mark a notification as read |
| `get_my_tasks` | Get pending tasks for a project |
| `get_config` | Get config document for a stage |
| `add_subscription` | Add a subscription rule |
| `publish_draft` | Confirm a draft document |
| `generate_instruction_file` | Generate client-specific onboarding file (SDAOP) |
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
| `AGENT_NEXUS_PUBLIC_URL` | (derived from host/port) | Outward-facing URL baked into onboarding files; changing it bumps the SDAOP version |
| `AGENT_NEXUS_DEFAULT_SPACE_ID` | `default` | Default space ID for bootstrap imports |
| `PLANNER_LLM_PROVIDER` | `openai` | LLM provider for Planner AI (`openai` \| `anthropic`) |
| `PLANNER_LLM_MODEL` | (provider default) | LLM model name |
| `PLANNER_LLM_API_KEY` | (none) | API key; leave empty to disable AI features |
| `PLANNER_LLM_BASE_URL` | (none) | Custom API endpoint for OpenAI-compatible APIs (Azure, Ollama, proxies) |

## Running Tests

```bash
python -m pytest tests/ -q
```

## Paper

The accompanying research papers are in the [`paper/`](paper/) directory:

- [`paper/agentnexus-v4.md`](paper/agentnexus-v4.md) — v4 (current): generalizes the coordination unit from *service* to *ownership boundary*, adds the control/data plane split and the Planner ([中文版](paper/agentnexus-v4-ch.md))
- [`paper/agentnexus-v3.md`](paper/agentnexus-v3.md) — v3: introduces SDAOP
- [`paper/agentnexus.md`](paper/agentnexus.md) — v2

> dugubuyan. *AgentNexus: A Boundary-Aware Coordination Architecture for Heterogeneous LLM Code Agents (v4).* Zenodo, 2026. https://doi.org/10.5281/zenodo.21257426

## License

MIT
