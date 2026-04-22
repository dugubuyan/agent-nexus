# Agent Document Context Guide

This guide defines how an AI agent should determine which documents to read,
based on the current **intent** and **project stage**. It is IDE-agnostic and
applies to any agent integrated with doc-exchange.

---

## 1. Document Type Reference

All documents in doc-exchange use standardized `doc_type` and optional `doc_variant` values.
The full identifier is `{doc_type}` or `{doc_type}/{variant}`.

| doc_type | variant | Description | Typical owner |
|----------|---------|-------------|---------------|
| `requirement` | — | Functional and non-functional requirements | development, shared |
| `design` | — | Architecture and technical design | development, shared |
| `api` | `rest` / `graphql` / `grpc` / — | API contracts | development |
| `config` | `dev` / `test` / `prod` (**required**) | Environment configuration | ops, infra |
| `schema` | `db` / `mq` / — | Database or message queue schema | development, shared |
| `runbook` | `deploy` / `rollback` / — | Operational procedures | infra, ops |
| `changelog` | `notes` / `breaking` | Release notes (cumulative) / Breaking changes (per-release) | development |
| `test-plan` | — | Test strategy and cases | testing |
| `task` | — | Work items and implementation plans | development, testing |

> `config` is the only type where a variant is **required**.
> All other types may include an optional variant for further disambiguation.

### changelog variants explained

- `changelog/notes` — cumulative human-readable release notes (append each version, push full content)
- `changelog/breaking` — structured breaking changes for the **current release only** (overwrite each release)

```markdown
# changelog/breaking example (v1.3.0)
## API changes
- `POST /api/v1/chat`: field `session_id` is now required

## Config changes
- New required env var: `FIRECRAWL_API_KEY`

## Schema changes
- Table `memory_cells`: new column `decay_score` (requires migration)
```

---

## 2. Project Type Reference

| type | Role |
|------|------|
| `development` | Application service (backend, frontend) |
| `testing` | QA / test automation service |
| `ops` | Operations tooling (admin, monitoring) |
| `infra` | Infrastructure (deployment, networking, CI/CD) |
| `shared` | Shared libraries or platform services |

---

## 3. Project Stage Reference

| stage | Meaning |
|-------|---------|
| `design` | Requirements and architecture being defined |
| `development` | Active feature development |
| `testing` | Integration and functional testing |
| `deployment` | Deploying to production |
| `upgrade` | Upgrading a running production service |

---

## 4. Intent → Document Mapping

When an agent receives a request, it should first identify the **intent**,
then use the table below to determine which documents to fetch.

### 4.1 By Intent (question-driven)

| Intent | Fetch from | doc_type |
|--------|-----------|----------|
| Architecture / system design question | all `development` + `shared` projects | `design` |
| API contract / interface question | relevant `development` projects | `api` (any variant) |
| Functional requirements question | relevant `development` + `shared` projects | `requirement` |
| Database / queue schema question | relevant projects | `schema` (any variant) |
| Development config question | relevant projects | `config/dev` |
| Test config question | relevant projects | `config/test` |
| Production config question | `infra` + relevant `ops` projects | `config/prod` |
| Deployment question | `infra` project | `config/prod` + `runbook/deploy` |
| Rollback question | `infra` project | `runbook/rollback` + `changelog/breaking` |
| Upgrade / breaking changes question | affected `development` projects | `changelog/breaking` |
| Release history question | relevant projects | `changelog/notes` |
| Task / work item question | relevant projects | `task` |
| Test strategy question | `testing` projects | `test-plan` |

### 4.2 By Stage (stage-driven)

| Stage context | Primary doc_types | Secondary doc_types | Project scope |
|---------------|-------------------|---------------------|---------------|
| `design` | `requirement` | `design` | all `development` + `shared` |
| `development` | `requirement`, `design`, `api` | `task`, `config/dev`, `schema` | relevant `development` projects |
| `testing` | `api`, `task`, `test-plan` | `config/test`, `requirement` | relevant `development` + `testing` projects |
| `deployment` | `config/prod`, `runbook/deploy` | `design` | `infra` + all `ops` projects |
| `upgrade` | `changelog/breaking`, `config/prod` | `runbook/rollback`, `api` | `infra` + affected `development` projects |

---

## 5. Lookup Procedure

When an agent needs to determine what to read, follow these steps:

```
1. Identify intent from user message keywords:
   - "deploy", "production", "rollback", "nginx", "docker" → deployment intent
   - "upgrade", "breaking", "migration", "what changed" → upgrade intent
   - "test", "QA", "integration", "staging" → testing intent
   - "architecture", "design", "how does X work" → design intent
   - "config", "env", "environment variable" → config intent (clarify dev/test/prod)
   - "API", "endpoint", "interface", "contract" → api intent
   - "database", "schema", "table", "migration" → schema intent
   - "requirement", "feature", "what should X do" → requirement intent
   - "release", "version", "changelog" → changelog intent

2. Call list_projects(project_space_id) to get all projects with their type and stage.

3. Filter projects by type based on intent (see Section 4.1).

4. For each relevant project, call list_documents(project_id) and filter by
   the target doc_type(s). Use prefix matching for variants:
   - "api" matches api, api/rest, api/graphql
   - "config/prod" matches exactly config/prod
   - "changelog" matches changelog/notes, changelog/breaking

5. Fetch documents with get_document(doc_id, project_id).

6. If intent is ambiguous, default to:
   - `infra/config/prod` for anything deployment-related
   - `design` from all development projects for anything architecture-related
   - `changelog/breaking` for anything upgrade-related
```

---

## 6. Special Cases

### infra project
The `infra` project is the single source of truth for:
- Overall deployment topology (docker-compose, nginx routing)
- Inter-service directory conventions
- Production environment checklist

Always read `infra/config/prod` first for any deployment or production question,
regardless of which specific service is being discussed.

### config/prod sensitivity
`config/prod` documents may reference secret variable names (not values).
Agents should treat these documents as sensitive context and not echo their
full contents unnecessarily.

### Multiple environments
When a user asks about "config" without specifying an environment, ask for
clarification: dev / test / prod. Do not assume prod by default.
