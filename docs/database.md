# Database

Blueprint stores component templates and generated projects in a relational database. PostgreSQL is the default, with a **SQLite fallback** for local development.

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

## Seeding the database
Seed data is defined in `backend/seed_db.py`. Running:
```bash
python3 seed_db.py
```
creates the initial component library (MCUs, sensors, displays, actuators, power parts).

## Extensibility ideas
- Component metadata enrichment (availability, supply chain links)
- Versioned project history and diffing
- User accounts and shared project workspaces
- Parameterized footprints and PCB-ready libraries
