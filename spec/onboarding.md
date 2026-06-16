# AgentNexus Onboarding

You are connecting to **AgentNexus** — a document exchange center for coordinating
LLM code agents across service boundaries.

## Steps to complete before any other work

### Step 1 — Register your project

Call `register_project` to create your sub-project entry:

```
register_project(
    name="<your-service-name>",       # short snake_case, e.g. "auth-service"
    type="development",               # development | testing | ops | infra | shared
    project_space_id="<space_id>",    # get from planner_overview() if unknown
    stage="design"
)
```

This returns your `project_id` (a UUID). Save it.

### Step 2 — Create your local instruction file

Use `generate_instruction_file` MCP tool with your project details and client type.
It returns a `file_path` and `file_content` — write the content to that path immediately.

Alternatively, use the template matching your client from the MCP resources list:

| Client | Resource URI | Target file |
|--------|-------------|-------------|
| Kiro   | `agent-nexus://templates/steering/kiro`   | `.kiro/steering/agent-nexus.md` |
| Claude | `agent-nexus://templates/steering/claude` | `CLAUDE.md` |
| Codex  | `agent-nexus://templates/steering/codex`  | `AGENTS.md` |
| Cursor | `agent-nexus://templates/steering/cursor` | `.cursor/rules/agent-nexus.mdc` |
| Other  | `agent-nexus://templates/steering/default`| `AGENT_NEXUS.md` |

Replace every `{{PLACEHOLDER}}` with your actual values, then write the file.
**This file will be auto-loaded in future sessions so you won't need to re-onboard.**

### Step 3 — Download the push script (one-time setup per workspace)

Download nexus_push.py — a workspace-level tool for pushing documents via HTTP.
Save it to a location appropriate for your client (e.g. `.kiro/nexus_push.py` for Kiro):

```
curl -o <push_script_path> http://localhost:10086/api/templates/push-tool.py
```

Then open the script and replace `{{PROJECT_ID}}` with your project_id from Step 1.

### Step 4 — Write your documents, then push them

The correct order is: **write first, then push** — just like git: write code, then commit.

**4a.** Write your project documents as local Markdown files first (you decide which ones):
```
# example locations — choose any path that suits your project
docs/requirement.md
docs/design.md
```

**4b.** Run the script to push each document:
```
python <push_script_path> requirement docs/requirement.md
python <push_script_path> design docs/design.md
# push any other doc types you have: api, config/dev, task, etc.
```

The script reads the file content, POSTs it to `http://localhost:10086/api/documents`,
and updates your local nexus-state file automatically.

**Do NOT hardcode document content inside the script or pass it as a string.**
Always write the document as a file first, then push the file.

## doc_id rule

`doc_id` MUST be prefixed with your `project_id`:
- `<project_id>/requirement`
- `<project_id>/design`
- `<project_id>/api`
- `<project_id>/config/dev`

Never use a bare doc type without the project_id prefix.
