#!/usr/bin/env python3
"""
NYC First Responder Dispatch — Unified Data Ingestion Pipeline
==============================================================
Covers ALL 27 datasets identified in the CSV data report:

  Incident data:
    311_Service_Requests_from_2020_to_Present.csv  (also: 311_requests.csv)
    EMS_Incident_Dispatch_Data.csv                 (also: ems_incidents.csv)
    Fire_Incident_Dispatch_Data.csv                (also: fire_incidents.csv)
    Incidents_Responded_to_by_Fire_Companies.csv   (also: fire_company_incidents.csv)
    NYPD_Complaint_Data_Current__Year_To_Date_.csv (also: NYPD_complaints.csv)
    fire_prevention_inspections.csv

  Building / property data:
    Primary_Land_Use_Tax_Lot_Output__PLUTO_.csv    (also: pluto.csv)
    Housing_Maintenance_Code_Complaints_and_Problems.csv (also: hpd_complaints.csv)
    Housing_Maintenance_Code_Violations.csv        (also: hpd_violations.csv)
    dob_violations.csv
    dob_safety_violations.csv
    dob_ecb_violations.csv
    Multiple_Dwelling_Registrations.csv            (also: registrations.csv)
    registration_contacts.csv

  Infrastructure / reference data:
    DOB_NOW__Build_Elevator_Device_Details.csv     (also: elevators.csv)
    fire_hydrants.csv
    Facilities_Database.csv                        (hospitals + city facilities)
    hospitals.csv

Design principles
-----------------
* Uses DuckDB's native parquet/CSV readers — no pyarrow required.
* Every load function uses DELETE then INSERT (no INSERT OR REPLACE conflicts).
* Dynamic column detection: column aliases handle any case/naming variant seen
  in both the raw NYC open-data filenames AND the normalised local filenames.
* Full CLI: --download | --load | --portfolio | --risk | --all
* Incremental-friendly: re-running any phase is safe (DELETE before INSERT).
* Structured logging to stdout + logs/ingest.log.

Usage
-----
  python ingest.py --download          # fetch all 27 datasets from Socrata
  python ingest.py --load              # load CSVs/parquets into DuckDB
  python ingest.py --portfolio         # compute owner portfolio metrics
  python ingest.py --risk              # compute per-building risk scores
  python ingest.py --all               # full pipeline end-to-end
"""

import os
import sys
import time
import logging
import argparse
import hashlib
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Directory setup  (must happen before any I/O)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
RAW_DIR      = DATA_DIR / "raw"
DB_PATH      = DATA_DIR / "responder.duckdb"
CHROMA_DIR   = DATA_DIR / "chromadb"
LOGS_DIR     = PROJECT_ROOT / "logs"
SCHEMA_PATH  = PROJECT_ROOT / "schema.sql"

for _d in [DATA_DIR, RAW_DIR, CHROMA_DIR, LOGS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOGS_DIR / "ingest.log"), mode="w"),
    ],
)
log = logging.getLogger("ingest")

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
_MISSING = []
for _pkg in ["duckdb", "numpy", "pandas", "requests"]:
    try:
        __import__(_pkg)
    except ImportError:
        _MISSING.append(_pkg)

# Optional — only needed for ChromaDB phase
_CHROMA_OK = True
try:
    import chromadb
    from sentence_transformers import SentenceTransformer
except ImportError:
    _CHROMA_OK = False

if _MISSING:
    log.error(f"Missing required packages: {', '.join(_MISSING)}")
    log.error(f"  pip install {' '.join(_MISSING)} --break-system-packages")
    sys.exit(1)

import duckdb
import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BATCH_SIZE   = 512
EMBED_MODEL  = "sentence-transformers/all-MiniLM-L6-v2"
SOCRATA_BASE = "https://data.cityofnewyork.us/resource"

BOROUGH_MAP = {
    "MANHATTAN": "MANHATTAN", "BROOKLYN": "BROOKLYN",
    "QUEENS": "QUEENS",       "BRONX": "BRONX",
    "STATEN ISLAND": "STATEN ISLAND",
    "NEW YORK": "MANHATTAN",  "KINGS": "BROOKLYN",
    "RICHMOND": "STATEN ISLAND",
    "1": "MANHATTAN", "2": "BRONX",  "3": "BROOKLYN",
    "4": "QUEENS",    "5": "STATEN ISLAND",
}

# ---------------------------------------------------------------------------
# Dataset catalog
# All 27 files present in the CSV report, with their Socrata IDs and the
# candidate local filenames (both original NYC names and normalised aliases).
# ---------------------------------------------------------------------------
DATASETS = {
    # ── Incident / service datasets ──────────────────────────────────────
    "311_requests": {
        "socrata_id": "erm2-nwe9",
        "limit": 500_000,
        "filter": (
            "complaint_type in('HEATING','PLUMBING','GENERAL CONSTRUCTION',"
            "'Heat/Hot Water','ELECTRIC','Gas Leak','STRUCTURAL','SAFETY',"
            "'Hazardous Materials','Illegal Conversion','Building/Use')"
        ),
        "files": [
            "311_Service_Requests_from_2020_to_Present.parquet",
            "311_Service_Requests_from_2020_to_Present.csv",
            "311_requests.parquet",
            "311_requests.csv",
        ],
        "description": "311 safety service requests",
    },
    "ems_incidents": {
        "socrata_id": "76xm-jjuj",
        "limit": 2_000_000,
        "filter": None,
        "files": [
            "EMS_Incident_Dispatch_Data.parquet",
            "EMS_Incident_Dispatch_Data.csv",
            "ems_incidents.parquet",
            "ems_incidents.csv",
        ],
        "description": "EMS dispatch data",
    },
    "fire_incidents": {
        "socrata_id": "8m42-w767",
        "limit": 2_000_000,
        "filter": None,
        "files": [
            "Fire_Incident_Dispatch_Data.parquet",
            "Fire_Incident_Dispatch_Data.csv",
            "fire_incidents.parquet",
            "fire_incidents.csv",
        ],
        "description": "Fire incident dispatch",
    },
    "fire_company_incidents": {
        "socrata_id": "tm6d-hbzd",
        "limit": 2_000_000,
        "filter": None,
        "files": [
            "Incidents_Responded_to_by_Fire_Companies.parquet",
            "Incidents_Responded_to_by_Fire_Companies.csv",
            "fire_company_incidents.parquet",
            "fire_company_incidents.csv",
        ],
        "description": "Incidents responded to by fire companies",
    },
    "nypd_complaints": {
        "socrata_id": "",           # manual download — large file
        "limit": 0,
        "filter": None,
        "files": [
            "NYPD_Complaint_Data_Current__Year_To_Date_.parquet",
            "NYPD_Complaint_Data_Current__Year_To_Date_.csv",
            "NYPD_complaints.parquet",
            "NYPD_complaints.csv",
        ],
        "description": "NYPD complaint data (manual download)",
    },
    "fire_prevention_inspections": {
        "socrata_id": "ssq6-fkht",
        "limit": 500_000,
        "filter": None,
        "files": [
            "fire_prevention_inspections.parquet",
            "fire_prevention_inspections.csv",
        ],
        "description": "FDNY Bureau of Fire Prevention inspections",
    },
    # ── Building / property datasets ──────────────────────────────────────
    "pluto": {
        "socrata_id": "64uk-42ks",
        "limit": 900_000,
        "filter": None,
        "files": [
            "Primary_Land_Use_Tax_Lot_Output__PLUTO_.parquet",
            "Primary_Land_Use_Tax_Lot_Output__PLUTO_.csv",
            "pluto.parquet",
            "pluto.csv",
        ],
        "description": "PLUTO building profiles",
    },
    "hpd_complaints": {
        "socrata_id": "ygpa-z7cr",
        "limit": 3_000_000,
        "filter": None,
        "files": [
            "Housing_Maintenance_Code_Complaints_and_Problems.parquet",
            "Housing_Maintenance_Code_Complaints_and_Problems.csv",
            "hpd_complaints.parquet",
            "hpd_complaints.csv",
        ],
        "description": "HPD housing complaints",
    },
    "hpd_violations": {
        "socrata_id": "wvxf-dwi5",
        "limit": 3_000_000,
        "filter": None,
        "files": [
            "Housing_Maintenance_Code_Violations.parquet",
            "Housing_Maintenance_Code_Violations.csv",
            "hpd_violations.parquet",
            "hpd_violations.csv",
        ],
        "description": "HPD housing violations",
    },
    "dob_violations": {
        "socrata_id": "3h2n-5cm9",
        "limit": 2_000_000,
        "filter": None,
        "files": [
            "dob_violations.parquet",
            "dob_violations.csv",
        ],
        "description": "DOB violations",
    },
    "dob_safety_violations": {
        "socrata_id": "855j-jady",
        "limit": 500_000,
        "filter": None,
        "files": [
            "dob_safety_violations.parquet",
            "dob_safety_violations.csv",
        ],
        "description": "DOB safety violations",
    },
    "dob_ecb_violations": {
        "socrata_id": "6bgk-3dad",
        "limit": 1_000_000,
        "filter": None,
        "files": [
            "dob_ecb_violations.parquet",
            "dob_ecb_violations.csv",
        ],
        "description": "DOB ECB violations",
    },
    "registrations": {
        "socrata_id": "tesw-yqqr",
        "limit": 500_000,
        "filter": None,
        "files": [
            "Multiple_Dwelling_Registrations.parquet",
            "Multiple_Dwelling_Registrations.csv",
            "registrations.parquet",
            "registrations.csv",
        ],
        "description": "Multiple dwelling registrations",
    },
    "registration_contacts": {
        "socrata_id": "feu5-w2e2",
        "limit": 1_000_000,
        "filter": None,
        "files": [
            "registration_contacts.parquet",
            "registration_contacts.csv",
        ],
        "description": "Registration owner/management contacts",
    },
    # ── Infrastructure / reference datasets ───────────────────────────────
    "elevators": {
        "socrata_id": "juyv-2jek",
        "limit": 500_000,
        "filter": None,
        "files": [
            "DOB_NOW__Build_Elevator_Device_Details.parquet",
            "DOB_NOW__Build_Elevator_Device_Details.csv",
            "elevators.parquet",
            "elevators.csv",
        ],
        "description": "Elevator device details (DOB_NOW)",
    },
    "fire_hydrants": {
        "socrata_id": "23d2-ttdp",
        "limit": 200_000,
        "filter": None,
        "files": [
            "fire_hydrants.parquet",
            "fire_hydrants.csv",
        ],
        "description": "Fire hydrant locations",
    },
    "facilities": {
        "socrata_id": "ji82-xba5",
        "limit": 200_000,
        "filter": None,
        "files": [
            "Facilities_Database.parquet",
            "Facilities_Database.csv",
            "facilities.parquet",
            "facilities.csv",
        ],
        "description": "NYC Facilities Database (schools, hospitals, city services)",
    },
    "hospitals": {
        "socrata_id": "",           # subset of facilities — use facilities file
        "limit": 0,
        "filter": None,
        "files": [
            "hospitals.parquet",
            "hospitals.csv",
        ],
        "description": "NYC hospitals (standalone or from Facilities DB)",
    },
}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def fp_str(fp: Path) -> str:
    """Forward-slash path string safe for DuckDB SQL (escapes single-quotes)."""
    return str(fp).replace("\\", "/").replace("'", "''")


def find_file(search_dirs: list, candidates: list) -> Path | None:
    """Return the first existing candidate file found in any search dir."""
    for d in search_dirs:
        for fname in candidates:
            p = Path(d) / fname
            if p.exists():
                return p
    return None


def get_columns(con, fp: Path) -> list[str]:
    """
    Return column names from a parquet or CSV file using DuckDB.
    Works without pyarrow.
    """
    ext = fp.suffix.lower()
    path = fp_str(fp)
    try:
        if ext == ".parquet":
            query = f"DESCRIBE SELECT * FROM read_parquet('{path}') LIMIT 0"
        else:
            query = (
                f"SELECT * FROM read_csv_auto('{path}', "
                f"header=true, ignore_errors=true, sample_size=200) LIMIT 0"
            )
            desc = con.execute(query).description
            return [c[0] for c in desc]
        result = con.execute(query).fetchall()
        return [row[0] for row in result]
    except Exception as e:
        log.warning(f"    Cannot read schema from {fp.name}: {e}")
        return []


def col(available: set, *candidates) -> str:
    """
    Return first matching candidate as a quoted DuckDB column reference.
    Falls back to NULL if none found.
    `available` is a set of lowercase column names.
    """
    for c in candidates:
        if c.lower() in available:
            return f'"{c}"'
    return "NULL"


def read_auto(fp: Path) -> str:
    """
    Return the DuckDB read expression for a file, choosing between
    read_parquet and read_csv_auto based on file extension.
    This is THE fix for risk scores being 0 — previously every load
    function used read_csv_auto even on .parquet files, which silently
    produced empty/corrupted results.
    """
    path = fp_str(fp)
    if fp.suffix.lower() == ".parquet":
        return f"read_parquet('{path}')"
    else:
        return f"read_csv_auto('{path}', header=true, ignore_errors=true, all_varchar=true)"




def as_date(expr: str) -> str:
    """
    Safe for both parquet-typed DATE columns and CSV string columns.
    """
    return f"TRY_CAST(CAST({expr} AS VARCHAR) AS DATE)"


def as_timestamp(expr: str) -> str:
    """
    Safe for both parquet-typed TIMESTAMP columns and CSV string columns.
    """
    return f"TRY_CAST(CAST({expr} AS VARCHAR) AS TIMESTAMP)"

def normalize_borough(val) -> str:
    if val is None:
        return "UNKNOWN"
    if isinstance(val, float):
        try:
            if np.isnan(val):
                return "UNKNOWN"
        except (TypeError, ValueError):
            pass
    s = str(val).strip().upper()
    if not s or s in ("NAN", "NONE", "NAT", ""):
        return "UNKNOWN"
    return BOROUGH_MAP.get(s, s if s in BOROUGH_MAP.values() else "UNKNOWN")


# ---------------------------------------------------------------------------
# Phase 0 — Download datasets from Socrata
# ---------------------------------------------------------------------------

def download_dataset(name: str, config: dict, dest_dir: Path) -> bool:
    sid = config.get("socrata_id", "")
    if not sid:
        log.info(f"  SKIP {name}: manual/local-only dataset")
        return True

    # Use first .csv candidate name as the output filename
    csv_candidates = [f for f in config["files"] if f.endswith(".csv")]
    if not csv_candidates:
        log.warning(f"  SKIP {name}: no CSV filename configured")
        return False

    out_path = dest_dir / csv_candidates[0]
    if out_path.exists():
        log.info(f"  EXISTS {name}: {out_path.name} ({out_path.stat().st_size/1e6:.1f} MB)")
        return True

    url = f"{SOCRATA_BASE}/{sid}.csv"
    params = {"$limit": config["limit"]}
    if config.get("filter"):
        params["$where"] = config["filter"]

    log.info(f"  DOWNLOADING {name}: {config['description']}")
    try:
        resp = requests.get(url, params=params, stream=True, timeout=300)
        resp.raise_for_status()
        total = 0
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=131_072):
                f.write(chunk)
                total += len(chunk)
        log.info(f"  OK {name}: {total/1e6:.1f} MB -> {out_path.name}")
        return True
    except Exception as e:
        log.error(f"  FAIL {name}: {e}")
        return False


def download_all(dest_dir: Path = RAW_DIR):
    dest_dir.mkdir(parents=True, exist_ok=True)
    log.info("=" * 60)
    log.info("DOWNLOADING NYC OPEN DATA DATASETS")
    log.info("=" * 60)
    ok, fail = 0, 0
    for name, config in DATASETS.items():
        result = download_dataset(name, config, dest_dir)
        if result:
            ok += 1
        else:
            fail += 1
    log.info(f"Download complete: {ok} succeeded, {fail} failed")
    return fail == 0


# ---------------------------------------------------------------------------
# Phase 1 — Schema initialisation
# ---------------------------------------------------------------------------

def init_schema(con):
    """
    Create all required tables.  If schema.sql exists it is executed first;
    otherwise a minimal inline schema is created so the pipeline can run
    without an external SQL file.
    """
    if SCHEMA_PATH.exists():
        log.info(f"  Using schema file: {SCHEMA_PATH}")
        with open(SCHEMA_PATH) as f:
            sql = f.read()
        try:
            con.execute(sql)
        except Exception:
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        con.execute(stmt)
                    except Exception as e:
                        if "already exists" not in str(e).lower():
                            log.warning(f"  Schema warning: {e}")
    else:
        log.info("  schema.sql not found — using inline schema")
        _create_inline_schema(con)

    _migrate_schema(con)
    log.info("  Schema ready")


def _create_inline_schema(con):
    con.executemany("", [])     # no-op, tables are created by load functions if absent
    statements = [
        """CREATE TABLE IF NOT EXISTS buildings (
            bbl VARCHAR, bin VARCHAR, borough VARCHAR,
            block VARCHAR, lot VARCHAR, address VARCHAR, zipcode VARCHAR,
            borough_code INTEGER, num_floors INTEGER, year_built INTEGER,
            building_class VARCHAR, land_use VARCHAR,
            residential_units INTEGER, total_units INTEGER,
            lot_area DOUBLE, building_area DOUBLE,
            construction_type VARCHAR, owner_name VARCHAR,
            latitude DOUBLE, longitude DOUBLE,
            risk_score DOUBLE DEFAULT 0.0,
            last_updated TIMESTAMP
        )""",
        "CREATE TABLE IF NOT EXISTS owner_portfolio (owner_name VARCHAR, total_buildings INTEGER, total_open_violations INTEGER, total_class_c_violations INTEGER, total_ecb_balance_due DOUBLE, avg_violations_per_building DOUBLE, bins VARCHAR)",
        "CREATE TABLE IF NOT EXISTS dob_violations (id BIGINT, bin VARCHAR, block VARCHAR, lot VARCHAR, violation_type VARCHAR, violation_number VARCHAR, violation_category VARCHAR, description VARCHAR, disposition_date DATE, disposition_comments VARCHAR, issue_date DATE, severity VARCHAR, is_active BOOLEAN)",
        "CREATE TABLE IF NOT EXISTS dob_safety_violations (id BIGINT, bin VARCHAR, violation_number VARCHAR, violation_type VARCHAR, violation_description VARCHAR, issue_date DATE, status VARCHAR)",
        "CREATE TABLE IF NOT EXISTS dob_ecb_violations (ecb_violation_number VARCHAR, bin VARCHAR, block VARCHAR, lot VARCHAR, violation_type VARCHAR, violation_description VARCHAR, infraction_code VARCHAR, section_law_description VARCHAR, penalty_imposed DOUBLE, amount_paid DOUBLE, balance_due DOUBLE, hearing_date DATE, hearing_status VARCHAR, served_date DATE, issue_date DATE, severity VARCHAR, is_active BOOLEAN)",
        "CREATE TABLE IF NOT EXISTS hpd_violations (violation_id INTEGER, bin VARCHAR, building_id INTEGER, borough_id VARCHAR, block VARCHAR, lot VARCHAR, apartment VARCHAR, story VARCHAR, violation_class VARCHAR, inspection_date DATE, approved_date DATE, original_certify_by_date DATE, original_correct_by_date DATE, new_certify_by_date DATE, new_correct_by_date DATE, certified_dismissed_datetime TIMESTAMP, order_number VARCHAR, nov_id VARCHAR, nov_description VARCHAR, nov_issueddate DATE, current_status VARCHAR, current_status_date DATE)",
        "CREATE TABLE IF NOT EXISTS hpd_complaints (complaint_id INTEGER, bin VARCHAR, building_id INTEGER, borough_id VARCHAR, block VARCHAR, lot VARCHAR, apartment VARCHAR, status VARCHAR, status_date DATE, complaint_type VARCHAR, major_category VARCHAR, minor_category VARCHAR, code VARCHAR, problem_description VARCHAR, status_description VARCHAR, received_date DATE)",
        "CREATE TABLE IF NOT EXISTS fire_incidents (incident_id VARCHAR, incident_datetime TIMESTAMP, incident_type_desc VARCHAR, incident_borough VARCHAR, zipcode VARCHAR, policeprecinct VARCHAR, incident_classification VARCHAR, incident_classification_group VARCHAR, dispatch_response_seconds INTEGER, incident_response_seconds INTEGER, incident_travel_seconds INTEGER, engines_assigned INTEGER, ladders_assigned INTEGER, other_units_assigned INTEGER, latitude DOUBLE, longitude DOUBLE, bin VARCHAR)",
        "CREATE TABLE IF NOT EXISTS fire_company_incidents (id BIGINT, im_incident_key VARCHAR, incident_type_desc VARCHAR, incident_date_time TIMESTAMP, arrival_date_time TIMESTAMP, last_unit_cleared_date_time TIMESTAMP, highest_alarm_level VARCHAR, total_incident_duration INTEGER, action_taken_primary VARCHAR, action_taken_secondary VARCHAR, property_use_desc VARCHAR, street_highway VARCHAR, zip_code VARCHAR, borough_desc VARCHAR, floor_of_fire_origin VARCHAR, fire_origin_below_grade BOOLEAN, fire_spread_desc VARCHAR, detector_presence_desc VARCHAR, aes_presence_desc VARCHAR, standpipe_system_type_desc VARCHAR, latitude DOUBLE, longitude DOUBLE)",
        "CREATE TABLE IF NOT EXISTS ems_incidents (cad_incident_id VARCHAR, incident_datetime TIMESTAMP, initial_call_type VARCHAR, final_call_type VARCHAR, initial_severity_level VARCHAR, final_severity_level VARCHAR, incident_disposition VARCHAR, borough VARCHAR, zipcode VARCHAR, policeprecinct VARCHAR, citycouncildistrict VARCHAR, communitydistrict VARCHAR, dispatch_response_seconds INTEGER, incident_response_seconds INTEGER, incident_travel_seconds INTEGER, latitude DOUBLE, longitude DOUBLE)",
        "CREATE TABLE IF NOT EXISTS service_requests_311 (unique_key VARCHAR, created_date TIMESTAMP, closed_date TIMESTAMP, agency VARCHAR, agency_name VARCHAR, complaint_type VARCHAR, descriptor VARCHAR, location_type VARCHAR, incident_zip VARCHAR, incident_address VARCHAR, city VARCHAR, borough VARCHAR, latitude DOUBLE, longitude DOUBLE, bbl VARCHAR, bin VARCHAR, status VARCHAR, resolution_description VARCHAR)",
        "CREATE TABLE IF NOT EXISTS nypd_complaints (complaint_number VARCHAR, complaint_date DATE, borough VARCHAR, offense_description VARCHAR, law_category VARCHAR, premises_type VARCHAR, police_precinct VARCHAR, latitude DOUBLE, longitude DOUBLE)",
        "CREATE TABLE IF NOT EXISTS fire_prevention_inspections (id BIGINT, bin VARCHAR, address VARCHAR, borough VARCHAR, inspection_date DATE, inspection_type VARCHAR, result VARCHAR, violation_description VARCHAR, certificate_number VARCHAR, expiration_date DATE, is_compliant BOOLEAN)",
        "CREATE TABLE IF NOT EXISTS elevators (id BIGINT, bin VARCHAR, job_filing_number VARCHAR, device_id VARCHAR, device_number VARCHAR, device_type VARCHAR, device_status VARCHAR, floor_from VARCHAR, floor_to VARCHAR, speed VARCHAR, capacity VARCHAR, approval_date DATE, status VARCHAR)",
        "CREATE TABLE IF NOT EXISTS fire_hydrants (id BIGINT, latitude DOUBLE, longitude DOUBLE, unitid VARCHAR, borough VARCHAR)",
        "CREATE TABLE IF NOT EXISTS facilities (uid VARCHAR, facname VARCHAR, addressnum VARCHAR, streetname VARCHAR, address VARCHAR, city VARCHAR, borough VARCHAR, borocode VARCHAR, zipcode VARCHAR, factype VARCHAR, facsubgrp VARCHAR, opname VARCHAR, latitude DOUBLE, longitude DOUBLE)",
        "CREATE TABLE IF NOT EXISTS hospitals (facility_name VARCHAR, facility_type VARCHAR, borough VARCHAR, address VARCHAR, phone VARCHAR, latitude DOUBLE, longitude DOUBLE)",
        "CREATE TABLE IF NOT EXISTS building_risk_scores (bbl VARCHAR, bin VARCHAR, overall_risk_score DOUBLE DEFAULT 0.0, active_dob_violations INTEGER, active_ecb_violations INTEGER, active_hpd_class_c INTEGER, active_hpd_class_b INTEGER, prior_fire_incidents INTEGER, prior_ems_incidents INTEGER, nearby_nypd_incidents INTEGER, complaint_velocity_30d INTEGER, complaint_velocity_90d INTEGER, last_fdny_inspection_pass BOOLEAN, elevator_count INTEGER, elevator_out_of_service INTEGER, nearest_hydrant_ft DOUBLE, nearest_hospital VARCHAR, nearest_hospital_mi DOUBLE)",
    ]
    for stmt in statements:
        try:
            con.execute(stmt)
        except Exception as e:
            if "already exists" not in str(e).lower():
                log.warning(f"  Inline schema warning: {e}")


def _migrate_schema(con):
    """Backfill any missing columns on pre-existing databases."""
    existing_tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}

    def _add_col_if_missing(table, column, typedef):
        if table not in existing_tables:
            return
        cols = {r[0].lower() for r in con.execute(f"DESCRIBE {table}").fetchall()}
        if column.lower() not in cols:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")
                log.info(f"  Migration: added {table}.{column}")
            except Exception as e:
                log.warning(f"  Migration warning: {e}")

    _add_col_if_missing("buildings", "bbl", "VARCHAR")
    _add_col_if_missing("buildings", "bin", "VARCHAR")
    _add_col_if_missing("building_risk_scores", "bbl", "VARCHAR")
    _add_col_if_missing("building_risk_scores", "bin", "VARCHAR")
    _add_col_if_missing("building_risk_scores", "nearby_nypd_incidents", "INTEGER")
    _add_col_if_missing("service_requests_311", "bbl", "VARCHAR")
    _add_col_if_missing("elevators", "job_filing_number", "VARCHAR")
    _add_col_if_missing("elevators", "device_id", "VARCHAR")
    _add_col_if_missing("facilities", "factype", "VARCHAR")
    _add_col_if_missing("facilities", "facsubgrp", "VARCHAR")
    _add_col_if_missing("facilities", "opname", "VARCHAR")

    # Indexes
    index_cmds = [
        "CREATE INDEX IF NOT EXISTS idx_buildings_bin   ON buildings(bin)",
        "CREATE INDEX IF NOT EXISTS idx_buildings_bbl   ON buildings(bbl)",
        "CREATE INDEX IF NOT EXISTS idx_brs_bin         ON building_risk_scores(bin)",
        "CREATE INDEX IF NOT EXISTS idx_brs_bbl         ON building_risk_scores(bbl)",
        "CREATE INDEX IF NOT EXISTS idx_311_bbl         ON service_requests_311(bbl)",
    ]
    for cmd in index_cmds:
        try:
            con.execute(cmd)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Phase 1 — Load helpers
# ---------------------------------------------------------------------------

def _find_source_file(config: dict) -> Path | None:
    search_dirs = [RAW_DIR, DATA_DIR]
    return find_file(search_dirs, config["files"])


def _read_csv_auto(con, fp: Path) -> set:
    """Return lowercase set of column names for a CSV/parquet file."""
    try:
        cols = get_columns(con, fp)
        return {c.lower() for c in cols}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Individual load functions
# ---------------------------------------------------------------------------

def load_pluto(con):
    cfg = DATASETS["pluto"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP pluto: file not found")
        return
    log.info(f"  Loading PLUTO from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_bbl   = col(cols, "bbl", "BBL")
    c_bin   = col(cols, "bin", "BIN")
    c_block = col(cols, "block", "Block", "Tax block")
    c_lot   = col(cols, "lot", "Lot", "Tax lot")
    c_zip   = col(cols, "zipcode", "ZIPCode", "postcode")
    if c_bbl == "NULL":
        log.warning("  SKIP pluto: BBL column not found")
        return
    con.execute("DELETE FROM buildings")
    con.execute(f"""
        INSERT INTO buildings (
            bbl, bin, borough, block, lot, address, zipcode, borough_code,
            num_floors, year_built, building_class, land_use,
            residential_units, total_units, lot_area, building_area,
            construction_type, owner_name, latitude, longitude,
            risk_score, last_updated
        )
        SELECT
            CAST({c_bbl} AS VARCHAR)                AS bbl,
            CASE
                WHEN {c_bin} IS NULL OR TRIM(CAST({c_bin} AS VARCHAR)) IN ('','0')
                THEN NULL
                ELSE CAST({c_bin} AS VARCHAR)
            END                                     AS bin,
            "borough"                               AS borough,
            CAST({c_block} AS VARCHAR)              AS block,
            CAST({c_lot} AS VARCHAR)                AS lot,
            "address"                               AS address,
            CAST({c_zip} AS VARCHAR)                AS zipcode,
            TRY_CAST("borocode" AS INTEGER)         AS borough_code,
            TRY_CAST("numfloors" AS INTEGER)        AS num_floors,
            TRY_CAST("yearbuilt" AS INTEGER)        AS year_built,
            "bldgclass"                             AS building_class,
            "landuse"                               AS land_use,
            TRY_CAST("unitsres" AS INTEGER)         AS residential_units,
            TRY_CAST("unitstotal" AS INTEGER)       AS total_units,
            TRY_CAST("lotarea" AS DOUBLE)           AS lot_area,
            TRY_CAST("bldgarea" AS DOUBLE)          AS building_area,
            NULL                                    AS construction_type,
            "ownername"                             AS owner_name,
            TRY_CAST("latitude" AS DOUBLE)          AS latitude,
            TRY_CAST("longitude" AS DOUBLE)         AS longitude,
            0.0                                     AS risk_score,
            CURRENT_TIMESTAMP                       AS last_updated
        FROM {read_auto(fp)}
        WHERE {c_bbl} IS NOT NULL
          AND TRIM(CAST({c_bbl} AS VARCHAR)) NOT IN ('', '0')
    """)
    n = con.execute("SELECT COUNT(*) FROM buildings").fetchone()[0]
    log.info(f"  OK pluto: {n:,} buildings")


def load_registrations(con):
    cfg = DATASETS["registrations"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP registrations: file not found")
        return
    log.info(f"  Loading Registrations from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_contact = col(cols, "contactdescription", "ContactDescription",
                    "OwnersBusinessName", "ownername")
    c_regid   = col(cols, "registrationid", "RegistrationID")
    c_bin     = col(cols, "bin", "BIN")
    con.execute("DELETE FROM owner_portfolio")
    con.execute(f"""
        INSERT INTO owner_portfolio (owner_name, total_buildings,
            total_open_violations, total_class_c_violations,
            total_ecb_balance_due, avg_violations_per_building, bins)
        SELECT
            {c_contact}                             AS owner_name,
            COUNT(DISTINCT {c_regid})               AS total_buildings,
            0, 0, 0.0, 0.0,
            STRING_AGG(CAST({c_bin} AS VARCHAR), ',')
        FROM {read_auto(fp)}
        WHERE {c_contact} IS NOT NULL AND {c_bin} IS NOT NULL
        GROUP BY {c_contact}
    """)
    n = con.execute("SELECT COUNT(*) FROM owner_portfolio").fetchone()[0]
    log.info(f"  OK registrations: {n:,} owners")


def load_registration_contacts(con):
    cfg = DATASETS["registration_contacts"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP registration_contacts: file not found")
        return
    log.info(f"  Loading Registration Contacts from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_corp  = col(cols, "corporationname", "CorporationName")
    c_first = col(cols, "firstname", "FirstName")
    c_last  = col(cols, "lastname", "LastName")
    c_reg   = col(cols, "registrationid", "RegistrationID")
    c_type  = col(cols, "type", "contacttype", "ContactType")
    con.execute(f"""
        INSERT INTO owner_portfolio (owner_name, total_buildings,
            total_open_violations, total_class_c_violations,
            total_ecb_balance_due, avg_violations_per_building, bins)
        SELECT
            COALESCE({c_corp},
                TRIM(CONCAT(COALESCE({c_first},''), ' ', COALESCE({c_last},''))))
                                                    AS owner_name,
            COUNT(DISTINCT {c_reg})                 AS total_buildings,
            0, 0, 0.0, 0.0,
            STRING_AGG(CAST({c_reg} AS VARCHAR), ',')
        FROM {read_auto(fp)}
        WHERE {c_type} IN ('HeadOfficer','IndividualOwner','CorporateOwner')
          AND COALESCE({c_corp}, {c_first}) IS NOT NULL
        GROUP BY owner_name
        HAVING owner_name NOT IN (
            SELECT owner_name FROM owner_portfolio WHERE owner_name IS NOT NULL
        )
    """)
    n = con.execute("SELECT COUNT(*) FROM owner_portfolio").fetchone()[0]
    log.info(f"  OK registration_contacts: owner_portfolio now {n:,} records")


def load_dob_violations(con):
    cfg = DATASETS["dob_violations"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP dob_violations: file not found")
        return
    log.info(f"  Loading DOB Violations from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_bin     = col(cols, "bin", "BIN")
    c_blk     = col(cols, "block", "BLOCK", "Block")
    c_lot     = col(cols, "lot", "LOT", "Lot")
    c_vtype   = col(cols, "violation_type", "VIOLATION_TYPE", "ViolationType")
    c_vnum    = col(cols, "violation_number", "VIOLATION_NUMBER")
    c_vcat    = col(cols, "violation_category", "VIOLATION_CATEGORY")
    c_desc    = col(cols, "description", "DESCRIPTION", "ViolationDescription")
    c_disp_dt = col(cols, "disposition_date", "DISPOSITION_DATE", "DispositionDate")
    c_disp_cm = col(cols, "disposition_comments", "DISPOSITION_COMMENTS")
    c_iss_dt  = col(cols, "issue_date", "ISSUE_DATE", "IssueDate",
                    "isn_dob_bis_viol")   # fallback id col if issue_date absent
    con.execute("DELETE FROM dob_violations")
    con.execute(f"""
        INSERT INTO dob_violations
        SELECT
            ROW_NUMBER() OVER ()                    AS id,
            CAST({c_bin} AS VARCHAR)                AS bin,
            CAST({c_blk} AS VARCHAR)                AS block,
            CAST({c_lot} AS VARCHAR)                AS lot,
            {c_vtype}                               AS violation_type,
            {c_vnum}                                AS violation_number,
            {c_vcat}                                AS violation_category,
            {c_desc}                                AS description,
            TRY_CAST({c_disp_dt} AS DATE)           AS disposition_date,
            {c_disp_cm}                             AS disposition_comments,
            TRY_CAST({c_iss_dt} AS DATE)            AS issue_date,
            CASE
                WHEN {c_vtype} IN ('LL6291','AEUHAZ','IMEGNCY','LL1081') THEN 'CRITICAL'
                WHEN {c_vtype} IN ('ACC1','HBLVIO','P*CLSS1')            THEN 'HIGH'
                WHEN {c_vtype} IN ('UB','COMPBLD')                       THEN 'MEDIUM'
                ELSE 'LOW'
            END                                     AS severity,
            CASE WHEN {c_disp_dt} IS NULL THEN TRUE ELSE FALSE END AS is_active
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
    """)
    total  = con.execute("SELECT COUNT(*) FROM dob_violations").fetchone()[0]
    active = con.execute("SELECT COUNT(*) FROM dob_violations WHERE is_active").fetchone()[0]
    log.info(f"  OK dob_violations: {total:,} total, {active:,} active")


def load_dob_safety_violations(con):
    cfg = DATASETS["dob_safety_violations"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP dob_safety_violations: file not found")
        return
    log.info(f"  Loading DOB Safety Violations from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_bin    = col(cols, "bin", "BIN")
    c_vnum   = col(cols, "violation_number", "VIOLATION_NUMBER")
    c_vtype  = col(cols, "violation_type", "VIOLATION_TYPE")
    c_vdesc  = col(cols, "violation_description", "VIOLATION_DESCRIPTION", "description")
    c_iss_dt = col(cols, "issue_date", "ISSUE_DATE")
    c_status = col(cols, "status", "STATUS")
    con.execute("DELETE FROM dob_safety_violations")
    con.execute(f"""
        INSERT INTO dob_safety_violations
        SELECT
            ROW_NUMBER() OVER ()            AS id,
            CAST({c_bin} AS VARCHAR)        AS bin,
            {c_vnum}                        AS violation_number,
            {c_vtype}                       AS violation_type,
            {c_vdesc}                       AS violation_description,
            TRY_CAST({c_iss_dt} AS DATE)    AS issue_date,
            COALESCE({c_status}, 'OPEN')    AS status
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM dob_safety_violations").fetchone()[0]
    log.info(f"  OK dob_safety_violations: {n:,} records")


def load_dob_ecb_violations(con):
    cfg = DATASETS["dob_ecb_violations"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP dob_ecb_violations: file not found")
        return
    log.info(f"  Loading DOB ECB Violations from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_bin     = col(cols, "bin", "BIN")
    c_ecb_num = col(cols, "ecb_violation_number", "isn_dob_bis_viol")
    c_blk     = col(cols, "block", "BLOCK", "Block")
    c_lot     = col(cols, "lot", "LOT", "Lot")
    c_vtype   = col(cols, "violation_type", "VIOLATION_TYPE")
    c_vdesc   = col(cols, "violation_description", "VIOLATION_DESCRIPTION")
    c_infr    = col(cols, "infraction_code")
    c_seclaw  = col(cols, "section_law_description")
    c_pen     = col(cols, "penalty_imposed")
    c_paid    = col(cols, "amount_paid")
    c_bal     = col(cols, "balance_due")
    c_hear_dt = col(cols, "hearing_date", "hearing_date_time")
    c_hear_st = col(cols, "hearing_status")
    c_serv_dt = col(cols, "served_date")
    c_iss_dt  = col(cols, "issue_date")
    con.execute("DELETE FROM dob_ecb_violations")
    con.execute(f"""
        INSERT INTO dob_ecb_violations
        SELECT
            {c_ecb_num}                             AS ecb_violation_number,
            CAST({c_bin} AS VARCHAR)                AS bin,
            CAST({c_blk} AS VARCHAR)                AS block,
            CAST({c_lot} AS VARCHAR)                AS lot,
            {c_vtype}                               AS violation_type,
            {c_vdesc}                               AS violation_description,
            {c_infr}                                AS infraction_code,
            {c_seclaw}                              AS section_law_description,
            TRY_CAST({c_pen}  AS DOUBLE)            AS penalty_imposed,
            TRY_CAST({c_paid} AS DOUBLE)            AS amount_paid,
            TRY_CAST({c_bal}  AS DOUBLE)            AS balance_due,
            TRY_CAST({c_hear_dt} AS DATE)           AS hearing_date,
            {c_hear_st}                             AS hearing_status,
            TRY_CAST({c_serv_dt} AS DATE)           AS served_date,
            TRY_CAST({c_iss_dt}  AS DATE)           AS issue_date,
            CASE
                WHEN TRY_CAST({c_pen} AS DOUBLE) > 10000 THEN 'CRITICAL'
                WHEN TRY_CAST({c_pen} AS DOUBLE) > 2500  THEN 'HIGH'
                WHEN TRY_CAST({c_pen} AS DOUBLE) > 500   THEN 'MEDIUM'
                ELSE 'LOW'
            END                                     AS severity,
            CASE WHEN TRY_CAST({c_bal} AS DOUBLE) > 0
                 THEN TRUE ELSE FALSE END           AS is_active
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
    """)
    total  = con.execute("SELECT COUNT(*) FROM dob_ecb_violations").fetchone()[0]
    unpaid = con.execute("SELECT COUNT(*) FROM dob_ecb_violations WHERE is_active").fetchone()[0]
    bal    = con.execute("SELECT COALESCE(SUM(balance_due),0) FROM dob_ecb_violations").fetchone()[0]
    log.info(f"  OK dob_ecb_violations: {total:,} total, {unpaid:,} unpaid (${bal:,.0f} owed)")


def load_hpd_violations(con):
    cfg = DATASETS["hpd_violations"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP hpd_violations: file not found")
        return
    log.info(f"  Loading HPD Violations from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_vid  = col(cols, "violationid", "ViolationID")
    c_bin  = col(cols, "bin", "BIN")
    c_bldg = col(cols, "buildingid", "BuildingID")
    c_boro = col(cols, "boroid", "BoroID")
    c_blk  = col(cols, "block", "Block")
    c_lot  = col(cols, "lot", "Lot")
    c_apt  = col(cols, "apartment", "Apartment")
    c_story = col(cols, "story", "Story")
    c_cls  = col(cols, "class", "Class", "violationclass", "ViolationClass")
    c_insp = col(cols, "inspectiondate", "InspectionDate")
    c_appr = col(cols, "approveddate", "ApprovedDate")
    c_ocbd = col(cols, "originalcertifybydate")
    c_ocrd = col(cols, "originalcorrectbydate")
    c_ncbd = col(cols, "newcertifybydate")
    c_ncrd = col(cols, "newcorrectbydate")
    c_cdd  = col(cols, "certifieddismisseddatetime")
    c_ordr = col(cols, "ordernumber", "OrderNumber")
    c_novid  = col(cols, "novid", "NOVID")
    c_novdsc = col(cols, "novdescription", "NOVDescription")
    c_novis  = col(cols, "novissueddate", "NOVIssuedDate")
    c_curst  = col(cols, "currentstatus", "CurrentStatus")
    c_curstd = col(cols, "currentstatusdate", "CurrentStatusDate")
    con.execute("DELETE FROM hpd_violations")
    con.execute(f"""
        INSERT INTO hpd_violations
        SELECT
            TRY_CAST({c_vid}  AS INTEGER)           AS violation_id,
            CAST({c_bin} AS VARCHAR)                AS bin,
            TRY_CAST({c_bldg} AS INTEGER)           AS building_id,
            {c_boro}                                AS borough_id,
            {c_blk}                                 AS block,
            {c_lot}                                 AS lot,
            {c_apt}                                 AS apartment,
            {c_story}                               AS story,
            {c_cls}                                 AS violation_class,
            TRY_CAST({c_insp} AS DATE)              AS inspection_date,
            TRY_CAST({c_appr} AS DATE)              AS approved_date,
            TRY_CAST({c_ocbd} AS DATE)              AS original_certify_by_date,
            TRY_CAST({c_ocrd} AS DATE)              AS original_correct_by_date,
            TRY_CAST({c_ncbd} AS DATE)              AS new_certify_by_date,
            TRY_CAST({c_ncrd} AS DATE)              AS new_correct_by_date,
            TRY_CAST({c_cdd}  AS TIMESTAMP)         AS certified_dismissed_datetime,
            {c_ordr}                                AS order_number,
            {c_novid}                               AS nov_id,
            {c_novdsc}                              AS nov_description,
            TRY_CAST({c_novis} AS DATE)             AS nov_issueddate,
            {c_curst}                               AS current_status,
            TRY_CAST({c_curstd} AS DATE)            AS current_status_date
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
    """)
    total   = con.execute("SELECT COUNT(*) FROM hpd_violations").fetchone()[0]
    class_c = con.execute("""
        SELECT COUNT(*) FROM hpd_violations
        WHERE violation_class = 'C' AND current_status != 'CLOSE'
    """).fetchone()[0]
    log.info(f"  OK hpd_violations: {total:,} total, {class_c:,} open Class C")


def load_hpd_complaints(con):
    cfg = DATASETS["hpd_complaints"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP hpd_complaints: file not found")
        return
    log.info(f"  Loading HPD Complaints from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_cid   = col(cols, "problemid", "ProblemID", "complaintid", "ComplaintID",
                  "problem id", "complaint id")
    c_bin   = col(cols, "bin", "BIN")
    c_bldg  = col(cols, "buildingid", "BuildingID", "building id")
    c_boro  = col(cols, "boroid", "BoroID", "borough")
    c_blk   = col(cols, "block", "Block")
    c_lot   = col(cols, "lot", "Lot")
    c_apt   = col(cols, "apartment", "Apartment")
    c_stat  = col(cols, "status", "Status", "complaint status", "problem status")
    c_statd = col(cols, "statusdate", "StatusDate", "complaint status date",
                  "problem status date")
    c_ctype = col(cols, "type", "Type", "complainttype")
    c_majc  = col(cols, "majorcategoryid", "MajorCategoryID",
                  "majorcategory", "MajorCategory", "major category")
    c_minc  = col(cols, "minorcategoryid", "MinorCategoryID",
                  "minorcategory", "MinorCategory", "minor category")
    c_code  = col(cols, "codeid", "CodeID", "code", "Code", "problem code")
    c_stdsc = col(cols, "statusdescription", "StatusDescription")
    c_prdsc = col(cols, "problemdescription", "ProblemDescription",
                  "problem description")
    c_recvd = col(cols, "receiveddate", "ReceivedDate", "received date")
    con.execute("DELETE FROM hpd_complaints")
    con.execute(f"""
        INSERT INTO hpd_complaints
        SELECT
            TRY_CAST({c_cid} AS INTEGER)    AS complaint_id,
            CAST({c_bin} AS VARCHAR)        AS bin,
            TRY_CAST({c_bldg} AS INTEGER)   AS building_id,
            {c_boro}                        AS borough_id,
            {c_blk}                         AS block,
            {c_lot}                         AS lot,
            {c_apt}                         AS apartment,
            {c_stat}                        AS status,
            {as_date(c_statd)}              AS status_date,
            {c_ctype}                       AS complaint_type,
            {c_majc}                        AS major_category,
            {c_minc}                        AS minor_category,
            {c_code}                        AS code,
            {c_prdsc}                       AS problem_description,
            {c_stdsc}                       AS status_description,
            {as_date(c_recvd)}              AS received_date
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM hpd_complaints").fetchone()[0]
    log.info(f"  OK hpd_complaints: {n:,} records")


def load_fire_incidents(con):
    cfg = DATASETS["fire_incidents"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP fire_incidents: file not found")
        return
    log.info(f"  Loading Fire Incidents from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_id   = col(cols, "starfire_incident_id", "STARFIRE_INCIDENT_ID")
    c_dt   = col(cols, "incident_datetime", "INCIDENT_DATETIME")
    c_type = col(cols, "incident_type_desc", "INCIDENT_TYPE_DESC")
    c_boro = col(cols, "incident_borough", "INCIDENT_BOROUGH")
    c_zip  = col(cols, "zipcode", "ZIPCODE", "zip_code", "ZIP_CODE")
    c_prec = col(cols, "policeprecinct", "POLICEPRECINCT")
    c_cls  = col(cols, "incident_classification", "INCIDENT_CLASSIFICATION")
    c_clsg = col(cols, "incident_classification_group", "INCIDENT_CLASSIFICATION_GROUP")
    c_disp = col(cols, "dispatch_response_seconds_qy", "DISPATCH_RESPONSE_SECONDS_QY")
    c_resp = col(cols, "incident_response_seconds_qy", "INCIDENT_RESPONSE_SECONDS_QY")
    c_trav = col(cols, "incident_travel_tm_seconds_qy", "INCIDENT_TRAVEL_TM_SECONDS_QY")
    c_eng  = col(cols, "engines_assigned_quantity", "ENGINES_ASSIGNED_QUANTITY")
    c_lad  = col(cols, "ladders_assigned_quantity", "LADDERS_ASSIGNED_QUANTITY")
    c_oth  = col(cols, "other_units_assigned_quantity", "OTHER_UNITS_ASSIGNED_QUANTITY")
    c_lat  = col(cols, "latitude", "LATITUDE")
    c_lon  = col(cols, "longitude", "LONGITUDE")
    con.execute("DELETE FROM fire_incidents")
    con.execute(f"""
        INSERT INTO fire_incidents
        SELECT incident_id, incident_datetime, incident_type_desc, incident_borough,
               zipcode, policeprecinct, incident_classification, incident_classification_group,
               dispatch_response_seconds, incident_response_seconds, incident_travel_seconds,
               engines_assigned, ladders_assigned, other_units_assigned, latitude, longitude, bin
        FROM (
            SELECT
                {c_id}                              AS incident_id,
                {as_timestamp(c_dt)}            AS incident_datetime,
                {c_type}                            AS incident_type_desc,
                {c_boro}                            AS incident_borough,
                {c_zip}                             AS zipcode,
                {c_prec}                            AS policeprecinct,
                {c_cls}                             AS incident_classification,
                {c_clsg}                            AS incident_classification_group,
                TRY_CAST({c_disp} AS INTEGER)       AS dispatch_response_seconds,
                TRY_CAST({c_resp} AS INTEGER)       AS incident_response_seconds,
                TRY_CAST({c_trav} AS INTEGER)       AS incident_travel_seconds,
                TRY_CAST({c_eng}  AS INTEGER)       AS engines_assigned,
                TRY_CAST({c_lad}  AS INTEGER)       AS ladders_assigned,
                TRY_CAST({c_oth}  AS INTEGER)       AS other_units_assigned,
                TRY_CAST({c_lat}  AS DOUBLE)        AS latitude,
                TRY_CAST({c_lon}  AS DOUBLE)        AS longitude,
                NULL                                AS bin,
                ROW_NUMBER() OVER (
                    PARTITION BY {c_id}
                    ORDER BY {as_timestamp(c_dt)} DESC NULLS LAST
                )                                   AS rn
            FROM {read_auto(fp)}
            WHERE {c_id} IS NOT NULL
              AND TRIM(CAST({c_id} AS VARCHAR)) != ''
        ) deduped
        WHERE rn = 1
    """)
    n = con.execute("SELECT COUNT(*) FROM fire_incidents").fetchone()[0]
    log.info(f"  OK fire_incidents: {n:,} records")


def load_fire_company_incidents(con):
    cfg = DATASETS["fire_company_incidents"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP fire_company_incidents: file not found")
        return
    log.info(f"  Loading Fire Company Incidents from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_imkey  = col(cols, "im_incident_key", "IM_INCIDENT_KEY")
    c_type   = col(cols, "incident_type_desc", "INCIDENT_TYPE_DESC")
    c_dt     = col(cols, "incident_date_time", "INCIDENT_DATE_TIME")
    c_arr    = col(cols, "arrival_date_time", "ARRIVAL_DATE_TIME")
    c_clr    = col(cols, "last_unit_cleared_date_time", "LAST_UNIT_CLEARED_DATE_TIME")
    c_alarm  = col(cols, "highest_alarm_level", "HIGHEST_ALARM_LEVEL")
    c_dur    = col(cols, "total_incident_duration", "TOTAL_INCIDENT_DURATION")
    c_act1   = col(cols, "action_taken1_desc", "ACTION_TAKEN1_DESC")
    c_act2   = col(cols, "action_taken2_desc", "ACTION_TAKEN2_DESC")
    c_prop   = col(cols, "property_use_desc", "PROPERTY_USE_DESC")
    c_street = col(cols, "street_highway", "STREET_HIGHWAY")
    c_zip    = col(cols, "zip_code", "ZIP_CODE", "zipcode", "ZIPCODE")
    c_boro   = col(cols, "borough_desc", "BOROUGH_DESC")
    c_floor  = col(cols, "floor_of_fire_origin", "FLOOR_OF_FIRE_ORIGIN")
    c_below  = col(cols, "fire_origin_below_grade", "FIRE_ORIGIN_BELOW_GRADE")
    c_spread = col(cols, "fire_spread_desc", "FIRE_SPREAD_DESC")
    c_det    = col(cols, "detector_presence_desc", "DETECTOR_PRESENCE_DESC")
    c_aes    = col(cols, "aes_presence_desc", "AES_PRESENCE_DESC")
    c_stand  = col(cols, "standpipe_sys_present_desc", "STANDPIPE_SYS_PRESENT_DESC")
    c_lat    = col(cols, "latitude", "LATITUDE")
    c_lon    = col(cols, "longitude", "LONGITUDE")
    con.execute("DELETE FROM fire_company_incidents")
    con.execute(f"""
        INSERT INTO fire_company_incidents
        SELECT
            ROW_NUMBER() OVER ()                    AS id,
            {c_imkey}                               AS im_incident_key,
            {c_type}                                AS incident_type_desc,
            TRY_CAST({c_dt}  AS TIMESTAMP)          AS incident_date_time,
            TRY_CAST({c_arr} AS TIMESTAMP)          AS arrival_date_time,
            TRY_CAST({c_clr} AS TIMESTAMP)          AS last_unit_cleared_date_time,
            {c_alarm}                               AS highest_alarm_level,
            TRY_CAST({c_dur} AS INTEGER)            AS total_incident_duration,
            {c_act1}                                AS action_taken_primary,
            {c_act2}                                AS action_taken_secondary,
            {c_prop}                                AS property_use_desc,
            {c_street}                              AS street_highway,
            {c_zip}                                 AS zip_code,
            {c_boro}                                AS borough_desc,
            {c_floor}                               AS floor_of_fire_origin,
            CASE WHEN {c_below} = 'Y' THEN TRUE ELSE FALSE END AS fire_origin_below_grade,
            {c_spread}                              AS fire_spread_desc,
            {c_det}                                 AS detector_presence_desc,
            {c_aes}                                 AS aes_presence_desc,
            {c_stand}                               AS standpipe_system_type_desc,
            TRY_CAST({c_lat} AS DOUBLE)             AS latitude,
            TRY_CAST({c_lon} AS DOUBLE)             AS longitude
        FROM {read_auto(fp)}
    """)
    n = con.execute("SELECT COUNT(*) FROM fire_company_incidents").fetchone()[0]
    log.info(f"  OK fire_company_incidents: {n:,} records")


def load_ems_incidents(con):
    cfg = DATASETS["ems_incidents"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP ems_incidents: file not found")
        return
    log.info(f"  Loading EMS Incidents from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_cad    = col(cols, "cad_incident_id", "CAD_INCIDENT_ID")
    c_dt     = col(cols, "incident_datetime", "INCIDENT_DATETIME")
    c_inct   = col(cols, "initial_call_type", "INITIAL_CALL_TYPE")
    c_finct  = col(cols, "final_call_type", "FINAL_CALL_TYPE")
    c_isev   = col(cols, "initial_severity_level_code", "INITIAL_SEVERITY_LEVEL_CODE")
    c_fsev   = col(cols, "final_severity_level_code", "FINAL_SEVERITY_LEVEL_CODE")
    c_disp   = col(cols, "incident_disposition_code", "INCIDENT_DISPOSITION_CODE")
    c_boro   = col(cols, "borough", "BOROUGH")
    c_zip    = col(cols, "zipcode", "ZIPCODE")
    c_prec   = col(cols, "policeprecinct", "POLICEPRECINCT")
    c_council= col(cols, "citycouncildistrict", "CITYCOUNCILDISTRICT")
    c_comm   = col(cols, "communitydistrict", "COMMUNITYDISTRICT")
    c_dresp  = col(cols, "dispatch_response_seconds_qy", "DISPATCH_RESPONSE_SECONDS_QY")
    c_iresp  = col(cols, "incident_response_seconds_qy", "INCIDENT_RESPONSE_SECONDS_QY")
    c_trav   = col(cols, "incident_travel_tm_seconds_qy", "INCIDENT_TRAVEL_TM_SECONDS_QY")
    c_lat    = col(cols, "latitude", "LATITUDE")
    c_lon    = col(cols, "longitude", "LONGITUDE")
    con.execute("DELETE FROM ems_incidents")
    con.execute(f"""
        INSERT INTO ems_incidents
        SELECT
            {c_cad}                             AS cad_incident_id,
            TRY_CAST({c_dt} AS TIMESTAMP)       AS incident_datetime,
            {c_inct}                            AS initial_call_type,
            {c_finct}                           AS final_call_type,
            {c_isev}                            AS initial_severity_level,
            {c_fsev}                            AS final_severity_level,
            {c_disp}                            AS incident_disposition,
            {c_boro}                            AS borough,
            {c_zip}                             AS zipcode,
            {c_prec}                            AS policeprecinct,
            {c_council}                         AS citycouncildistrict,
            {c_comm}                            AS communitydistrict,
            TRY_CAST({c_dresp} AS INTEGER)      AS dispatch_response_seconds,
            TRY_CAST({c_iresp} AS INTEGER)      AS incident_response_seconds,
            TRY_CAST({c_trav}  AS INTEGER)      AS incident_travel_seconds,
            TRY_CAST({c_lat}   AS DOUBLE)       AS latitude,
            TRY_CAST({c_lon}   AS DOUBLE)       AS longitude
        FROM {read_auto(fp)}
    """)
    n = con.execute("SELECT COUNT(*) FROM ems_incidents").fetchone()[0]
    log.info(f"  OK ems_incidents: {n:,} records")


def load_311(con):
    cfg = DATASETS["311_requests"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP 311_requests: file not found")
        return
    log.info(f"  Loading 311 Requests from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_ukey  = col(cols, "unique_key", "UniqueKey", "unique key", "Unique Key")
    c_crdt  = col(cols, "created_date", "CreatedDate", "created date")
    c_cldt  = col(cols, "closed_date", "ClosedDate", "closed date")
    c_agcy  = col(cols, "agency", "Agency")
    c_agnam = col(cols, "agency_name", "AgencyName", "agency name")
    c_ctype = col(cols, "complaint_type", "ComplaintType",
                  "problem (formerly complaint type)",
                  "Problem (formerly Complaint Type)")
    c_desc  = col(cols, "descriptor", "Descriptor",
                  "problem detail (formerly descriptor)",
                  "Problem Detail (formerly Descriptor)")
    c_loctp = col(cols, "location_type", "LocationType", "location type")
    c_zip   = col(cols, "incident_zip", "IncidentZip", "incident zip")
    c_addr  = col(cols, "incident_address", "IncidentAddress", "incident address")
    c_city  = col(cols, "city", "City")
    c_boro  = col(cols, "borough", "Borough")
    c_bbl   = col(cols, "bbl", "BBL")
    c_lat   = col(cols, "latitude", "Latitude")
    c_lon   = col(cols, "longitude", "Longitude")
    c_stat  = col(cols, "status", "Status")
    c_res   = col(cols, "resolution_description", "ResolutionDescription",
                  "resolution description")
    con.execute("DELETE FROM service_requests_311")
    con.execute(f"""
        INSERT INTO service_requests_311
        SELECT
            {c_ukey}                        AS unique_key,
            {as_timestamp(c_crdt)}      AS created_date,
            {as_timestamp(c_cldt)}      AS closed_date,
            {c_agcy}                        AS agency,
            {c_agnam}                       AS agency_name,
            {c_ctype}                       AS complaint_type,
            {c_desc}                        AS descriptor,
            {c_loctp}                       AS location_type,
            CAST({c_zip} AS VARCHAR)        AS incident_zip,
            {c_addr}                        AS incident_address,
            {c_city}                        AS city,
            {c_boro}                        AS borough,
            TRY_CAST({c_lat} AS DOUBLE)     AS latitude,
            TRY_CAST({c_lon} AS DOUBLE)     AS longitude,
            CAST({c_bbl} AS VARCHAR)        AS bbl,
            NULL                            AS bin,
            {c_stat}                        AS status,
            {c_res}                         AS resolution_description
        FROM {read_auto(fp)}
    """)
    n = con.execute("SELECT COUNT(*) FROM service_requests_311").fetchone()[0]
    log.info(f"  OK 311_requests: {n:,} records")


def load_nypd_complaints(con):
    cfg = DATASETS["nypd_complaints"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP nypd_complaints: file not found")
        return
    log.info(f"  Loading NYPD Complaints from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_num  = col(cols, "cmplnt_num", "CMPLNT_NUM")
    c_date = col(cols, "cmplnt_fr_dt", "CMPLNT_FR_DT", "rpt_dt", "RPT_DT")
    c_boro = col(cols, "boro_nm", "BORO_NM")
    c_ofns = col(cols, "ofns_desc", "OFNS_DESC")
    c_law  = col(cols, "law_cat_cd", "LAW_CAT_CD")
    c_prem = col(cols, "prem_typ_desc", "PREM_TYP_DESC")
    c_prec = col(cols, "addr_pct_cd", "ADDR_PCT_CD")
    c_lat  = col(cols, "latitude", "Latitude")
    c_lon  = col(cols, "longitude", "Longitude")
    con.execute("DELETE FROM nypd_complaints")
    con.execute(f"""
        INSERT INTO nypd_complaints
        SELECT
            CAST({c_num} AS VARCHAR)        AS complaint_number,
            TRY_CAST({c_date} AS DATE)      AS complaint_date,
            {c_boro}                        AS borough,
            {c_ofns}                        AS offense_description,
            {c_law}                         AS law_category,
            {c_prem}                        AS premises_type,
            CAST({c_prec} AS VARCHAR)       AS police_precinct,
            TRY_CAST({c_lat} AS DOUBLE)     AS latitude,
            TRY_CAST({c_lon} AS DOUBLE)     AS longitude
        FROM {read_auto(fp)}
        WHERE {c_num} IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM nypd_complaints").fetchone()[0]
    log.info(f"  OK nypd_complaints: {n:,} records")


def load_fire_prevention_inspections(con):
    cfg = DATASETS["fire_prevention_inspections"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP fire_prevention_inspections: file not found")
        return
    log.info(f"  Loading Fire Prevention Inspections from {fp.name}")
    cols = _read_csv_auto(con, fp)

    # ── Actual column names in the CSV (verified from data report) ─────────
    c_bin = col(cols, "bin", "BIN")
    c_bbl = col(cols, "bbl", "BBL")  # bonus: available
    c_addr = col(cols, "prem_addr", "PREM_ADDR",  # ← was "premisesaddress"
                 "premisesaddress", "PREMISESADDRESS", "address")
    c_boro = col(cols, "borough", "BOROUGH")
    # Two date columns; prefer the full-inspection date over the last-visit date
    c_insp_dt = col(cols, "last_full_insp_dt", "LAST_FULL_INSP_DT",  # ← was "inspectiondate"
                    "last_visit_dt", "LAST_VISIT_DT",
                    "inspectiondate", "INSPECTIONDATE")
    # Inspection type: alpha = 'DV' (device) / 'N' (non-device) / 'F' (fire)
    c_itype = col(cols, "alpha", "ALPHA",
                  "inspectiontype", "INSPECTIONTYPE")
    # Result: 'APPROVAL' | 'NOT APPROVAL(VIO)' | 'NOV(HOLD)'             ← was "result"
    c_result = col(cols, "last_insp_stat", "LAST_INSP_STAT",
                   "result", "RESULT")
    # No violation_description in this dataset; owner_name is the best proxy
    c_vdesc = col(cols, "owner_name", "OWNER_NAME",
                  "violationdescription", "VIOLATIONDESCRIPTION",
                  "violation_description")
    # Certificate number → use account number
    c_certno = col(cols, "acct_num", "ACCT_NUM",
                   "certificatenumber", "CERTIFICATENUMBER")
    # No expiration_date; reuse last_visit_dt as a reasonable fallback
    c_expdt = col(cols, "last_visit_dt", "LAST_VISIT_DT",
                  "expirationdate", "EXPIRATIONDATE")
    # Coordinates — actual names are cent_latitude / cent_longitude       ← was latitude/longitude
    c_lat = col(cols, "cent_latitude", "CENT_LATITUDE",
                "latitude", "LATITUDE")
    c_lon = col(cols, "cent_longitude", "CENT_LONGITUDE",
                "longitude", "LONGITUDE")

    con.execute("DELETE FROM fire_prevention_inspections")
    con.execute(f"""
        INSERT INTO fire_prevention_inspections
        SELECT
            ROW_NUMBER() OVER ()                        AS id,
            CAST({c_bin} AS VARCHAR)                    AS bin,
            {c_addr}                                    AS address,
            {c_boro}                                    AS borough,
            TRY_CAST({c_insp_dt} AS DATE)               AS inspection_date,
            {c_itype}                                   AS inspection_type,
            {c_result}                                  AS result,
            {c_vdesc}                                   AS violation_description,
            {c_certno}                                  AS certificate_number,
            TRY_CAST({c_expdt} AS DATE)                 AS expiration_date,
            -- APPROVAL = pass; NOT APPROVAL(VIO) / NOV(HOLD) / NULL = fail
            CASE
                WHEN UPPER(TRIM(CAST({c_result} AS VARCHAR))) = 'APPROVAL'
                THEN TRUE
                ELSE FALSE
            END                                         AS is_compliant
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
          AND TRIM(CAST({c_bin} AS VARCHAR)) NOT IN ('', '0')
    """)
    total = con.execute("SELECT COUNT(*) FROM fire_prevention_inspections").fetchone()[0]
    passed = con.execute(
        "SELECT COUNT(*) FROM fire_prevention_inspections WHERE is_compliant"
    ).fetchone()[0]
    failed = total - passed
    log.info(f"  OK fire_prevention_inspections: {total:,} total, "
             f"{passed:,} compliant, {failed:,} non-compliant")


def load_elevators(con):
    """
    Handles BOTH the original DOB_NOW__Build_Elevator_Device_Details.csv
    (UPPERCASE column names, JOB_FILING_NUMBER, DEVICE_ID etc.) and
    the normalised elevators.csv (mixed case).
    """
    cfg = DATASETS["elevators"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP elevators: file not found")
        return
    log.info(f"  Loading Elevators from {fp.name}")
    cols = _read_csv_auto(con, fp)
    # DOB_NOW uses JOB_FILING_NUMBER + DEVICE_ID as primary keys
    c_bin    = col(cols, "bin", "BIN")
    c_job    = col(cols, "job_filing_number", "JOB_FILING_NUMBER")
    c_devid  = col(cols, "device_id", "DEVICE_ID")
    c_devnum = col(cols, "devicenumber", "DeviceNumber", "DEVICE_NUMBER", "device_number")
    c_devtyp = col(cols, "device_type", "DEVICE_TYPE", "devicetype", "DeviceType")
    c_devst  = col(cols, "device_status", "DEVICE_STATUS", "devicestatus", "DeviceStatus",
                   "status", "STATUS")
    c_ffrm   = col(cols, "floorfrom", "FloorFrom", "FLOOR_FROM", "floor_from")
    c_fto    = col(cols, "floorto", "FloorTo", "FLOOR_TO", "floor_to")
    c_speed  = col(cols, "speed", "Speed", "SPEED")
    c_cap    = col(cols, "capacity", "Capacity", "CAPACITY")
    c_appr   = col(cols, "approvaldate", "ApprovalDate", "APPROVAL_DATE", "approval_date")
    con.execute("DELETE FROM elevators")
    con.execute(f"""
        INSERT INTO elevators
        SELECT
            ROW_NUMBER() OVER ()                AS id,
            CAST({c_bin} AS VARCHAR)            AS bin,
            {c_job}                             AS job_filing_number,
            {c_devid}                           AS device_id,
            {c_devnum}                          AS device_number,
            {c_devtyp}                          AS device_type,
            COALESCE({c_devst}, 'UNKNOWN')      AS device_status,
            {c_ffrm}                            AS floor_from,
            {c_fto}                             AS floor_to,
            {c_speed}                           AS speed,
            {c_cap}                             AS capacity,
            TRY_CAST({c_appr} AS DATE)          AS approval_date,
            COALESCE({c_devst}, 'UNKNOWN')      AS status
        FROM {read_auto(fp)}
        WHERE {c_bin} IS NOT NULL
    """)
    total = con.execute("SELECT COUNT(*) FROM elevators").fetchone()[0]
    oos   = con.execute(
        "SELECT COUNT(*) FROM elevators WHERE status != 'ACTIVE'"
    ).fetchone()[0]
    log.info(f"  OK elevators: {total:,} devices, {oos:,} not active")


def load_fire_hydrants(con):
    cfg = DATASETS["fire_hydrants"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP fire_hydrants: file not found")
        return
    log.info(f"  Loading Fire Hydrants from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_lat   = col(cols, "latitude", "LATITUDE", "Latitude")
    c_lon   = col(cols, "longitude", "LONGITUDE", "Longitude")
    c_uid   = col(cols, "unitid", "UNITID", "UnitID")
    c_boro  = col(cols, "borough", "BOROUGH", "Borough")
    con.execute("DELETE FROM fire_hydrants")
    con.execute(f"""
        INSERT INTO fire_hydrants
        SELECT
            ROW_NUMBER() OVER ()        AS id,
            TRY_CAST({c_lat} AS DOUBLE) AS latitude,
            TRY_CAST({c_lon} AS DOUBLE) AS longitude,
            {c_uid}                     AS unitid,
            {c_boro}                    AS borough
        FROM {read_auto(fp)}
        WHERE {c_lat} IS NOT NULL AND {c_lon} IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM fire_hydrants").fetchone()[0]
    log.info(f"  OK fire_hydrants: {n:,} locations")


def load_facilities(con):
    """
    Loads the full NYC Facilities Database (Facilities_Database.csv).
    This replaces the narrower hospitals.csv load when available and is
    a superset: schools, firehouses, hospitals, parks, etc.
    Columns confirmed from the CSV report: uid, facname, addressnum,
    streetname, address, city, boro, borocode, zipcode, latitude, longitude
    plus factype, facsubgrp, opname, opabbrev, captype, optype.
    """
    cfg = DATASETS["facilities"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP facilities: file not found")
        return
    log.info(f"  Loading Facilities Database from {fp.name}")
    cols = _read_csv_auto(con, fp)
    c_uid    = col(cols, "uid")
    c_name   = col(cols, "facname")
    c_addrn  = col(cols, "addressnum")
    c_street = col(cols, "streetname")
    c_addr   = col(cols, "address")
    c_city   = col(cols, "city")
    c_boro   = col(cols, "boro", "borough")
    c_boroc  = col(cols, "borocode")
    c_zip    = col(cols, "zipcode")
    c_factyp = col(cols, "factype")
    c_facsgr = col(cols, "facsubgrp")
    c_opname = col(cols, "opname")
    c_lat    = col(cols, "latitude")
    c_lon    = col(cols, "longitude")
    con.execute("DELETE FROM facilities")
    con.execute(f"""
        INSERT INTO facilities
        SELECT
            {c_uid}                         AS uid,
            {c_name}                        AS facname,
            {c_addrn}                       AS addressnum,
            {c_street}                      AS streetname,
            COALESCE({c_addr},
                TRIM(CONCAT(
                    COALESCE({c_addrn},''), ' ',
                    COALESCE({c_street},'')
                )))                         AS address,
            {c_city}                        AS city,
            {c_boro}                        AS borough,
            {c_boroc}                       AS borocode,
            CAST({c_zip} AS VARCHAR)        AS zipcode,
            {c_factyp}                      AS factype,
            {c_facsgr}                      AS facsubgrp,
            {c_opname}                      AS opname,
            TRY_CAST({c_lat} AS DOUBLE)     AS latitude,
            TRY_CAST({c_lon} AS DOUBLE)     AS longitude
        FROM {read_auto(fp)}
        WHERE {c_lat} IS NOT NULL
          AND TRY_CAST({c_lat} AS DOUBLE) IS NOT NULL
          AND TRY_CAST({c_lat} AS DOUBLE) != 0.0
    """)
    total = con.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]
    log.info(f"  OK facilities: {total:,} records")

    # Populate hospitals table from facilities as a convenience view
    _populate_hospitals_from_facilities(con)


def _populate_hospitals_from_facilities(con):
    """
    Seed the hospitals table from the already-loaded facilities table.
    Falls back to load_hospitals_standalone() if the facilities table is empty.

    FIX: added parentheses around the OR block so the AND lat/lon guards
    apply to ALL three OR branches, not just the last one.
    """
    con.execute("DELETE FROM hospitals")
    con.execute("""
                INSERT INTO hospitals (facility_name, facility_type, borough,
                                       address, phone, latitude, longitude)
                SELECT facname,
                       COALESCE(factype, facsubgrp) AS facility_type,
                       borough,
                       address,
                       NULL                         AS phone,
                       latitude,
                       longitude
                FROM facilities
                WHERE (
                    -- ← parentheses here are the fix; original had no parens
                    UPPER(COALESCE(facsubgrp, '')) LIKE '%HOSPITAL%'
                        OR UPPER(COALESCE(factype, '')) LIKE '%HOSPITAL%'
                        OR UPPER(COALESCE(facsubgrp, '')) LIKE '%HEALTH%'
                    )
                  AND latitude IS NOT NULL
                  AND latitude != 0.0
          AND longitude IS NOT NULL AND longitude != 0.0
                """)
    n = con.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
    log.info(f"  OK hospitals (from facilities): {n:,} facilities")


def load_hospitals_standalone(con):
    """
    Load hospitals.csv directly when Facilities_Database.csv is absent.

    FIX: hospitals.csv has the SAME schema as Facilities_Database.csv
    (38 columns: uid, facname, boro, facsubgrp, factype, opname, latitude …).
    The original code looked for 'facility_name' / 'facility_type' columns
    that do not exist → 0 rows loaded.  Now uses the real column names.
    """
    cfg = DATASETS["hospitals"]
    fp = _find_source_file(cfg)
    if not fp:
        log.info("  SKIP hospitals: file not found (will use facilities table instead)")
        return
    log.info(f"  Loading Hospitals standalone from {fp.name}")
    cols = _read_csv_auto(con, fp)

    # hospitals.csv uses the Facilities DB schema — real column names:
    c_name = col(cols, "facname", "FacName", "facility_name", "name")
    # factype is the detailed type; facsubgrp is the group (e.g. 'HOSPITALS AND CLINICS')
    c_ftype = col(cols, "factype", "FacType", "facility_type")
    c_fsubg = col(cols, "facsubgrp", "FacSubGrp")
    c_boro = col(cols, "boro", "Borough", "borough")
    c_addr = col(cols, "address", "Address")
    c_lat = col(cols, "latitude", "Latitude")
    c_lon = col(cols, "longitude", "Longitude")

    con.execute("DELETE FROM hospitals")
    con.execute(f"""
        INSERT INTO hospitals (facility_name, facility_type, borough,
                               address, phone, latitude, longitude)
        SELECT
            {c_name}                        AS facility_name,
            COALESCE({c_ftype}, {c_fsubg})  AS facility_type,
            {c_boro}                        AS borough,
            {c_addr}                        AS address,
            NULL                            AS phone,
            TRY_CAST({c_lat} AS DOUBLE)     AS latitude,
            TRY_CAST({c_lon} AS DOUBLE)     AS longitude
        FROM {read_auto(fp)}
        WHERE TRY_CAST({c_lat} AS DOUBLE) IS NOT NULL
          AND TRY_CAST({c_lat} AS DOUBLE) != 0.0
          AND TRY_CAST({c_lon} AS DOUBLE) IS NOT NULL
          AND TRY_CAST({c_lon} AS DOUBLE) != 0.0
    """)
    n = con.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
    log.info(f"  OK hospitals standalone: {n:,} records")


def populate_building_bins(con):
    """Cross-walk BBL -> BIN using HPD violations, HPD complaints, and registrations."""
    log.info("  Building BBL->BIN crosswalk...")
    sources = []
    for fname_base, priority in [
        ("hpd_violations", 1),
        ("Housing_Maintenance_Code_Violations", 1),
        ("hpd_complaints", 2),
        ("Housing_Maintenance_Code_Complaints_and_Problems", 2),
        ("registrations", 3),
        ("Multiple_Dwelling_Registrations", 3),
    ]:
        for d in [RAW_DIR, DATA_DIR]:
            for ext in [".parquet", ".csv"]:
                p = d / (fname_base + ext)
                if p.exists():
                    sources.append((p, priority))
                    break
            else:
                continue
            break

    if not sources:
        log.warning("  No crosswalk source files found, skipping BBL->BIN fill")
        return

    union_parts = []
    for fp, priority in sources:
        reader = read_auto(fp)
        # Detect actual column names (could be BBL or bbl depending on file)
        file_cols = get_columns(con, fp)
        cols_lower = {c.lower(): c for c in file_cols}
        c_bbl = cols_lower.get("bbl")
        c_bin = cols_lower.get("bin")
        if not c_bbl or not c_bin:
            log.warning(f"    Crosswalk skip {fp.name}: no bbl/bin columns")
            continue
        union_parts.append(f"""
            SELECT CAST("{c_bbl}" AS VARCHAR) AS bbl, CAST("{c_bin}" AS VARCHAR) AS bin,
                   {priority} AS src_pri, COUNT(*) AS cnt
            FROM {reader}
            WHERE "{c_bbl}" IS NOT NULL AND "{c_bin}" IS NOT NULL
              AND CAST("{c_bbl}" AS VARCHAR) NOT IN ('','0')
              AND CAST("{c_bin}" AS VARCHAR) NOT IN ('','0')
            GROUP BY 1,2
        """)

    union_sql = " UNION ALL ".join(union_parts)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _xwalk_candidates AS {union_sql}")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _xwalk AS
        SELECT bbl, bin FROM (
            SELECT bbl, bin,
                   ROW_NUMBER() OVER (PARTITION BY bbl ORDER BY src_pri, cnt DESC, bin) AS rn
            FROM _xwalk_candidates
        ) WHERE rn = 1
    """)
    con.execute("""
        UPDATE buildings SET bin = x.bin
        FROM _xwalk x
        WHERE buildings.bbl = x.bbl
          AND (buildings.bin IS NULL OR buildings.bin IN ('','0'))
    """)
    filled = con.execute(
        "SELECT COUNT(*) FROM buildings WHERE bin IS NOT NULL AND bin NOT IN ('','0')"
    ).fetchone()[0]
    log.info(f"  OK crosswalk: {filled:,} buildings have BIN")


def load_all(con):
    log.info("=" * 60)
    log.info("PHASE 1: Loading all datasets into DuckDB")
    log.info("=" * 60)
    init_schema(con)
    load_pluto(con)
    load_registrations(con)
    load_registration_contacts(con)
    load_dob_violations(con)
    load_dob_safety_violations(con)
    load_dob_ecb_violations(con)
    load_hpd_violations(con)
    load_hpd_complaints(con)
    load_fire_incidents(con)
    load_fire_company_incidents(con)
    load_ems_incidents(con)
    load_311(con)
    load_nypd_complaints(con)
    load_fire_prevention_inspections(con)
    load_elevators(con)
    load_fire_hydrants(con)
    # Facilities covers hospitals; fall back to standalone hospitals.csv
    fac_fp = _find_source_file(DATASETS["facilities"])
    if fac_fp:
        load_facilities(con)
    else:
        load_hospitals_standalone(con)
    populate_building_bins(con)

    # Summary
    log.info("")
    log.info("DATABASE SUMMARY")
    log.info("-" * 40)
    for (table,) in con.execute("SHOW TABLES").fetchall():
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log.info(f"  {table:<40} {n:>12,} rows")
        except Exception:
            log.info(f"  {table:<40} {'(empty)':>12}")
    db_mb = DB_PATH.stat().st_size / 1e6 if DB_PATH.exists() else 0
    log.info(f"  Database size: {db_mb:.1f} MB")


# ---------------------------------------------------------------------------
# Phase 2 — Owner portfolio
# ---------------------------------------------------------------------------

def compute_owner_portfolio(con):
    log.info("=" * 60)
    log.info("PHASE 2: Computing owner portfolio analysis")
    log.info("=" * 60)
    con.execute("DELETE FROM owner_portfolio")
    con.execute("""
        INSERT INTO owner_portfolio
        SELECT
            b.owner_name,
            COUNT(DISTINCT b.bbl)                                       AS total_buildings,
            COUNT(DISTINCT dv.id) FILTER (WHERE dv.is_active)           AS total_open_violations,
            COUNT(DISTINCT hv.violation_id)
                FILTER (WHERE hv.violation_class = 'C'
                          AND hv.current_status != 'CLOSE')             AS total_class_c_violations,
            COALESCE(SUM(ecb.balance_due), 0)                           AS total_ecb_balance_due,
            COALESCE(
                COUNT(DISTINCT dv.id) FILTER (WHERE dv.is_active)
                    / NULLIF(COUNT(DISTINCT b.bbl), 0)
            , 0)                                                        AS avg_violations_per_building,
            STRING_AGG(DISTINCT b.bin, ',')                             AS bins
        FROM buildings b
        LEFT JOIN dob_violations dv       ON b.bin = dv.bin
        LEFT JOIN hpd_violations hv       ON b.bin = hv.bin
        LEFT JOIN dob_ecb_violations ecb  ON b.bin = ecb.bin AND ecb.is_active
        WHERE b.owner_name IS NOT NULL
        GROUP BY b.owner_name
        HAVING COUNT(DISTINCT b.bbl) > 0
    """)
    total   = con.execute("SELECT COUNT(*) FROM owner_portfolio").fetchone()[0]
    neglect = con.execute("""
        SELECT COUNT(*) FROM owner_portfolio
        WHERE total_class_c_violations > 5 OR total_ecb_balance_due > 50000
    """).fetchone()[0]
    log.info(f"  OK: {total:,} owners, {neglect:,} high-neglect")


# ---------------------------------------------------------------------------
# Phase 3 — Risk scores
# ---------------------------------------------------------------------------

def compute_risk_scores(con):
    log.info("=" * 60)
    log.info("PHASE 3: Computing building risk scores")
    log.info("=" * 60)
    con.execute("DELETE FROM building_risk_scores")
    con.execute("""
        INSERT INTO building_risk_scores (bbl, bin, overall_risk_score)
        SELECT bbl, bin, 0.0 FROM buildings
    """)

    risk_steps = [
        ("DOB active violations", """
            UPDATE building_risk_scores SET active_dob_violations = s.cnt
            FROM (SELECT bin, COUNT(*) AS cnt FROM dob_violations WHERE is_active GROUP BY bin) s
            WHERE building_risk_scores.bin = s.bin
        """),
        ("ECB active violations", """
            UPDATE building_risk_scores SET active_ecb_violations = s.cnt
            FROM (SELECT bin, COUNT(*) AS cnt FROM dob_ecb_violations WHERE is_active GROUP BY bin) s
            WHERE building_risk_scores.bin = s.bin
        """),
        ("HPD Class C open", """
            UPDATE building_risk_scores SET active_hpd_class_c = s.cnt
            FROM (SELECT bin, COUNT(*) AS cnt FROM hpd_violations
                  WHERE violation_class = 'C' AND current_status != 'CLOSE' GROUP BY bin) s
            WHERE building_risk_scores.bin = s.bin
        """),
        ("HPD Class B open", """
            UPDATE building_risk_scores SET active_hpd_class_b = s.cnt
            FROM (SELECT bin, COUNT(*) AS cnt FROM hpd_violations
                  WHERE violation_class = 'B' AND current_status != 'CLOSE' GROUP BY bin) s
            WHERE building_risk_scores.bin = s.bin
        """),
        ("Prior fire incidents", """
            UPDATE building_risk_scores SET prior_fire_incidents = s.cnt
            FROM (
                SELECT b.bin, COUNT(*) AS cnt
                FROM buildings b
                JOIN fire_incidents f
                  ON (b.bin = f.bin AND f.bin IS NOT NULL)
                  OR (f.bin IS NULL
                      AND f.latitude IS NOT NULL
                      AND ABS(b.latitude - f.latitude) < 0.0005
                      AND ABS(b.longitude - f.longitude) < 0.0005)
                WHERE b.bin IS NOT NULL
                GROUP BY b.bin
            ) s
            WHERE building_risk_scores.bin = s.bin
        """),
        ("EMS proximity", """
            UPDATE building_risk_scores SET prior_ems_incidents = s.cnt
            FROM (
                SELECT b.bbl, COUNT(*) AS cnt FROM buildings b
                JOIN ems_incidents e
                  ON ABS(b.latitude - e.latitude)  < 0.0005
                 AND ABS(b.longitude - e.longitude) < 0.0005
                GROUP BY b.bbl
            ) s WHERE building_risk_scores.bbl = s.bbl
        """),
        ("NYPD proximity", """
            UPDATE building_risk_scores SET nearby_nypd_incidents = s.cnt
            FROM (
                SELECT b.bbl, COUNT(*) AS cnt FROM buildings b
                JOIN nypd_complaints n
                  ON ABS(b.latitude - n.latitude)  < 0.0010
                 AND ABS(b.longitude - n.longitude) < 0.0010
                GROUP BY b.bbl
            ) s WHERE building_risk_scores.bbl = s.bbl
        """),
        ("311 velocity 30d", """
            UPDATE building_risk_scores SET complaint_velocity_30d = s.cnt
            FROM (
                SELECT b.bbl, COUNT(*) AS cnt FROM buildings b
                JOIN service_requests_311 sr
                  ON LOWER(b.address) = LOWER(sr.incident_address)
                WHERE sr.created_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY b.bbl
            ) s WHERE building_risk_scores.bbl = s.bbl
        """),
        ("311 velocity 90d", """
            UPDATE building_risk_scores SET complaint_velocity_90d = s.cnt
            FROM (
                SELECT b.bbl, COUNT(*) AS cnt FROM buildings b
                JOIN service_requests_311 sr
                  ON LOWER(b.address) = LOWER(sr.incident_address)
                WHERE sr.created_date >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY b.bbl
            ) s WHERE building_risk_scores.bbl = s.bbl
        """),
        ("FDNY inspection compliance", """
            UPDATE building_risk_scores SET last_fdny_inspection_pass = s.pass
            FROM (SELECT bin, BOOL_OR(is_compliant) AS pass
                  FROM fire_prevention_inspections GROUP BY bin) s
            WHERE building_risk_scores.bin = s.bin
        """),
        ("Elevator status", """
            UPDATE building_risk_scores
            SET elevator_count = s.total, elevator_out_of_service = s.oos
            FROM (
                SELECT bin, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status != 'ACTIVE') AS oos
                FROM elevators GROUP BY bin
            ) s WHERE building_risk_scores.bin = s.bin
        """),
        ("Nearest hydrant", """
            UPDATE building_risk_scores SET nearest_hydrant_ft = s.dist_ft
            FROM (
                SELECT b.bbl,
                    MIN(SQRT(
                        POW((b.latitude  - h.latitude)  * 364000, 2) +
                        POW((b.longitude - h.longitude) * 288200, 2)
                    )) AS dist_ft
                FROM buildings b, fire_hydrants h
                WHERE ABS(b.latitude  - h.latitude)  < 0.005
                  AND ABS(b.longitude - h.longitude) < 0.005
                GROUP BY b.bbl
            ) s WHERE building_risk_scores.bbl = s.bbl
        """),
        ("Nearest hospital", """
            UPDATE building_risk_scores
            SET nearest_hospital = s.name, nearest_hospital_mi = s.dist_mi
            FROM (
                SELECT b.bbl,
                    ARG_MIN(h.facility_name,
                        SQRT(POW((b.latitude  - h.latitude)  * 69.0, 2) +
                             POW((b.longitude - h.longitude) * 54.6, 2))
                    ) AS name,
                    MIN(SQRT(POW((b.latitude  - h.latitude)  * 69.0, 2) +
                             POW((b.longitude - h.longitude) * 54.6, 2))) AS dist_mi
                FROM buildings b, hospitals h GROUP BY b.bbl
            ) s WHERE building_risk_scores.bbl = s.bbl
        """),
        ("Composite score", """
            UPDATE building_risk_scores SET overall_risk_score =
                COALESCE(active_hpd_class_c,      0) * 6.0  +
                COALESCE(prior_fire_incidents,    0) * 4.0  +
                COALESCE(active_ecb_violations,   0) * 3.0  +
                COALESCE(active_dob_violations,   0) * 2.5  +
                COALESCE(active_hpd_class_b,      0) * 2.0  +
                COALESCE(complaint_velocity_30d,  0) * 2.0  +
                COALESCE(prior_ems_incidents,     0) * 1.5  +
                COALESCE(nearby_nypd_incidents,   0) * 0.75 +
                COALESCE(complaint_velocity_90d,  0) * 1.0  +
                CASE WHEN last_fdny_inspection_pass = FALSE THEN 15.0 ELSE 0.0 END +
                CASE WHEN elevator_out_of_service > 0       THEN  5.0 ELSE 0.0 END
        """),
    ]

    for label, sql in risk_steps:
        log.info(f"  -> {label}...")
        try:
            con.execute(sql)
        except Exception as e:
            log.warning(f"     Risk step failed (non-fatal): {e}")

    # Push scores back to buildings table
    con.execute("""
        UPDATE buildings SET risk_score = brs.overall_risk_score
        FROM building_risk_scores brs WHERE buildings.bbl = brs.bbl
    """)

    scored    = con.execute(
        "SELECT COUNT(*) FROM building_risk_scores WHERE overall_risk_score > 0"
    ).fetchone()[0]
    high_risk = con.execute(
        "SELECT COUNT(*) FROM building_risk_scores WHERE overall_risk_score > 30"
    ).fetchone()[0]
    critical  = con.execute(
        "SELECT COUNT(*) FROM building_risk_scores WHERE overall_risk_score > 60"
    ).fetchone()[0]
    log.info(f"  OK scored: {scored:,} buildings")
    log.info(f"     Yellow  (>30): {high_risk:,}")
    log.info(f"     Red     (>60): {critical:,}")


# ---------------------------------------------------------------------------
# Phase 4 — ChromaDB vector embeddings (optional)
# ---------------------------------------------------------------------------

def build_doc_text(row: dict) -> str:
    parts = [str(row.get("complaint_type", "Unknown"))]
    borough = str(row.get("borough", ""))
    if borough and borough not in ("UNKNOWN", "None", "nan"):
        parts[0] += f" in {borough}"
    desc = str(row.get("descriptor", ""))
    if desc and desc not in ("", "None", "nan"):
        parts.append(desc[:200])
    res = row.get("resolution_days", -1)
    try:
        res = float(res)
    except (TypeError, ValueError):
        res = -1.0
    if res > 0:
        parts.append(f"resolved in {res:.1f} days")
    return " — ".join(parts)


def populate_chromadb(con) -> int:
    if not _CHROMA_OK:
        log.warning("ChromaDB / sentence-transformers not installed; skipping vector phase")
        log.warning("  pip install chromadb sentence-transformers torch --break-system-packages")
        return 0

    log.info("=" * 60)
    log.info("PHASE 4: Embedding incidents into ChromaDB")
    log.info("=" * 60)

    # Build a unified incidents view from 311 + EMS + fire + NYPD
    # Build a unified incidents view from a smaller recent subset
    try:
        con.execute("""
            CREATE OR REPLACE TEMP VIEW _incidents_unified AS
            SELECT *
            FROM (
                SELECT
                    unique_key AS id,
                    complaint_type,
                    descriptor,
                    borough,
                    agency,
                    CAST(created_date AS VARCHAR) AS created_date,
                    DATEDIFF('day', created_date, closed_date) AS resolution_days,
                    'SR311' AS source
                FROM service_requests_311
                WHERE created_date >= DATE '2024-01-01'
                LIMIT 30000
            )

            UNION ALL

            SELECT *
            FROM (
                SELECT
                    incident_id AS id,
                    incident_type_desc AS complaint_type,
                    incident_classification AS descriptor,
                    incident_borough AS borough,
                    'FDNY' AS agency,
                    CAST(incident_datetime AS VARCHAR) AS created_date,
                    -1 AS resolution_days,
                    'FIRE' AS source
                FROM fire_incidents
                WHERE incident_datetime >= TIMESTAMP '2024-01-01 00:00:00'
                LIMIT 20000
            )

            UNION ALL

            SELECT *
            FROM (
                SELECT
                    complaint_number AS id,
                    offense_description AS complaint_type,
                    law_category AS descriptor,
                    borough,
                    'NYPD' AS agency,
                    CAST(complaint_date AS VARCHAR) AS created_date,
                    -1 AS resolution_days,
                    'NYPD' AS source
                FROM nypd_complaints
                WHERE complaint_date >= DATE '2024-01-01'
                LIMIT 10000
            )
        """)
    except Exception as e:
        log.error(f"  Failed to build unified incidents view: {e}")
        return 0

    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    except Exception as e:
        log.error(f"  ChromaDB init failed: {e}")
        return 0

    try:
        client.delete_collection("nyc_incidents")
    except Exception:
        pass
    collection = client.create_collection(
        name="nyc_incidents",
        metadata={"hnsw:space": "cosine"},
    )

    model = SentenceTransformer(EMBED_MODEL)
    total_rows = con.execute("SELECT COUNT(*) FROM _incidents_unified").fetchone()[0]
    log.info(f"  Embedding {total_rows:,} rows (batch {BATCH_SIZE})")

    offset, batch_num, embedded = 0, 0, 0
    seen: set = set()
    t0 = time.time()

    while offset < total_rows:
        rows = con.execute(f"""
            SELECT id, complaint_type, descriptor, borough, agency,
                   created_date, resolution_days, source
            FROM _incidents_unified LIMIT {BATCH_SIZE} OFFSET {offset}
        """).fetchall()
        if not rows:
            break

        keys = ["id", "complaint_type", "descriptor", "borough", "agency",
                "created_date", "resolution_days", "source"]
        batch = [dict(zip(keys, r)) for r in rows]

        docs, ids, metas = [], [], []
        for i, d in enumerate(batch):
            uid = hashlib.md5(
                f"{d.get('source','')}_{d.get('id','')}_{offset}_{i}".encode()
            ).hexdigest()[:16]
            if uid in seen:
                continue
            seen.add(uid)
            docs.append(build_doc_text(d))
            ids.append(uid)
            res = d.get("resolution_days", -1)
            try:
                res = float(res)
            except (TypeError, ValueError):
                res = -1.0
            metas.append({
                "source": str(d.get("source", "")),
                "borough": str(d.get("borough", "")),
                "agency": str(d.get("agency", "")),
                "complaint_type": str(d.get("complaint_type", "")),
                "resolution_days": res,
                "date": str(d.get("created_date", "")),
            })

        if docs:
            try:
                emb = model.encode(docs, batch_size=BATCH_SIZE,
                                   show_progress_bar=False).tolist()
                collection.add(ids=ids, documents=docs, embeddings=emb, metadatas=metas)
                embedded += len(ids)
            except Exception as e:
                log.warning(f"  Batch {batch_num} failed: {e}")

        batch_num += 1
        offset += BATCH_SIZE
        if batch_num % 100 == 0:
            pct = min(100.0, offset / total_rows * 100)
            rate = embedded / (time.time() - t0)
            log.info(f"  [{pct:5.1f}%] {embedded:,}/{total_rows:,} ({rate:.0f}/s)")

    elapsed = time.time() - t0
    log.info(f"  Done: {embedded:,} docs in {elapsed:.0f}s")
    return embedded


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NYC First Responder Dispatch — Unified Data Ingestion Pipeline"
    )
    parser.add_argument("--download",  action="store_true",
                        help="Download all datasets from NYC Open Data (Socrata)")
    parser.add_argument("--load",      action="store_true",
                        help="Load all CSV/parquet files into DuckDB")
    parser.add_argument("--portfolio", action="store_true",
                        help="Compute owner portfolio metrics")
    parser.add_argument("--risk",      action="store_true",
                        help="Compute per-building risk scores")
    parser.add_argument("--embed",     action="store_true",
                        help="Embed incidents into ChromaDB (requires sentence-transformers)")
    parser.add_argument("--all",       action="store_true",
                        help="Run the full pipeline end-to-end")
    parser.add_argument("--data-dir",  default=str(RAW_DIR),
                        help=f"Directory for raw data files (default: {RAW_DIR})")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(1)

    # Override data dir if passed
    _raw = Path(args.data_dir)
    _raw.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    log.info("=" * 60)
    log.info("NYC First Responder Dispatch — Unified Ingestion Pipeline")
    log.info("=" * 60)
    log.info(f"Data dir : {_raw}")
    log.info(f"DB path  : {DB_PATH}")

    if args.download or args.all:
        download_all(_raw)

    if args.load or args.all:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(DB_PATH))
        load_all(con)
        con.close()

    if args.portfolio or args.all:
        con = duckdb.connect(str(DB_PATH))
        compute_owner_portfolio(con)
        con.close()

    if args.risk or args.all:
        con = duckdb.connect(str(DB_PATH))
        compute_risk_scores(con)
        con.close()

    if args.embed or args.all:
        con = duckdb.connect(str(DB_PATH))
        populate_chromadb(con)
        con.close()

    elapsed = time.time() - t0
    log.info("")
    log.info("=" * 60)
    log.info(f"DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    log.info(f"  Database : {DB_PATH}")
    log.info(f"  ChromaDB : {CHROMA_DIR}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()