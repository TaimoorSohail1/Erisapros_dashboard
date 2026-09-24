# ERISAPros Schedule A / 5500 Dashboard

This project is now a split full-code stack:

- `frontend/`: React + Vite + TypeScript dashboard
- `backend/`: Python FastAPI backend with Pydantic models and a PydanticAI-ready service layer
- `MongoDB`: filing workflow database
- `AWS S3`: uploaded PDF storage
- `EyeLevel/GroundX`: extraction adapter
- `ShareFile`: optional automated intake adapter
- `FT Williams`: current-value review, guarded updates, edit checks, and a local Windows agent for verified Bring Forward work

## Run locally

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

Environment variables can live in root `.env.local` or `backend/.env.local`.

GroundX extraction needs:

```bash
GROUNDX_API_KEY=your_groundx_key
GROUNDX_BUCKET_ID=your_groundx_bucket_id
GROUNDX_API_BASE_URL=https://api.groundx.ai/api/v1
LOW_CONFIDENCE_THRESHOLD=0.8
```

## Core workflow

The application accepts manual or ShareFile intake, extracts Schedule A data, compares it with FT Williams, routes uncertain fields to review, and supports guarded FT Williams updates. Automatic update and Bring Forward features are controlled by explicit safety flags and remain disabled unless an authorized environment enables them.

Upload flow:

1. Save uploaded Schedule A PDF.
2. Send the PDF to the configured GroundX bucket.
3. Normalize GroundX extract/X-Ray output into standard fields.
4. Match extracted values against the FT Williams field catalog and aliases.
5. Compare extracted values with current FT Williams values and surface missing, low-confidence, or conflicting fields for review.
6. Send only approved values, run FT Williams edit checks, and preserve an audit trail.

See [CONTEXT.md](CONTEXT.md) for the system context and [HANDOVER.md](HANDOVER.md) for setup, verification, and documentation links.
