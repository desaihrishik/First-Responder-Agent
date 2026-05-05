# NYC Dispatch Intelligence — Location Lookup

Enter any NYC address → get a complete risk report from 27 datasets.

## What it does

Type an address. The system finds the building by BBL/BIN/address/lat-lon, then fans out across every NYC dataset and returns:

- **Building profile** (PLUTO): year built, floors, class, owner, lot area
- **Risk score** (computed live): weighted composite from violations + incidents
- **DOB violations**: active building code violations
- **ECB violations**: environmental control board penalties and balances
- **HPD violations**: housing code violations (Class A/B/C)
- **HPD complaints**: tenant complaints and status
- **311 requests**: service requests matched by BBL or address
- **Fire incidents**: FDNY dispatch records near the building
- **Fire company responses**: detailed fire response with actions taken
- **EMS incidents**: ambulance calls in the vicinity
- **NYPD complaints**: crime reports near the building
- **Fire inspections**: FDNY Bureau of Fire Prevention results
- **Elevators**: device status, capacity, floors served
- **Nearby hospitals**: closest hospitals with distance
- **Nearby facilities**: schools, fire stations, precincts

## Stack

- **Backend**: FastAPI + DuckDB (that's it — no ChromaDB, no ML models, no pyarrow)
- **Frontend**: React + TypeScript + Vite
- **Data**: 27 NYC Open Data parquet files (~7GB total)

## Run

```bash
# 1. Ingest data (use your existing ingest.py --all)
python scripts/ingest.py --all

# 2. Start API
pip install -r requirements.txt
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000

# 3. Start frontend
cd frontend && npm install && npm run dev
```

Open http://localhost:5173
