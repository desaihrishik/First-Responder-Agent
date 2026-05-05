#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# NYC First Responder Dispatch — System Warmup Script
# Run this before demo. Ensures all models are loaded and warm.
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[WARMUP]${NC} $1"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $1"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $1"; }
fail() { echo -e "${RED}[ FAIL ]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ============================================================================
# Step 1: Verify GPU
# ============================================================================
log "Checking GPU..."
if command -v nvidia-smi &>/dev/null; then
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    ok "GPU: ${GPU_NAME} | Memory: ${GPU_MEM}MB"
else
    warn "nvidia-smi not found. Proceeding without GPU verification."
fi

# ============================================================================
# Step 2: Start Ollama (if not running)
# ============================================================================
log "Checking Ollama..."
if ! pgrep -x "ollama" &>/dev/null; then
    log "Starting Ollama server..."
    ollama serve &>/dev/null &
    sleep 3
fi

if curl -s http://localhost:11434/api/version &>/dev/null; then
    OLLAMA_VERSION=$(curl -s http://localhost:11434/api/version | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','unknown'))" 2>/dev/null || echo "unknown")
    ok "Ollama running (version: ${OLLAMA_VERSION})"
else
    fail "Ollama not responding at localhost:11434"
    exit 1
fi

# ============================================================================
# Step 3: Pull models if not present
# ============================================================================
log "Checking models..."

pull_if_missing() {
    local model="$1"
    if ollama list 2>/dev/null | grep -q "$model"; then
        ok "Model already available: $model"
    else
        log "Pulling $model (this may take a few minutes)..."
        ollama pull "$model"
        ok "Pulled: $model"
    fi
}

pull_if_missing "nemotron-mini"
pull_if_missing "llava:13b"

# ============================================================================
# Step 4: Warmup inference — one query per model
# ============================================================================
log "Warming up Nemotron Nano..."
NEMOTRON_START=$(date +%s%N)
curl -s http://localhost:11434/api/chat \
    -d '{"model":"nemotron-mini","messages":[{"role":"user","content":"test"}],"stream":false,"options":{"num_predict":5}}' \
    > /dev/null 2>&1
NEMOTRON_END=$(date +%s%N)
NEMOTRON_MS=$(( (NEMOTRON_END - NEMOTRON_START) / 1000000 ))
ok "Nemotron Nano warm: ${NEMOTRON_MS}ms"

log "Warming up LLaVA 13B..."
LLAVA_START=$(date +%s%N)
curl -s http://localhost:11434/api/chat \
    -d '{"model":"llava:13b","messages":[{"role":"user","content":"test"}],"stream":false,"options":{"num_predict":5}}' \
    > /dev/null 2>&1
LLAVA_END=$(date +%s%N)
LLAVA_MS=$(( (LLAVA_END - LLAVA_START) / 1000000 ))
ok "LLaVA 13B warm: ${LLAVA_MS}ms"

# ============================================================================
# Step 5: Verify data files
# ============================================================================
log "Checking data files..."
if [ -f "data/responder.duckdb" ]; then
    DB_SIZE=$(du -h data/responder.duckdb | cut -f1)
    ok "DuckDB database: ${DB_SIZE}"
else
    warn "DuckDB not found. Run: python scripts/ingest.py"
fi

if [ -d "data/chromadb" ]; then
    CHROMA_SIZE=$(du -sh data/chromadb | cut -f1)
    ok "ChromaDB store: ${CHROMA_SIZE}"
else
    warn "ChromaDB not found. Run: python scripts/ingest.py"
fi

# ============================================================================
# Step 6: Start FastAPI server
# ============================================================================
log "Starting FastAPI server..."
mkdir -p logs

# Kill existing server if running
pkill -f "uvicorn src.api.main:app" 2>/dev/null || true
sleep 1

python3 -m uvicorn src.api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info &
API_PID=$!

log "Waiting for API to start (PID: $API_PID)..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/health &>/dev/null; then
        break
    fi
    sleep 1
done

if curl -s http://localhost:8000/health &>/dev/null; then
    HEALTH=$(curl -s http://localhost:8000/health)
    ok "API server healthy at http://localhost:8000"
else
    fail "API failed to start within 30 seconds"
    exit 1
fi

# ============================================================================
# Step 7: Run one full triage query
# ============================================================================
log "Running warmup triage query..."
TRIAGE_START=$(date +%s%N)
TRIAGE_RESULT=$(curl -s -w "\n%{http_code}" http://localhost:8000/triage \
    -H "Content-Type: application/json" \
    -d '{"text":"Test warmup query: noise complaint from residential building","borough":"Manhattan"}')
TRIAGE_END=$(date +%s%N)
TRIAGE_MS=$(( (TRIAGE_END - TRIAGE_START) / 1000000 ))

HTTP_CODE=$(echo "$TRIAGE_RESULT" | tail -1)
if [ "$HTTP_CODE" = "200" ]; then
    ok "Triage warmup complete: ${TRIAGE_MS}ms (HTTP ${HTTP_CODE})"
else
    warn "Triage warmup returned HTTP ${HTTP_CODE}"
fi

# ============================================================================
# Done
# ============================================================================
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  System ready. All models warm.${NC}"
echo -e "${GREEN}  Nemotron warmup: ${NEMOTRON_MS}ms${NC}"
echo -e "${GREEN}  LLaVA warmup:    ${LLAVA_MS}ms${NC}"
echo -e "${GREEN}  Full triage:     ${TRIAGE_MS}ms${NC}"
echo -e "${GREEN}  API:             http://localhost:8000${NC}"
echo -e "${GREEN}  Frontend:        http://localhost:5173${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "To start the frontend:"
echo "  cd frontend && npm run dev"
echo ""
echo "To run benchmarks:"
echo "  python scripts/benchmark.py"
