# Frontend

The frontend is a **Next.js 14** app that visualizes Hardware IR and provides the interactive CAD-style experience.

## Core UI features
- **Prompt input** with optional image upload.
- **Example presets** for quick exploration.
- **React Flow schematic** showing components and nets.
- **Vector schematic** rendered from SVG output.
- **BOM & sourcing** table.
- **Assembly instructions** and **mechanical notes** views.
- **Export** of the Hardware IR package as JSON.

## Primary tabs
The main dashboard exposes several focused views:
- **Overview** – project summary, constraints, and cost metrics.
- **Schematic** – interactive React Flow wiring view.
- **Vector View** – SVG schematic rendering.
- **BOM & Sourcing** – component list and total cost.
- **Instructions** – step-by-step assembly guidance.
- **Mechanical** – enclosure and fabrication guidance.

## Data flow
The UI communicates with the backend API:
- `GET /` – health check
- `GET /api/components` – component catalog
- `GET /api/projects` – history of generated projects
- `POST /api/generate` – run the agent pipeline

## Where to look
- `frontend/app/page.tsx` – main UI, React Flow rendering, and tab layouts
- `frontend/public/examples/` – example IR JSON files
- `frontend/app/globals.css` – styling and theming

## Rendering details
The schematic view maps:
- **Components → Nodes**
- **ConnectionNet → Edges**
- **Net type → Color coding**

This makes it easy to visualize power, ground, I2C, SPI, and other signal types at a glance.
