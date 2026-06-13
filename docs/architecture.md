# Architecture

Blueprint OSS turns prompts into structured hardware projects using a sequential, validation-aware agent pipeline. The system is intentionally scoped to low-voltage maker electronics and emphasizes traceable, typed outputs.

## System pipeline
1. **Prompt + optional image** enters the system.
2. **Safety guardrails** block high-risk domains early (weapons, medical, mains AC, etc.).
3. **Model resolution** determines whether live LLM generation runs or a deterministic simulation fallback is used.
4. **Intent Parser Agent** produces a high-level `ProjectOverview`.
5. **Requirements Agent** extracts functional requirements and constraints.
6. **Component Selection Agent** chooses parts from the seed database.
7. **Wiring/Netlist Agent** generates connection nets and pin mappings.
8. **Validation rules** run on the netlist.
9. **Repair loop** re-invokes the wiring agent if critical issues are found.
10. **BOM step** computes total cost deterministically.
11. **Mechanical/Fabrication Agent** drafts enclosure notes and (optionally) placements.
12. **Assembly Instruction Agent** emits step-by-step build guidance.
13. **Post-processing** enriches missing mechanical placements for the 3D viewer.
14. **Hardware IR** is stored in the database and rendered in the UI.
15. **A2A transports** expose generation and validation to external agents over REST, WebSocket, optional TCP JSONL, and MCP-style JSON-RPC.

## Orchestration and model runtime
- The backend runs an **ADK-style sequential workflow** implemented in `backend/agents/orchestrator.py`.
- Live structured JSON output is routed through `backend/llm_providers.py`.
- Supported providers are `gemini`, `openai`, `openai-compatible`, and `simulation`.
- Generic configuration uses `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `STRICT_LLM`, and `LLM_FALLBACK_MODEL`.
- Gemini-specific variables (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_MODEL`, `STRICT_GEMINI`, `GEMINI_FALLBACK_MODEL`) remain supported as compatibility aliases.
- If no API key is configured (or generation errors), the backend uses a deterministic simulation fallback backed by curated example projects.

## System diagram
```mermaid
flowchart TD
  A[Prompt + optional image] --> S[Safety guardrails]
  S --> M[Model resolution\n(live LLM vs simulation)]
  M --> B[Intent Parser Agent]
  B --> C[Requirements Agent]
  C --> D[Component Selection Agent]
  D --> E[Wiring/Netlist Agent]
  E --> F[Rule-based Validation]
  F -->|critical issues| E
  F --> G[BOM + Mechanical/Fabrication Agent]
  G --> H[Assembly Instruction Agent]
  H --> P[Mechanical render enrichment]
  P --> I[Typed Hardware IR]
  I --> J[UI: React Flow + SVG + Mermaid + 3D mech]
  I --> K[(Project database)]
  I --> L[A2A: REST + WebSocket + TCP JSONL + MCP]
```

## Core subsystems
- **Frontend (Next.js + React Flow):** Visualizes the structured project, nets, BOM, and instructions.
- **Backend (FastAPI):** Hosts the orchestration layer, validation, and storage APIs.
- **A2A broker:** Lets external agents register, send messages, listen for queued events, or call Blueprint tools through MCP-style JSON-RPC.
- **Database (Postgres/SQLite):** Stores component templates and generated projects.
- **Utilities:** Render Mermaid and SVG schematics from the IR.

## Output artifacts
- **Hardware IR JSON** (typed source of truth)
- **React Flow schematic** (interactive wiring view)
- **SVG schematic** (static vector view)
- **Mermaid diagram** (lightweight topology graph)
- **BOM + assembly steps**
