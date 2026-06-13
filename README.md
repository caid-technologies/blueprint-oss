# Blueprint

Blueprint is AI-native hardware design generation. It turns a prompt (and optionally an image) into a structured, validated **Hardware IR** package plus generated product imagery, wiring diagrams, BOM, and build steps.

This repository is an **MVP and research prototype** focused on **low-voltage maker electronics** (3.3V–5V) and safe, educational projects.

## What you can do
- Compile a hardware idea into typed **Hardware IR** (Pydantic)
- Run **rule-based electrical validation** (shorts, voltage mismatch, unpowered ICs, pin conflicts, overcurrent risk)
- Visualize wiring with:
  - Interactive **React Flow** schematic
  - Generated **SVG** schematic
- View a lightweight **3D mechanical layout** (Three.js / React Three Fiber)
- Generate an optional **product concept image** with an image model
- Persist generated projects to **Postgres** (default) with an automatic **SQLite fallback**
- Let external agents integrate over **REST long-polling, WebSocket, optional TCP JSONL sockets, or MCP-style JSON-RPC tools**

## How it works

Blueprint follows a sequential processing pipeline:

1. **Input**: User provides a prompt and optional image
2. **Agent Processing**: ADK-style sequential agents process the input using the configured structured LLM provider
3. **Hardware IR Generation**: Agents produce typed Hardware IR (Pydantic models)
4. **Validation & Repair**: Rule-based validation checks the design and repairs issues automatically
5. **UI Outputs**: Generate interactive visualizations (product image, React Flow schematic, SVG diagrams, 3D mechanical layout) and save to database
6. **Persistence**: Project data is stored in PostgreSQL or SQLite

## MVP scope & safety boundaries
Blueprint intentionally limits scope to low-voltage maker electronics:
- 3.3V–5V DC systems
- Breadboard-friendly microcontrollers, sensors, displays, and actuators
- Educational and hobbyist prototypes

It blocks or warns on high-risk domains (mains AC, medical, automotive control, weapons, high-power battery packs). See [docs/validation.md](docs/validation.md).

## Local setup (quick)
Detailed instructions live in [docs/setup.md](docs/setup.md). The short version:

### Run Everything
From the repo root:

```bash
./scripts/dev.sh
```

This starts the FastAPI backend and Next.js frontend together. Use `BACKEND_PORT`, `FRONTEND_PORT`, `BACKEND_HOST`, or `FRONTEND_HOST` to override defaults.

### Backend (FastAPI)
From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

**Optional: seed the component library**
The server auto-seeds the component library on startup if empty. To seed manually:

```bash
python3 backend/seed_db.py
```

**Run the backend:**
```bash
uvicorn backend.main:app --reload --port 8000
```

**Backend CLI:**
```bash
./scripts/blueprint-backend serve --reload
./scripts/blueprint-backend health
./scripts/blueprint-backend jobs --status running
./scripts/blueprint-backend jobs --local --limit 10
./scripts/blueprint-backend seed
```

The CLI uses `.venv/bin/python` when present and falls back to `python3`. `health`
checks the root, component, and A2A jobs endpoints; `jobs --local` reads the
SQLite job metadata store directly when the API server is not running.

To run with OpenAI:
```bash
LLM_PROVIDER=openai OPENAI_API_KEY=your_openai_api_key_here OPENAI_MODEL=gpt-4o-mini uvicorn backend.main:app --reload --port 8000
```

Environment variables (recommended via a repo-root `.env`; see `.env.example`):
- `DATABASE_URL`: Database connection string (default: `******localhost:5432/blueprint`). Falls back to `sqlite:///./blueprint.db` if PostgreSQL is unavailable.
- `LLM_PROVIDER`: Live generation provider: `gemini`, `openai`, `openai-compatible`, or `simulation`.
- `OPENAI_API_KEY`: API key for first-party OpenAI when `LLM_PROVIDER=openai`.
- `OPENAI_MODEL`: OpenAI model ID. The example default is `gpt-4o-mini`.
- `OPENAI_RESPONSE_FORMAT`: OpenAI response format. Defaults to `json_schema`; `json_object` and `none` are also supported.
- `OPENAI_PROJECT_ID` / `OPENAI_ORG_ID`: Optional OpenAI project and organization routing headers.
- `IMAGE_OUTPUT_ENABLED`: Optional global default for generated product images. The UI and API can opt in per job with `generate_image=true`.
- `IMAGE_PROVIDER`: Image provider. Supports `openai`, `openai-compatible`, or `none`.
- `OPENAI_IMAGE_MODEL`: OpenAI image model ID. The example default is `gpt-image-2`.
- `OPENAI_IMAGE_SIZE`: Generated image size, for example `1024x1024`.
- `LLM_API_KEY`: Generic provider API key alias. For Gemini, `GEMINI_API_KEY` or `GOOGLE_API_KEY` still work.
- `LLM_MODEL`: Model to use, for example `gemini-3.5-flash` or an OpenAI/OpenAI-compatible model ID.
- `STRICT_LLM`: Set to `true` (default) to fail fast when model validation is enabled and the model is unavailable. Set to `false` to attempt fallback.
- `LLM_FALLBACK_MODEL`: Optional fallback model when `STRICT_LLM=false`.
- `LLM_BASE_URL`: Optional base URL for OpenAI-compatible providers.
- `JOB_METADATA_DB_PATH`: SQLite file used for durable A2A job metadata (default: `./blueprint_jobs.db`).
- `A2A_SOCKET_ENABLED`: Set to `true` to start the optional TCP JSONL A2A socket.
- `A2A_SOCKET_HOST` / `A2A_SOCKET_PORT`: Host and port for the optional TCP JSONL listener.

If no live LLM provider is configured or generation fails, the backend returns deterministic simulation outputs based on built-in example projects.

### Frontend (Next.js)
```bash
cd frontend
npm install
npm run dev
```

Open:
- http://localhost:3000 (UI)
- http://localhost:8000/docs (API docs)

Tip: load an example directly with http://localhost:3000/?example=pocket_mp3_player (or any JSON under `frontend/public/examples/`).

## Documentation
- [Architecture](docs/architecture.md)
- [Agents](docs/agents.md)
- [Hardware IR](docs/hardware-ir.md)
- [Validation](docs/validation.md)
- [Database](docs/database.md)
- [A2A](docs/a2a.md)
- [Backend](docs/backend.md)
- [Frontend](docs/frontend.md)
- [Setup](docs/setup.md)
- [Development](docs/development.md)
- [Examples](docs/examples.md)
- [Roadmap](docs/roadmap.md)
