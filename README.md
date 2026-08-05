# ERISAPros Schedule A / 5500 Dashboard

This project is now a split full-code stack:

- `frontend/`: React + Vite + TypeScript dashboard
- `backend/`: Python FastAPI backend with Pydantic models and a PydanticAI-ready service layer
- `MongoDB`: filing workflow database
- `AWS S3`: uploaded PDF storage
- `EyeLevel/GroundX`: extraction adapter
- `ShareFile`: optional automated intake adapter
- `FT Williams`: XML preview only in V1

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
GROUNDX_BUCKET_ID=28208
GROUNDX_API_BASE_URL=https://api.groundx.ai/api/v1
LOW_CONFIDENCE_THRESHOLD=0.8
```

## V1 behavior

Manual upload works first. ShareFile is included as a connector/status/sync API path, but real sync requires a valid ShareFile OAuth token/access setup from the client. FT Williams write operations are intentionally disabled; the app only prepares proposed XML for review.

Upload flow:

1. Save uploaded Schedule A PDF.
2. Send the PDF to the configured GroundX bucket.
3. Normalize GroundX extract/X-Ray output into standard fields.
4. Match extracted names against the 61 FT Williams Field Rules and aliases.
5. Show missing HIGH/MEDIUM/LOW fields, low-confidence fields, unmapped fields, and proposed XML for review.
