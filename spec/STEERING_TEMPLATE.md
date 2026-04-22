# AgentNexus Integration Guide

This project is integrated with AgentNexus, a service-boundary-aware coordination center for LLM code agents.

## Project Info

- `project_name`: `{YOUR_PROJECT_NAME}`
- `project_space_id`: `{YOUR_SPACE_ID}`
- MCP endpoint: `{YOUR_MCP_ENDPOINT}`

## Workflow

### At the start of each session

1. Call `get_project_id_by_name(name="{YOUR_PROJECT_NAME}", project_space_id="{YOUR_SPACE_ID}")` to resolve your `project_id`.
2. Call `get_my_updates_with_context(project_id=<result from step 1>)` to check for pending document updates.

Each update item contains:
- `update_id` — use with `ack_update` when done
- `doc_type` — type of document that changed (requirement / design / api / config / schema / runbook / changelog / test-plan / task)
- `new_version` — the new version number
- `diff` — unified diff showing what changed (`+` added, `-` removed)
- `latest_content` — full current document content

**Handling updates:**
- If updates exist: use `diff` to locate affected code, use `latest_content` for full context, make the necessary changes, then call `ack_update(project_id, update_id)` to mark as read.
- If no updates: proceed with normal work.

### After significant code or document changes

Resolve your `project_id` first (via `get_project_id_by_name`), then call `push_document` to publish your latest documents to AgentNexus so dependent services are notified.

`doc_id` format: `{project_id}/{doc_type}` or `{project_id}/{doc_type}/{variant}`, e.g.:
- `{project_id}/requirement`
- `{project_id}/api`
- `{project_id}/design`
- `{project_id}/config/dev`   ← config requires a variant: dev / test / prod
- `{project_id}/config/prod`
- `{project_id}/changelog/notes`
- `{project_id}/changelog/breaking`

See `AGENT_GUIDE.md` for the full list of supported doc_types and variants.
