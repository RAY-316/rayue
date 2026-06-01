# Rayue Architecture Guidelines

This document is the short-lived source of direction for larger structural work. Keep it concise; update it when the architecture changes, but do not turn it into a changelog.

## Shape

Rayue should evolve as a modular monolith first. Keep one deployable product while making module boundaries explicit enough that storage, billing, auth, or workers can be split later without rewriting the agent runtime.

## Backend Boundaries

```text
app/
  api/          HTTP routes and request/response orchestration
  core/         config, database, event bus, process-level infrastructure
  domain/       shared domain models and API schemas
  agent/        sandbox, app-server, turn execution, recovery
  files/        uploads, artifact storage adapters, file bundling
  admin/        admin-controlled skills and future admin-only features
  tools/        internal tool proxies such as ConvertX
```

Dependency direction should stay simple:

```text
api -> domain/core/module services
agent -> files/admin/core/domain
files -> core/domain
admin -> core/domain
tools -> core
```

Avoid making `agent` depend on auth, billing, or frontend concerns. Those modules should call the agent through explicit service methods.

## Product Modules

Future all-in-one features should be added as modules with clear ownership:

- `auth`: users, organizations, sessions, roles, permissions.
- `billing`: credit balance, reservations, usage ledger, refunds.
- `files`: R2/object storage, local cache, signed downloads, retention.
- `conversations`: conversations, messages, turns, process events.
- `agent`: execution only: sandbox, stop, recovery, artifact sync.

Credits must be ledger-based. A turn should reserve credits before execution and settle or refund by `turn_id`; do not directly decrement a balance from the agent runtime.

Files should use database metadata as the source of truth, local disk as hot cache, and R2/S3-compatible object storage as durable storage. Workspace files are indexed by `workspace_files`; artifacts still record turn/conversation provenance in `artifact_records`.

Sandbox file sharing mounts a workspace-scoped object storage prefix into the sandbox through rclone FUSE and exposes only `inputs/` and `outputs/` inside the Codex cwd. Do not mount over the whole sandbox workspace because the template keeps runtime dependencies such as `node_modules` there. Keep the archive/object-download sync path as compatibility fallback when the mount is disabled or unavailable.

When users reference files with `@`, the frontend must send structured file references, not only text. The backend should validate those references against `workspace_files` and pass exact paths to the agent.

## Frontend Boundaries

Keep the chat workspace as the product entry, but move future screens into feature folders:

```text
features/chat
features/uploads
features/artifacts
features/billing
features/auth
features/admin
```

Shared UI should not know about sandbox ids, model names, provider names, or other backend implementation details.

## Rules For New Work

- Preserve existing API behavior unless a migration plan is explicit.
- Put new cross-cutting state in Postgres first; in-memory maps are only caches.
- Add schema migrations before adding user, billing, or storage ownership fields.
- Keep generated and uploaded files out of git and behind the backend API.
- Do not expand this document with implementation history; keep decisions here.
