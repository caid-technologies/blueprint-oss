# Agents

Blueprint uses a sequential multi-agent workflow orchestrated by **Google ADK**. Each agent consumes the prior agent’s output and writes structured data into the Hardware IR.

## Pipeline overview
1. Intent Parser → 2. Requirements → 3. Component Selection → 4. Wiring/Netlist → 5. BOM → 6. Mechanical/Fabrication → 7. Assembly Instructions

## Agent responsibilities

### Intent Parser Agent
**Input:** Prompt (+ optional image)  
**Output:** `ProjectOverview`  
**Goal:** Convert intent into a concise, high-level project summary.

### Requirements Agent
**Input:** Prompt + `ProjectOverview`  
**Output:** `FunctionalRequirements`  
**Goal:** Extract functional requirements, power needs, constraints, and missing info.

### Component Selection Agent
**Input:** Requirements + seed component database  
**Output:** `ComponentInstance[]`  
**Goal:** Choose compatible parts and instantiate the BOM with pinouts.

### Wiring/Netlist Agent
**Input:** Components + requirements  
**Output:** `ConnectionNet[]` + `PinMappingEntry[]`  
**Goal:** Wire pins into power, ground, and signal nets.

### BOM Agent
**Input:** Component list  
**Output:** Updated `ProjectOverview.estimated_cost`  
**Goal:** Calculate total cost from unit prices and quantities (deterministic step).

### Mechanical/Fabrication Agent
**Input:** Overview + components  
**Output:** `MechanicalNotes`  
**Goal:** Suggest enclosure type, mounting, and fabrication details.

### Assembly Instruction Agent
**Input:** Overview + components + nets + mechanical notes  
**Output:** `AssemblyStep[]`  
**Goal:** Produce step-by-step build instructions with safety flags.

## State transitions
```mermaid
flowchart LR
  A[Prompt] --> B[ProjectOverview]
  B --> C[FunctionalRequirements]
  C --> D[ComponentInstance[]]
  D --> E[ConnectionNet[] + PinMappingEntry[]]
  E --> F[Validation + repair loop]
  F --> G[MechanicalNotes]
  G --> H[AssemblyStep[]]
  H --> I[Hardware IR]
```

## Notes
- Agents run **sequentially** for determinism and traceability.
- Validation can trigger a **repair loop** that re-invokes the wiring agent.
- The pipeline is designed to swap models or add agents without rewriting the core IR schema.
