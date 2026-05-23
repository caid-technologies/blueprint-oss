# Validation

Validation is a rule-based safety layer that checks the generated netlist before it is finalized. It protects beginners from unsafe wiring and provides structured feedback for repair loops.

## Electrical rules (current MVP)
Validation logic lives in `backend/validation.py` and includes:
- **Short Circuit Detection:** Power directly connected to ground.
- **Voltage Mismatch:** Mixed logic voltages on the same net.
- **Unpowered ICs:** Active components without power or ground.
- **Pin Conflict:** A single signal pin reused across multiple nets.
- **Overcurrent Risk:** High-draw actuators powered from MCU rails.

## Severity levels
- **CRITICAL:** Must be fixed before the project is considered valid.
- **WARNING:** Risky but potentially acceptable with user intent.
- **INFO:** Non-blocking recommendations.

## Repair loops
When critical issues appear:
1. Validation issues are summarized.
2. The wiring/netlist agent is re-run with the error report.
3. Nets are regenerated and validation re-runs.

This loop keeps the IR grounded in real-world electrical constraints.

## Safety boundaries
Blueprint is intentionally constrained to **low-voltage maker electronics**. The system blocks or warns on:
- Mains AC systems (110–240V)
- Medical or life-support devices
- Automotive control systems
- Weapons or hazardous systems
- High-power battery packs

These checks are enforced before the agent pipeline runs to keep the MVP safe and focused.
