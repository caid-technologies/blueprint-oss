# Hardware IR

Blueprint’s **Hardware IR** is a typed, versioned JSON schema built with Pydantic. It is the single source of truth for generated projects and is intentionally structured for validation, UI rendering, and future export formats.

## Why a typed IR?
- **Consistency:** Every agent writes into the same schema.
- **Validation-ready:** Rules can reason about pins, nets, and voltages.
- **UI-friendly:** The React Flow canvas can render nodes/edges directly.
- **Diffable:** Changes across versions are explicit and comparable.

## Top-level structure
The core schema lives in `backend/models.py` and includes:

- **hardware_ir_version** – schema version string.
- **overview** – `ProjectOverview` with title, description, difficulty, category.
- **requirements** – `FunctionalRequirements` with power, constraints, safety notes.
- **components** – list of `ComponentInstance` objects (instantiated BOM).
- **nets** – list of `ConnectionNet` objects (netlist connections).
- **buses** – `BusConnection` definitions (I2C/SPI/UART groups).
- **pin_mappings** – `PinMappingEntry` for MCU signal mapping.
- **assembly** – ordered `AssemblyStep` list.
- **mechanical** – `MechanicalNotes` for enclosure and fabrication.
- **constraints** – extra design constraints and notes.
- **power_rails** – summarized `PowerRail` entries.
- **estimated_current_draw_ma** – rough peak current estimate.
- **fabrication_notes** – free-form manufacturing notes.
- **validation** – `ValidationSummary` with categorized issues.
- **is_valid** – boolean status after validation.

## Key relationships
- **ComponentInstance → PinDefinition:** Each instance carries a full pinout.
- **ConnectionNet → PinReference:** Nets reference component pins by `ref_des` + `pin_id`.
- **BusConnection → ConnectionNet:** Buses group nets for higher-level comms.
- **ValidationSummary → ValidationIssue:** Structured diagnostics live inside the IR.

## Validation-aware generation
The IR is produced in a loop:
1. Agents generate components and nets.
2. Rule-based validation runs on the netlist.
3. Critical issues trigger a wiring repair step.
4. Validation results are embedded back into the IR.

This makes the IR more than a snapshot—it’s a record of what was checked and why the design is considered safe within MVP scope.
