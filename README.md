# Blueprint OSS

Blueprint OSS is an open-source, AI-native hardware design generator. It turns a natural-language idea into a structured, validated hardware project package (Hardware IR) plus schematics, BOM, and build steps.

This repository is an **MVP and research prototype** focused on **low-voltage maker electronics** (3.3V–5V) and safe, educational projects.

## What you can build (MVP)
- IoT sensor nodes and dashboards
- Simple automation controllers (relays, servos, pumps)
- Environmental monitors and data loggers
- Small wearable or tabletop devices
- Learning projects that map pins, nets, and BOMs

## How it works
Prompt → agents → Hardware IR → validation → outputs.

```mermaid
flowchart LR
  A[Prompt + optional image] --> B[Multi-agent workflow\nGoogle ADK + Gemini Flash]
  B --> C[Typed Hardware IR (Pydantic JSON)]
  C --> D[Rule-based validation + repair loop]
  D --> E[Outputs: React Flow schematic, SVG/Mermaid, BOM, assembly steps]
  C --> F[(Project database)]
```

- **Prompts** describe what you want to build.
- **Agents** interpret the intent, choose components, and draft wiring.
- **Hardware IR** is the structured, typed source of truth for everything else.
- **Validation** checks for electrical and safety issues and can trigger repair.
- **Outputs** render in the UI and export as JSON packages.

## MVP scope & safety boundaries
Blueprint intentionally limits scope to low-voltage maker electronics:
- 3.3V–5V DC systems
- Breadboard-friendly microcontrollers, sensors, displays, and actuators
- Educational and hobbyist prototypes

It blocks or warns on high-risk domains (mains AC, medical, automotive, weapons, high-power battery systems). See [docs/validation.md](docs/validation.md) for details.

## Local setup (quick)
Detailed instructions live in [docs/setup.md](docs/setup.md).

### Backend
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Optional: set DATABASE_URL and GEMINI_API_KEY in backend/.env
python3 seed_db.py
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Then open http://localhost:3000 (frontend) and http://localhost:8000/docs (API docs).

## Screenshots (placeholders)
![Blueprint UI placeholder](docs/assets/ui-placeholder.svg)

## Project structure (high level)
```
backend/   FastAPI + agent orchestration + validation
frontend/  Next.js + React Flow UI
docs/      Architecture and contributor docs
examples/  Sample Hardware IR projects
```

## Documentation
- [Architecture](docs/architecture.md)
- [Hardware IR](docs/hardware-ir.md)
- [Agents](docs/agents.md)
- [Validation](docs/validation.md)
- [Database](docs/database.md)
- [Frontend](docs/frontend.md)
- [Backend](docs/backend.md)
- [Setup](docs/setup.md)
- [Development](docs/development.md)
- [Roadmap](docs/roadmap.md)
- [Examples](docs/examples.md)

## Roadmap (summary)
- Expand the component library and validation rules
- Improve explainability, repair feedback, and UI tooling
- Add richer exports (PCB-ready netlists, mechanical assets)

Full roadmap: [docs/roadmap.md](docs/roadmap.md).

## Contributing
Blueprint OSS is research-oriented and welcomes contributors. Start with:
1. Read [docs/development.md](docs/development.md)
2. Open an issue or proposal
3. Send a focused PR with tests or repro steps when applicable

---

If you're new to hardware, this project aims to be a gentle path from idea to buildable prototype.
