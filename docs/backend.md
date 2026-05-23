# Backend

The backend is a **FastAPI** service that orchestrates agents, validates netlists, renders diagrams, and stores generated projects.

## Key modules
- `backend/main.py` – FastAPI app and API routes
- `backend/agents/orchestrator.py` – multi-agent pipeline
- `backend/validation.py` – rule-based electrical checks
- `backend/models.py` – Pydantic IR schemas
- `backend/database.py` – SQLAlchemy models + DB setup
- `backend/seed_db.py` – seed component templates
- `backend/utils.py` – Mermaid and SVG schematic generation

## API endpoints
- `POST /api/generate` – run the pipeline and return IR + diagrams
- `POST /api/validate` – validate a user-supplied netlist
- `GET /api/components` – list component templates
- `GET /api/projects` – list generated projects
- `GET /api/projects/{project_id}` – fetch a stored project
- `POST /api/seed` – re-seed the component database

## Orchestration layer
The orchestrator runs a **7-step pipeline** with Google ADK. When a Gemini API key is configured, the agents generate structured JSON that maps directly to the Hardware IR. If no key is set, the system falls back to deterministic example projects for a reliable local demo.

## Validation
Validation is run after the netlist step. Critical issues trigger a repair loop that re-invokes the wiring agent before finalizing the IR.
