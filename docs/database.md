# Database

Blueprint stores component templates and generated projects in a relational database. PostgreSQL is the default, with a **SQLite fallback** for local development.

The connection string is configured via `DATABASE_URL`. If the Postgres connection fails at startup, the backend falls back to `sqlite:///./blueprint.db` for out-of-the-box local reliability.

## Storage model
Database models are defined in `backend/database.py`:

### component_templates
Seed component library used by the Component Selection Agent.
- `part_number` (unique)
- `name`
- `category`
- `description`
- `price`
- `sourcing_url`
- `pins` (JSON list of `PinDefinition`)
- `use_cases` (JSON list of strings)

### generated_projects
Archived outputs from the pipeline.
- `project_id` (unique)
- `title`
- `prompt`
- `hardware_ir` (JSON representation of the IR)
- `created_at`

### a2a_jobs
A2A job metadata is stored separately with the Python stdlib `sqlite3` module, regardless of whether `DATABASE_URL` points at PostgreSQL or SQLite.
- Default path: `./blueprint_jobs.db`
- Override: `JOB_METADATA_DB_PATH`
- Stored data: job ids, sender/recipient/action, lifecycle status, timestamps, redacted payload metadata, compact result summaries, and errors

## Seeding the database
Seed data is defined in `backend/seed_db.py`. Running:
```bash
python3 backend/seed_db.py
```
creates the initial component library (MCUs, sensors, displays, actuators, power parts).

On server startup, if the `component_templates` table is empty, the backend will also auto-seed the templates.

## Extensibility ideas
- Component metadata enrichment (availability, supply chain links)
- Versioned project history and diffing
- User accounts and shared project workspaces
- Parameterized footprints and PCB-ready libraries
