# NYC First Responder Dispatch Intelligence

AI-assisted incident triage and building risk intelligence for New York City addresses, powered by public safety and property datasets.

## Hackathon Context

This project was built as part of an NVIDIA hackathon and was run on NVIDIA's GN100 workstation environment during development and demo validation.

## What This Project Does

Given an NYC address, BBL/BIN, or coordinates, the system resolves the building and returns a structured intelligence report across incident, violations, and infrastructure sources.

Key outputs include:

- Building profile (PLUTO): year built, floors, class, owner, lot area
- Computed risk score from violations and incident signals
- DOB violations and ECB penalties
- HPD violations and complaints
- 311 complaints and service requests
- FDNY fire incidents and fire company response history
- EMS incidents and response context
- NYPD nearby complaints
- Fire prevention inspection data
- Elevator device details
- Nearby facilities and hospitals

## Tech Stack

- Backend: FastAPI + DuckDB + optional Ollama model integration
- Frontend: React + TypeScript + Vite
- Data: 27 NYC Open Data datasets ingested into local DuckDB

## Repository Structure

```text
.
+-- src/api/            # FastAPI app and triage/lookup endpoints
+-- src/cpp/            # CUDA/C++ kernels and parser components
+-- scripts/            # ingestion, benchmark, validation utilities
+-- frontend/           # Vite + React UI
+-- demo/               # sample scenarios
+-- tests/              # parser tests
```

## Local Setup

Use Python 3.10 for best dependency compatibility on Windows.

```bash
# from repo root
py -3.10 -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

## Run Locally

1. Ingest datasets (downloads + load + risk + embeddings):

```bash
python scripts/ingest.py --all
```

2. Start backend API:

```bash
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

3. Start frontend (new terminal):

```bash
cd frontend
npm install
npm run dev
```

## Endpoints

- Frontend: `http://localhost:5173`
- API: `http://localhost:8000`
- Health check: `http://localhost:8000/health`

## Notes

- Initial ingestion is large and can take significant time and disk space.
- If Ollama is not running, the API falls back to rule-based triage behavior.
