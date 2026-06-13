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
- **3D mechanical scene** for enclosure and component placements.

## Primary tabs
The main dashboard exposes several focused views:
- **IMAGE** – project summary plus generated product image, falling back to the uploaded reference image when no generated image is present.
- **BOM** – component list and total cost.
- **MECH** – 3D enclosure + placements (Three.js / React Three Fiber).
- **WIRE** – interactive React Flow wiring view.
- **DOCS** – step-by-step assembly guidance and safety notes.
- **SVG** – static SVG schematic rendering.

## Data flow
The UI communicates with the backend API:
- `GET /` – health check
- `GET /api/components` – component catalog
- `GET /api/projects` – history of generated projects
- `POST /api/generate` – run the agent pipeline

If the backend is offline, the UI can still load example JSONs from `frontend/public/examples/`.

## Deep links
You can load an example directly:
- `http://localhost:3000/?example=pocket_mp3_player`

You can also preselect a tab:
- `http://localhost:3000/?example=pocket_mp3_player&tab=mech`

## Where to look
- `frontend/app/page.tsx` – main UI, React Flow rendering, and tab layouts
- `frontend/public/examples/` – example IR JSON files
- `frontend/app/globals.css` – styling and theming
- `frontend/components/mechanical-scene.tsx` – 3D mechanical viewer

## Rendering details
The schematic view maps:
- **Components → Nodes**
- **ConnectionNet → Edges**
- **Net type → Color coding**

This makes it easy to visualize power, ground, I2C, SPI, and other signal types at a glance.
