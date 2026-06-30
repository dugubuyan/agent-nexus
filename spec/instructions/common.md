## Service Identity

- project_name: `{{PROJECT_NAME}}`
- project_space_id: `{{PROJECT_SPACE_ID}}`
- MCP endpoint: `{{SERVER_URL}}/mcp`

## Initialization Workflow

At the start of every session:

0. Call `get_sdaop_version(client_type="<your client>")` and compare against
   the `_sdaop_version` field in your local `.kiro/nexus-state.json`. If they
   differ, the service-side onboarding protocol has been updated — call
   `generate_instruction_file` again to regenerate your steering file and
   refresh `nexus_push.py` (write `push_script.content` to the target file).
   Then continue with the steps below.
1. Call `get_project_id_by_name(name="{{PROJECT_NAME}}", project_space_id="{{PROJECT_SPACE_ID}}")` to resolve your project_id.
2. Call `get_my_updates_with_context(project_id=<project_id>)` to check for pending document changes.
3. Call `get_document_checklist(project_id=<project_id>)` to see which documents are missing for your project.

For step 2, each update contains:
- `update_id`: acknowledge with `ack_update` after processing
- `diff`: unified diff showing what changed (+ added, - removed)
- `latest_content`: full current document content

If updates exist: apply changes based on `diff` and `latest_content`, then call `ack_update(project_id, update_id)`.

For step 3, if `all_required_present` is false, create the missing documents listed in `required_docs`
before proceeding with other work. Use `suggested_doc_id` as the doc_id when pushing via HTTP POST.

## Document Convention

doc_id format: `{project_id}/{doc_type}` or `{project_id}/{doc_type}/{variant}`

### Supported doc_types

| doc_type | variant | Description |
|----------|---------|-------------|
| `requirement` | — | Functional and non-functional requirements |
| `design` | — | Architecture and technical design |
| `api` | `rest` / `graphql` / `grpc` | API contracts |
| `config` | `dev` / `test` / `prod` (**required**) | Environment configuration |
| `schema` | `db` / `mq` | Database or message queue schema |
| `runbook` | `deploy` / `rollback` | Operational procedures |
| `changelog` | `notes` / `breaking` | Release notes / Breaking changes |
| `test-plan` | — | Test strategy and cases |
| `task` | — | Work items and implementation plans |
| `task/checklist` | — | Custom document checklist for this project |

Examples:
- `{{PROJECT_ID}}/requirement`
- `{{PROJECT_ID}}/api`
- `{{PROJECT_ID}}/design`
- `{{PROJECT_ID}}/config/dev`

## Version State File (.kiro/nexus-state.json)

This file is your local version anchor — the equivalent of `.git/refs`.
It maps each doc_id to the server version your local file is based on.

At session start, read this file alongside `get_my_updates_with_context`:
- Server version higher than your `local_version` → apply the diff, update local file and `local_version`
- Versions match → local file is in sync
- File doesn't exist yet → treat all docs as new after first push

The file also stores a reserved key `_sdaop_version` (string) that records
the SDAOP protocol version your steering file and `nexus_push.py` were
generated from. Compare it against `get_sdaop_version()` at session start
(step 0 above) to detect when the service-side protocol has changed.

## Setup: Get the Push Script

The push script is delivered as part of `generate_instruction_file` — no separate download needed.

When you call `generate_instruction_file`, the response includes a `push_script` field:
- Write `push_script.content` to `{{PUSH_SCRIPT_PATH}}` in your workspace
- Replace `{{PROJECT_ID}}` in that file with your actual project_id (from step 1 above)

If the script file already exists and `_sdaop_version` in `.kiro/nexus-state.json` matches
the current version, no action needed — the existing script is up to date.

**HTTP fallback** (if you need to refresh the script independently):
```
curl -o {{PUSH_SCRIPT_PATH}} {{SERVER_URL}}/api/templates/push-tool.py
```

## Update Handling

**What you need to do:** write your document as a local file, POST it to AgentNexus,
then record the returned version in your local nexus-state file. The order matters:
write first, then push — just like git: write code, then commit.

**Recommended way** (using the push script — handles HTTP POST and nexus-state automatically):

1. Write or update your document as a local Markdown file
2. Run: `python {{PUSH_SCRIPT_PATH}} <doc_type> <path/to/file.md>`
3. nexus-state is updated automatically

**If the script is not available**, do the same steps manually:

1. Write your document as a local Markdown file
2. POST the file content to AgentNexus:
   `POST {{SERVER_URL}}/api/documents`
   body: `{"project_id": "<pid>", "doc_id": "<pid>/<doc_type>", "content": "<file content>"}`
3. Record the returned `version` in your nexus-state file:
   `{"<pid>/<doc_type>": {"local_version": <version>, "local_file_hint": "<doc_type>"}}`

## Custom Checklist

Declare what YOUR project needs by pushing a checklist document early:

  doc_id  = `{{PROJECT_ID}}/task/checklist`
  content = Markdown with `## Required` and `## Recommended` sections,
            each containing `- doc_type: description` list items.

`get_document_checklist` will use your custom checklist instead of the built-in fallback.

## Cross-Service Document Ownership

Deployment, ops/runbook, and global ADR documents belong to a coordination unit,
NOT to a single business service:

1. Call `list_projects(project_space_id)` to check if a coordination unit exists (e.g. `platform`, `infra`, `ops`).
2. If it exists → push the document under THAT project_id.
3. If not → `register_project(name="platform", type="infra", ...)`, then push under the new project_id.
4. Do NOT place cross-service documents under a business service's own project_id.
