# Development

This project is research-oriented and welcomes contributors. Keep changes focused and explain scope clearly in PRs.

## Contribution workflow
1. Fork the repo and create a feature branch.
2. Run the existing frontend build/lint if your change touches the UI.
3. Add or update documentation when you change behavior.
4. Open a PR with a concise summary and testing notes.

## Adding a new agent
1. Define or extend the relevant Pydantic schema in `backend/models.py`.
2. Add a new step in `backend/agents/orchestrator.py`.
3. Ensure the agent’s output is merged into the Hardware IR.
4. Update docs in `docs/agents.md` and `docs/architecture.md`.

## Extending validation rules
1. Add a new rule function in `backend/validation.py`.
2. Emit a structured `ValidationIssue` with severity and troubleshooting.
3. Re-run validation in the pipeline and update `docs/validation.md`.

## Adding seed components
1. Add new entries in `backend/seed_db.py`.
2. Re-run `python3 seed_db.py` to repopulate the database.
3. Ensure pin definitions are complete and typed (power/ground/digital/etc).

## Frontend development tips
- Main UI: `frontend/app/page.tsx`
- Styling: Tailwind + custom CSS in `frontend/app/globals.css`
- Example IRs: `frontend/public/examples/`
