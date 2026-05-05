#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# NYC Open Data Download Script
# Downloads all required CSV files for the Dispatch Intelligence system.
# Run ONCE before ingestion. Files are large (1-3GB each).
# ============================================================================

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${CYAN}[DATA]${NC} $1"; }
ok()   { echo -e "${GREEN}[ OK ]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$(dirname "$SCRIPT_DIR")/data"
mkdir -p "$DATA_DIR"

download() {
    local url="$1"
    local filename="$2"
    local limit="${3:-2000000}"

    if [ -f "$DATA_DIR/$filename" ]; then
        local size=$(du -h "$DATA_DIR/$filename" | cut -f1)
        ok "$filename already exists ($size). Skipping."
        return
    fi

    log "Downloading $filename ($limit rows)..."
    curl -L --progress-bar \
        "${url}?\$limit=${limit}" \
        -o "$DATA_DIR/$filename"

    if [ -f "$DATA_DIR/$filename" ]; then
        local size=$(du -h "$DATA_DIR/$filename" | cut -f1)
        ok "$filename downloaded ($size)"
    else
        warn "Failed to download $filename"
    fi
}

echo ""
log "NYC Open Data Download — First Responder Dispatch"
log "Target directory: $DATA_DIR"
echo ""

# 311 Service Requests (primary data source)
download \
    "https://data.cityofnewyork.us/api/views/erm2-nwe9/rows.csv?accessType=DOWNLOAD" \
    "311_Service_Requests_from_2020_to_Present.csv" \
    "2000000"

# EMS Incident Dispatch
download \
    "https://data.cityofnewyork.us/api/views/76xm-jjuj/rows.csv?accessType=DOWNLOAD" \
    "EMS_Incident_Dispatch_Data.csv" \
    "1000000"

# Fire Incident Dispatch
download \
    "https://data.cityofnewyork.us/api/views/8m42-w767/rows.csv?accessType=DOWNLOAD" \
    "Fire_Incident_Dispatch_Data.csv" \
    "1000000"

# NYPD Complaint Data (Year to Date)
download \
    "https://data.cityofnewyork.us/api/views/5uac-w243/rows.csv?accessType=DOWNLOAD" \
    "NYPD_Complaint_Data_Current__Year_To_Date_.csv" \
    "500000"

# Facilities Database (hospitals, fire stations, precincts)
download \
    "https://data.cityofnewyork.us/api/views/ji82-xba5/rows.csv?accessType=DOWNLOAD" \
    "Facilities_Database.csv" \
    "100000"

echo ""
log "Download complete. Files in: $DATA_DIR"
ls -lh "$DATA_DIR"/*.csv 2>/dev/null || warn "No CSV files found."
echo ""
log "Next step: python scripts/ingest.py"
