# Local Setup

Blueprint OSS runs a FastAPI backend and a Next.js frontend. PostgreSQL is supported but optional; the backend will fall back to SQLite for local use.

## Prerequisites
- **Python 3.11+**
- **Node.js 18+**
- **PostgreSQL** (optional, recommended for persistent storage)

## Backend setup
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment variables
Create `backend/.env` (optional but recommended):
```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/blueprint
GEMINI_API_KEY=your_gemini_api_key_here
```
Notes:
- If `DATABASE_URL` is missing or the connection fails, the backend uses `sqlite:///./blueprint.db`.
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) enables real multi-agent generation. Without it, the backend uses simulated example outputs.

### Seed the component database
```bash
python3 seed_db.py
```

### Run the backend
```bash
uvicorn main:app --reload --port 8000
```
API docs are available at http://localhost:8000/docs.

## Frontend setup
```bash
cd ../frontend
npm install
npm run dev
```
Open http://localhost:3000 to use the UI.

## Optional: validate a netlist
You can submit a netlist to `POST /api/validate` to test validation rules without running the full pipeline.
