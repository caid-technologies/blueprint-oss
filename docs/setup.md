# Local Setup

Blueprint OSS runs a FastAPI backend and a Next.js frontend. PostgreSQL is supported but optional; the backend will fall back to SQLite for local use.

## Prerequisites
- **Python 3.11+**
- **Node.js 18+**
- **PostgreSQL** (optional, recommended for persistent storage)

## Backend setup (FastAPI)
From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Environment variables
Recommended: create a repo-root `.env` (see `.env.example`).

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/blueprint

# Live LLM generation
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
STRICT_LLM=true

# Optional first-party OpenAI settings
# OPENAI_RESPONSE_FORMAT=json_schema
# OPENAI_VALIDATE_MODELS=false
# OPENAI_PROJECT_ID=your_openai_project_id_here
# OPENAI_ORG_ID=your_openai_org_id_here

# Optional generated product image output
IMAGE_OUTPUT_ENABLED=false
IMAGE_PROVIDER=openai
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_SIZE=1024x1024
# OPENAI_IMAGE_QUALITY=medium
# OPENAI_IMAGE_OUTPUT_FORMAT=png

# Generic provider aliases
# LLM_API_KEY=your_provider_api_key_here
# LLM_MODEL=gpt-4o-mini
# LLM_FALLBACK_MODEL=your_fallback_model_here

# Optional for OpenAI-compatible providers
# LLM_PROVIDER=openai-compatible
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_ALLOW_NO_API_KEY=true

# Optional TCP JSONL A2A socket
JOB_METADATA_DB_PATH=./blueprint_jobs.db
A2A_SOCKET_ENABLED=false
A2A_SOCKET_HOST=127.0.0.1
A2A_SOCKET_PORT=8766
```

Notes:
- If `DATABASE_URL` is missing or the connection fails, the backend falls back to `sqlite:///./blueprint.db`.
- `LLM_PROVIDER` can be `gemini`, `openai`, `openai-compatible`, or `simulation`.
- `OPENAI_API_KEY` enables first-party OpenAI live structured generation when `LLM_PROVIDER=openai`.
- `OPENAI_RESPONSE_FORMAT` defaults to `json_schema` for OpenAI. You can set it to `json_object` for older JSON mode or `none` to omit `response_format`.
- `OPENAI_PROJECT_ID` and `OPENAI_ORG_ID` are optional routing headers for accounts that need explicit project or organization selection.
- `IMAGE_OUTPUT_ENABLED=true` makes generated product concept images the default. Leave it `false` and use the UI checkbox or `generate_image=true` API flag to opt in per job.
- `IMAGE_PROVIDER` can be `openai`, `openai-compatible`, or `none`.
- `OPENAI_IMAGE_MODEL` selects the image model. The example default is `gpt-image-2`.
- `OPENAI_IMAGE_SIZE`, `OPENAI_IMAGE_QUALITY`, and `OPENAI_IMAGE_OUTPUT_FORMAT` tune generated image output.
- `LLM_API_KEY` is a generic provider key alias. Gemini aliases (`GEMINI_API_KEY` or `GOOGLE_API_KEY`) are still supported.
- With `STRICT_LLM=true`, generation fails fast when model availability validation is enabled and `LLM_MODEL` is unavailable.
- With `STRICT_LLM=false`, the backend may fall back to `LLM_FALLBACK_MODEL`.
- OpenAI-compatible endpoints can use `LLM_BASE_URL`; local endpoints that do not require auth can set `LLM_ALLOW_NO_API_KEY=true`.
- A2A job metadata is persisted to SQLite at `JOB_METADATA_DB_PATH`.
- A2A REST, WebSocket, and MCP routes are always mounted. The TCP JSONL socket starts only when `A2A_SOCKET_ENABLED=true`.

### Seed the component database
The server auto-seeds templates on startup if the `component_templates` table is empty.

Optional manual seed:
```bash
python3 backend/seed_db.py
```

### Run the backend
Run from the repo root so `backend.*` imports resolve correctly:

```bash
uvicorn backend.main:app --reload --port 8000
```

OpenAI one-liner:
```bash
LLM_PROVIDER=openai OPENAI_API_KEY=your_openai_api_key_here OPENAI_MODEL=gpt-4o-mini uvicorn backend.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

## Frontend setup (Next.js)
```bash
cd frontend
npm install
npm run dev
```

UI: http://localhost:3000

## Optional: validate a netlist
You can submit a netlist to `POST /api/validate` to test validation rules without running the full pipeline.
