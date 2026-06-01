# Blueprint

Blueprint is AI-native hardware design generation. It turns a prompt (and optionally an image) into a structured, validated **Hardware IR** package plus wiring diagrams, BOM, and build steps.

This repository is an **MVP and research prototype** focused on **low-voltage maker electronics** (3.3V–5V) and safe, educational projects.

## What you can do
- Compile a hardware idea into typed **Hardware IR** (Pydantic)
- Run **rule-based electrical validation** (shorts, voltage mismatch, unpowered ICs, pin conflicts, overcurrent risk)
- Visualize wiring with:
  - Interactive **React Flow** schematic
  - Generated **SVG** schematic
- View a lightweight **3D mechanical layout** (Three.js / React Three Fiber)
- Persist generated projects to **Postgres** (default) with an automatic **SQLite fallback**

## How it works

Blueprint follows a sequential processing pipeline:

1. **Input**: User provides a prompt and optional image
2. **Agent Processing**: ADK-style sequential agents process the input using Gemini structured JSON output
3. **Hardware IR Generation**: Agents produce typed Hardware IR (Pydantic models)
4. **Validation & Repair**: Rule-based validation checks the design and repairs issues automatically
5. **UI Outputs**: Generate interactive visualizations (React Flow schematic, SVG diagrams, 3D mechanical layout) and save to database
6. **Persistence**: Project data is stored in PostgreSQL or SQLite

## MVP scope & safety boundaries
Blueprint intentionally limits scope to low-voltage maker electronics:
- 3.3V–5V DC systems
- Breadboard-friendly microcontrollers, sensors, displays, and actuators
- Educational and hobbyist prototypes

It blocks or warns on high-risk domains (mains AC, medical, automotive control, weapons, high-power battery packs). See [docs/validation.md](docs/validation.md).

## Local setup (quick)
Detailed instructions live in [docs/setup.md](docs/setup.md). The short version:

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

Environment variables (recommended via a repo-root `.env`; see `.env.example`):
- `DATABASE_URL`: Database connection string (default: `******localhost:5432/blueprint`). Falls back to `sqlite:///./blueprint.db` if PostgreSQL is unavailable.
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`): Required to enable live hardware generation.
- `GEMINI_MODEL`: Gemini model to use (default: `gemini-3.5-flash`).
- `STRICT_GEMINI`: Set to `true` (default) to fail fast if `GEMINI_MODEL` is unavailable. Set to `false` to attempt fallback.
- `GEMINI_FALLBACK_MODEL`: Fallback model when `STRICT_GEMINI=false` (default: `gemini-2.5-flash`).

If no Gemini key is configured or generation fails, the backend returns deterministic simulation outputs based on built-in example projects.

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
- [Backend](docs/backend.md)
- [Frontend](docs/frontend.md)
- [Setup](docs/setup.md)
- [Development](docs/development.md)
- [Examples](docs/examples.md)
- [Roadmap](docs/roadmap.md)
