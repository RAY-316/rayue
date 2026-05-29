# Rayue Agent

This is a first working demo for the Rayue web agent:

- Frontend: Next.js workspace UI.
- Backend: FastAPI + PostgreSQL.
- Sandbox: one E2B sandbox per conversation.
- Agent runtime: `codex app-server --listen stdio://` inside the E2B `rayue-agent-v1` template.
- Model access: OpenAI-compatible Responses API through configurable `MODEL_BASE_URL`, `MODEL_NAME`, and `CODEX_API_KEY`.
- Admin skills: directories under `backend/agent_skills/*` are copied into every sandbox before a turn starts, so all users inherit admin-added skills and bundled resources.

Backend code is organized as a modular monolith. The current boundaries are documented in `docs/architecture-guidelines.md`: `api/`, `core/`, `domain/`, `agent/`, `files/`, `admin/`, and `tools/`. Thin compatibility modules remain at old paths such as `app.agent_runtime` and `app.uploads` so existing scripts keep working.

## Run

Preferred persistent startup:

```bash
docker compose up -d postgres
docker start rayue-convertx || true
pm2 start ecosystem.config.cjs
pm2 save
```

Restart after backend or frontend changes:

```bash
pm2 restart rayue-backend rayue-frontend
```

Create the local ConvertX container once:

```bash
docker run -d \
  --name rayue-convertx \
  --restart unless-stopped \
  -p 127.0.0.1:8072:3000 \
  -e JWT_SECRET=change_me \
  -e HTTP_ALLOWED=true \
  -e ALLOW_UNAUTHENTICATED=true \
  -e AUTO_DELETE_EVERY_N_HOURS=24 \
  -v /home/ubuntu/akool/agent_web/ConvertX/data:/app/data \
  ghcr.io/c4illin/convertx:latest
```

Start Postgres:

```bash
docker compose up -d postgres
```

Start backend:

```bash
cd backend
../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8071
```

Required environment for real E2B mode:

```bash
AGENT_RUN_MODE=e2b
DATABASE_URL=postgresql+asyncpg://rayue_agent:rayue_agent@localhost:55432/rayue_agent
E2B_API_KEY=...
CODEX_API_KEY=...
MODEL_BASE_URL=http://47.90.255.159:18080/v1
MODEL_NAME=gpt-5.5
E2B_TEMPLATE=rayue-agent-v1
SANDBOX_IDLE_TIMEOUT_SECONDS=900
CONVERTX_SERVICE_URL=http://127.0.0.1:8072
CONVERTX_API_BASE_URL=http://<server-host>:8071/api/tools/convertx
CONVERTX_TIMEOUT_SECONDS=900
```

Start frontend:

```bash
cd frontend
BACKEND_INTERNAL_URL=http://127.0.0.1:8071 npm run dev -- --hostname 0.0.0.0 --port 8070
```

Open:

```text
http://localhost:8070
```

## API Smoke Test

```bash
curl -s http://localhost:8071/api/health
```

Create a conversation and send a turn:

```bash
conv_id=$(curl -s -X POST http://localhost:8071/api/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"smoke"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -s -X POST "http://localhost:8071/api/conversations/$conv_id/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content":"只回复 live-ok。"}'
```

Then inspect:

```bash
curl -s "http://localhost:8071/api/conversations/$conv_id" | python3 -m json.tool
```

Expected event path:

```text
sandbox_starting -> sandbox_started -> skills_synced -> app_server_started
-> app_server_initialized -> thread_started -> turn_started -> assistant_delta
-> assistant_message -> turn_completed
```

The frontend does not expose this raw event stream to users. It converts useful events into a compact inline process card in the chat timeline, while final assistant messages remain separate chat bubbles.

## Admin Skills

For MVP, admin skills are shared directories:

```text
backend/agent_skills/<skill-name>/
```

The backend copies all valid skill folders into:

```text
$CODEX_HOME/skills/<skill-name>/
```

inside each E2B sandbox before starting a thread/turn. This makes admin-added skills available to all users without per-user setup.

The Anthropic public skills from `https://github.com/anthropics/skills/tree/main/skills` were installed from commit `690f15cac7f7b4c055c5ab109c79ed9259934081`.
The local `gpt-image-2.zip` skill was installed as `backend/agent_skills/gpt-image-2/`.
The local file conversion skill is installed as `backend/agent_skills/convertx-file-converter/` and calls the Rayue backend proxy at `/api/tools/convertx`.

To speed up sandbox startup, the backend builds a compressed skill bundle under:

```text
storage/skill-bundles/
```

New sandboxes receive one archive and extract it into `$CODEX_HOME/skills/`. The `rayue-agent-v1` template is pre-baked with the current admin skills and a matching `.rayue-agent-skills.sha256` marker, so startup skips the upload until skills change.

## Artifacts

Final files created under `/home/user/workspace/outputs/` are copied to local storage when a turn completes:

```text
storage/artifacts/<conversation_id>/
```

When S3/R2 settings are configured, final outputs are also mirrored to object storage. New sandboxes restore `outputs/` through short-lived signed object-storage downloads instead of streaming the files through the backend. The frontend Artifacts panel lists these persisted final files and downloads from local storage first. Intermediate files under `work/`, `tmp/`, extracted Office packages, renders, logs, and thumbnails are treated as transient workspace state and are not persisted as user-facing artifacts. Final output files up to 1 GB are allowed.

API:

```text
GET /api/conversations/{conversation_id}/artifacts
GET /api/conversations/{conversation_id}/artifacts/download?path=<workspace-relative-path>
```

Only files from `/home/user/workspace/outputs/` are synced/exposed as artifacts. Uploaded inputs are stored separately. `storage/` is gitignored.

Uploaded user input files are stored separately:

```text
storage/uploads/<conversation_id>/
```

The backend syncs them into each sandbox at:

```text
/home/user/workspace/inputs/
```

Those files are listed through `GET /api/conversations/{conversation_id}/uploads` and uploaded through `POST /api/conversations/{conversation_id}/uploads`. The upload endpoint accepts at most 10 files per request and rejects any single file above 150 MB. Files under `inputs/` are treated as input/reference material and are excluded from the downloadable Artifacts panel. When S3/R2 is configured, uploaded inputs are mirrored to object storage and new sandboxes download them directly from signed URLs.

Object storage uses the S3-compatible environment names `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`. `RAYUE_S3_*` and `XPET_S3_*` aliases also work.

## Current Limits

- No auth yet; this is an internal-test demo.
- Conversation data is persisted in PostgreSQL; generated workspace files are persisted locally under `storage/artifacts/`; uploaded input files are persisted locally under `storage/uploads/`; configured S3/R2 storage is used as the sandbox restore path for inputs and final outputs.
- One active in-memory worker is kept per conversation while the backend is running.
- Idle workers are closed after `SANDBOX_IDLE_TIMEOUT_SECONDS`, default `900`.
- GitHub integration is intentionally not connected yet.
