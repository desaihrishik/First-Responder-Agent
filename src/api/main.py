"""
NYC First Responder Dispatch Intelligence — API
=================================================
POST /triage  — incident text + photo + location → WHO to send + severity + brief + past incidents
POST /lookup  — address/BBL → full building report (violations, inspections, risk score)
GET  /search  — address autocomplete
GET  /health  — system status
"""

import os, sys, re, json, time, logging
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

import duckdb, httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "responder.duckdb"
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("api")

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
NEMOTRON = os.getenv("NEMOTRON_MODEL", "nemotron-mini")
LLAVA = os.getenv("LLAVA_MODEL", "llava:13b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", "gpt-4.1-mini")
OPENAI_MODEL_VISION = os.getenv("OPENAI_MODEL_VISION", "gpt-4.1-mini")

_con = None
_http: Optional[httpx.AsyncClient] = None

# ── Models ───────────────────────────────────────────────────────────────────

class TriageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    image_b64: Optional[str] = None
    address: Optional[str] = None
    borough: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class LookupRequest(BaseModel):
    address: Optional[str] = None
    borough: Optional[str] = None
    bbl: Optional[str] = None
    bin: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

# ── DB helpers ───────────────────────────────────────────────────────────────

def _q(con, sql, params=None):
    try:
        r = con.execute(sql, params) if params else con.execute(sql)
        cols = [d[0] for d in r.description]
        return [{cols[i]: (v if isinstance(v, (int,float,bool,str,type(None))) else str(v))
                 for i,v in enumerate(row)} for row in r.fetchall()]
    except: return []

def _f(rows): return rows[0] if rows else None
def _has(con, t):
    try: return t in {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    except: return False

# ── Building resolver ────────────────────────────────────────────────────────

def resolve_building(con, address=None, borough=None, bbl=None, bin_val=None, lat=None, lon=None):
    if not _has(con, "buildings"): return None
    if bbl:
        r = _f(_q(con, "SELECT * FROM buildings WHERE bbl=? LIMIT 1", [bbl]))
        if r: return r
    if bin_val:
        r = _f(_q(con, "SELECT * FROM buildings WHERE bin=? LIMIT 1", [bin_val]))
        if r: return r
    if address:
        a = address.strip().upper()
        r = _f(_q(con, "SELECT * FROM buildings WHERE UPPER(address)=? LIMIT 1", [a]))
        if r: return r
        r = _f(_q(con, "SELECT * FROM buildings WHERE UPPER(address) LIKE '%'||?||'%' ORDER BY LENGTH(address) LIMIT 1", [a]))
        if r: return r
        if borough:
            r = _f(_q(con, "SELECT * FROM buildings WHERE UPPER(address) LIKE '%'||?||'%' AND UPPER(borough)=? ORDER BY LENGTH(address) LIMIT 1", [a, borough.upper()]))
            if r: return r
    if lat and lon:
        d = 500/364000.0
        r = _f(_q(con, """SELECT * FROM buildings WHERE latitude BETWEEN ?-? AND ?+?
            AND longitude BETWEEN ?-? AND ?+? AND latitude IS NOT NULL AND latitude!=0
            ORDER BY SQRT(POW((latitude-?)*364000,2)+POW((longitude-?)*288200,2)) LIMIT 1""",
            [lat,d,lat,d,lon,d,lon,d,lat,lon]))
        if r: return r
    return None

# ── Past incidents near a location ───────────────────────────────────────────

def get_past_incidents(con, bbl=None, bin_val=None, addr=None, lat=None, lon=None):
    """Get recent incidents/violations near this location for triage context."""
    past = []

    # 311 complaints at this address/BBL
    if _has(con, "service_requests_311"):
        rows = []
        if bbl:
            rows = _q(con, """SELECT complaint_type, descriptor, status, created_date, agency,
                resolution_description FROM service_requests_311 WHERE bbl=?
                ORDER BY created_date DESC LIMIT 10""", [bbl])
        if not rows and addr:
            rows = _q(con, """SELECT complaint_type, descriptor, status, created_date, agency,
                resolution_description FROM service_requests_311
                WHERE UPPER(incident_address) LIKE '%'||?||'%'
                ORDER BY created_date DESC LIMIT 10""", [addr.upper()])
        for r in rows:
            past.append({"source": "311", "type": r.get("complaint_type",""),
                "detail": r.get("descriptor",""), "date": r.get("created_date",""),
                "status": r.get("status",""), "resolution": r.get("resolution_description",""),
                "agency": r.get("agency","")})

    # HPD violations at this building
    if bin_val and _has(con, "hpd_violations"):
        for r in _q(con, """SELECT violation_class, nov_description, current_status,
            inspection_date FROM hpd_violations WHERE bin=?
            ORDER BY inspection_date DESC LIMIT 8""", [bin_val]):
            past.append({"source": "HPD Violation", "type": f"Class {r.get('violation_class','')}",
                "detail": r.get("nov_description",""), "date": r.get("inspection_date",""),
                "status": r.get("current_status","")})

    # DOB violations at this building
    if bin_val and _has(con, "dob_violations"):
        for r in _q(con, """SELECT violation_type, description, severity, is_active, issue_date
            FROM dob_violations WHERE bin=? ORDER BY issue_date DESC LIMIT 8""", [bin_val]):
            past.append({"source": "DOB Violation", "type": r.get("violation_type",""),
                "detail": r.get("description",""), "date": r.get("issue_date",""),
                "status": "ACTIVE" if r.get("is_active") else "Resolved",
                "severity": r.get("severity","")})

    # Fire incidents nearby
    if lat and lon and _has(con, "fire_incidents"):
        for r in _q(con, """SELECT incident_type_desc, incident_classification,
            incident_datetime, dispatch_response_seconds, engines_assigned
            FROM fire_incidents WHERE latitude BETWEEN ?-0.003 AND ?+0.003
            AND longitude BETWEEN ?-0.003 AND ?+0.003 AND latitude IS NOT NULL AND latitude!=0
            ORDER BY incident_datetime DESC LIMIT 8""", [lat,lat,lon,lon]):
            past.append({"source": "FDNY", "type": r.get("incident_type_desc",""),
                "detail": r.get("incident_classification",""), "date": r.get("incident_datetime",""),
                "response_sec": r.get("dispatch_response_seconds"),
                "engines": r.get("engines_assigned")})

    # EMS incidents nearby
    if lat and lon and _has(con, "ems_incidents"):
        for r in _q(con, """SELECT initial_call_type, initial_severity_level,
            incident_datetime, dispatch_response_seconds
            FROM ems_incidents WHERE latitude BETWEEN ?-0.002 AND ?+0.002
            AND longitude BETWEEN ?-0.002 AND ?+0.002 AND latitude IS NOT NULL AND latitude!=0
            ORDER BY incident_datetime DESC LIMIT 8""", [lat,lat,lon,lon]):
            past.append({"source": "EMS", "type": r.get("initial_call_type",""),
                "detail": f"Severity: {r.get('initial_severity_level','')}",
                "date": r.get("incident_datetime",""),
                "response_sec": r.get("dispatch_response_seconds")})

    # NYPD complaints nearby
    if lat and lon and _has(con, "nypd_complaints"):
        for r in _q(con, """SELECT offense_description, law_category, complaint_date,
            premises_type FROM nypd_complaints WHERE latitude BETWEEN ?-0.002 AND ?+0.002
            AND longitude BETWEEN ?-0.002 AND ?+0.002 AND latitude IS NOT NULL AND latitude!=0
            ORDER BY complaint_date DESC LIMIT 8""", [lat,lat,lon,lon]):
            past.append({"source": "NYPD", "type": r.get("offense_description",""),
                "detail": r.get("law_category",""), "date": r.get("complaint_date",""),
                "premises": r.get("premises_type","")})

    return past

# ── Nearby resources ─────────────────────────────────────────────────────────

def get_nearby_resources(con, lat, lon):
    resources = {}
    if not lat or not lon: return resources

    if _has(con, "hospitals"):
        resources["hospitals"] = _q(con, """SELECT facility_name, address, borough,
            ROUND(SQRT(POW((latitude-?)*69.0,2)+POW((longitude-?)*54.6,2)),2) AS dist_mi
            FROM hospitals WHERE latitude IS NOT NULL AND latitude!=0
            ORDER BY dist_mi LIMIT 3""", [lat,lon])

    if _has(con, "fire_hydrants"):
        resources["hydrants"] = _q(con, """SELECT unitid, borough,
            ROUND(SQRT(POW((latitude-?)*364000,2)+POW((longitude-?)*288200,2))) AS dist_ft
            FROM fire_hydrants WHERE latitude BETWEEN ?-0.005 AND ?+0.005
            AND longitude BETWEEN ?-0.005 AND ?+0.005 AND latitude IS NOT NULL AND latitude!=0
            ORDER BY dist_ft LIMIT 3""", [lat,lon,lat,lat,lon,lon])

    if _has(con, "facilities"):
        resources["facilities"] = _q(con, """SELECT facname, factype, address,
            ROUND(SQRT(POW((latitude-?)*364000,2)+POW((longitude-?)*288200,2))) AS dist_ft
            FROM facilities WHERE latitude BETWEEN ?-0.01 AND ?+0.01
            AND longitude BETWEEN ?-0.01 AND ?+0.01 AND latitude IS NOT NULL AND latitude!=0
            ORDER BY dist_ft LIMIT 5""", [lat,lon,lat,lat,lon,lon])

    return resources

# ── Ollama LLM ───────────────────────────────────────────────────────────────

async def call_llava(image_b64: str) -> str | None:
    if not _http: return None
    if OPENAI_API_KEY:
        try:
            r = await _http.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL_VISION,
                    "temperature": 0.2,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Describe this scene for 911 dispatch. Focus on visible hazards, fire/smoke, injuries, structural damage, number of people, vehicles. Under 80 words."
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                            }
                        ]
                    }],
                    "max_tokens": 140,
                },
                timeout=20.0,
            )
            if r.status_code == 200:
                return r.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            log.warning(f"OpenAI vision failed: {r.status_code} {r.text[:180]}")
        except Exception as e:
            log.warning(f"OpenAI vision failed: {e}")
    try:
        r = await _http.post(f"{OLLAMA_BASE}/api/chat", json={
            "model": LLAVA, "stream": False,
            "messages": [{"role":"user",
                "content": "Describe this scene for 911 dispatch. Focus on: visible hazards, fire/smoke, injuries, structural damage, number of people, vehicles. Under 80 words.",
                "images": [image_b64]}],
            "options": {"num_predict": 120}}, timeout=12.0)
        if r.status_code == 200:
            return r.json().get("message",{}).get("content","").strip()
    except Exception as e:
        log.warning(f"LLaVA failed: {e}")
    return None

async def call_nemotron(text: str, context: str, vision: str = None) -> dict:
    if not _http: return _fallback(text)

    system = """You are NYC 911 Dispatch AI. Analyze the incident report and building history.
Return ONLY valid JSON with these exact fields:
{
  "category": "specific incident type (e.g. Structure Fire, Cardiac Emergency, Assault)",
  "severity": 1-5 (1=low noise/graffiti, 2=non-emergency, 3=gas/water, 4=medical/accident, 5=fire/shooting/collapse),
  "agency": "FDNY | EMS | NYPD | Multi | Buildings | Housing | Sanitation",
  "send": "what units to dispatch (e.g. Engine Company + Ladder + EMS, 2 RMPs, Ambulance)",
  "summary": "3-4 sentence brief for responding units. Include what to expect, hazards, and recommended approach.",
  "confidence": 0.0-1.0
}

ROUTING: NYPD=crime/suspicious FDNY=fire/gas/collapse EMS=medical/injury Multi=fire+injuries Buildings=structural Housing=tenant
ALWAYS include the 'send' field with specific unit types."""

    parts = []
    if context: parts.append(f"BUILDING HISTORY AT THIS LOCATION:\n{context}")
    if vision: parts.append(f"SCENE FROM PHOTO:\n{vision}")
    parts.append(f"INCIDENT REPORT:\n{text}\n\nReturn ONLY the JSON.")

    if OPENAI_API_KEY:
        try:
            r = await _http.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL_TEXT,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": "\n\n".join(parts)},
                    ],
                    "max_tokens": 500,
                },
                timeout=35.0,
            )
            if r.status_code == 200:
                raw = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                return json.loads(raw) if raw else _fallback(text)
            log.warning(f"OpenAI triage failed: {r.status_code} {r.text[:180]}")
        except Exception as e:
            log.warning(f"OpenAI triage failed: {e}")

    try:
        r = await _http.post(f"{OLLAMA_BASE}/api/chat", json={
            "model": NEMOTRON, "stream": False, "format": "json",
            "messages": [{"role":"system","content":system},
                         {"role":"user","content":"\n\n".join(parts)}],
            "options": {"num_predict": 350, "temperature": 0.3}}, timeout=30.0)
        if r.status_code == 200:
            raw = r.json().get("message",{}).get("content","")
            raw = re.sub(r"^```(?:json)?\s*","",raw.strip())
            raw = re.sub(r"\s*```$","",raw)
            try: return json.loads(raw)
            except:
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m: return json.loads(m.group())
    except Exception as e:
        log.warning(f"Nemotron failed: {e}")
    return _fallback(text)

def _fallback(text: str) -> dict:
    t = text.lower()
    if any(k in t for k in ["fire","smoke","burning","flames"]):
        return {"category":"Structure Fire","severity":5,"agency":"FDNY",
                "send":"Engine Company + Ladder Company + Battalion Chief + EMS standby",
                "summary":f"Reported fire/smoke. {text[:120]}. Deploy full box alarm. EMS standby for potential victims.","confidence":0.5}
    if any(k in t for k in ["medical","breathing","collapsed","unconscious","chest","heart"]):
        return {"category":"Medical Emergency","severity":4,"agency":"EMS",
                "send":"ALS Ambulance + First Responder",
                "summary":f"Medical emergency reported. {text[:120]}. Deploy ALS unit.","confidence":0.5}
    if any(k in t for k in ["gun","shot","robbery","assault","stabbing","weapon"]):
        return {"category":"Violent Crime","severity":5,"agency":"NYPD",
                "send":"2 RMPs + Sergeant + EMS standby",
                "summary":f"Violent crime reported. {text[:120]}. Multiple units needed. EMS standby.","confidence":0.5}
    if any(k in t for k in ["gas","leak","smell"]):
        return {"category":"Gas Leak","severity":4,"agency":"FDNY",
                "send":"Engine Company + Haz-Mat Unit",
                "summary":f"Gas leak/odor reported. {text[:120]}. Evacuate area. Con Ed notification.","confidence":0.5}
    if any(k in t for k in ["noise","loud","music","party"]):
        return {"category":"Noise Complaint","severity":1,"agency":"NYPD",
                "send":"1 RMP (Radio Motor Patrol)",
                "summary":f"Noise disturbance. {text[:120]}.","confidence":0.6}
    if any(k in t for k in ["water","flood","pipe","burst"]):
        return {"category":"Water Emergency","severity":3,"agency":"FDNY",
                "send":"Engine Company",
                "summary":f"Water emergency. {text[:120]}. Check for electrical hazards.","confidence":0.5}
    return {"category":"General Incident","severity":2,"agency":"311",
            "send":"Dispatch appropriate unit based on assessment",
            "summary":f"Incident reported: {text[:150]}","confidence":0.4}

def _build_context(building, past_incidents):
    parts = []
    if building:
        b = building
        parts.append(f"Building: {b.get('address','')} {b.get('borough','')} | Built {b.get('year_built','?')} | {b.get('num_floors','?')} floors | Owner: {b.get('owner_name','?')}")
        if b.get("risk_score",0) > 0:
            parts.append(f"Precomputed risk score: {b['risk_score']}")
    # Summarize past incidents
    fire_count = sum(1 for p in past_incidents if p.get("source") in ("FDNY",))
    ems_count = sum(1 for p in past_incidents if p.get("source") == "EMS")
    nypd_count = sum(1 for p in past_incidents if p.get("source") == "NYPD")
    hpd_count = sum(1 for p in past_incidents if "HPD" in p.get("source",""))
    dob_count = sum(1 for p in past_incidents if "DOB" in p.get("source",""))
    if fire_count: parts.append(f"{fire_count} prior fire incidents at this location")
    if ems_count: parts.append(f"{ems_count} prior EMS calls nearby")
    if nypd_count: parts.append(f"{nypd_count} prior NYPD complaints nearby")
    if hpd_count: parts.append(f"{hpd_count} HPD violations (housing code)")
    if dob_count: parts.append(f"{dob_count} DOB violations (building code)")
    # Include top 3 most recent incidents as detail
    for p in past_incidents[:3]:
        parts.append(f"  - [{p.get('source','')}] {p.get('type','')} {p.get('detail','')} ({p.get('date','')})")
    return "\n".join(parts) if parts else ""

# ── Full building report (for /lookup) ───────────────────────────────────────

def full_building_report(con, building, lat=None, lon=None):
    if not building: return {}
    bbl = building.get("bbl")
    bv = building.get("bin")
    lat = lat or building.get("latitude")
    lon = lon or building.get("longitude")
    addr = (building.get("address") or "").upper()
    data = {}

    if bv and _has(con,"dob_violations"):
        data["dob_violations"] = _q(con,"SELECT violation_type,description,issue_date,severity,is_active FROM dob_violations WHERE bin=? ORDER BY issue_date DESC LIMIT 50",[bv])
    if bv and _has(con,"dob_safety_violations"):
        data["dob_safety_violations"] = _q(con,"SELECT violation_type,violation_description,issue_date,status FROM dob_safety_violations WHERE bin=? ORDER BY issue_date DESC LIMIT 30",[bv])
    if bv and _has(con,"dob_ecb_violations"):
        data["ecb_violations"] = _q(con,"SELECT ecb_violation_number,violation_type,violation_description,penalty_imposed,balance_due,issue_date,severity,is_active FROM dob_ecb_violations WHERE bin=? ORDER BY issue_date DESC LIMIT 30",[bv])
    if bv and _has(con,"hpd_violations"):
        data["hpd_violations"] = _q(con,"SELECT violation_class,nov_description,inspection_date,current_status,apartment FROM hpd_violations WHERE bin=? ORDER BY inspection_date DESC LIMIT 50",[bv])
    if bv and _has(con,"hpd_complaints"):
        data["hpd_complaints"] = _q(con,"SELECT major_category,minor_category,status,status_description,received_date FROM hpd_complaints WHERE bin=? ORDER BY received_date DESC LIMIT 50",[bv])
    if bv and _has(con,"fire_prevention_inspections"):
        data["fire_inspections"] = _q(con,"SELECT inspection_date,result,violation_description,is_compliant FROM fire_prevention_inspections WHERE bin=? ORDER BY inspection_date DESC LIMIT 20",[bv])
    if bv and _has(con,"elevators"):
        data["elevators"] = _q(con,"SELECT device_type,status,speed,capacity,floor_from,floor_to FROM elevators WHERE bin=?",[bv])

    if _has(con,"service_requests_311"):
        sr = []
        if bbl: sr = _q(con,"SELECT complaint_type,descriptor,status,created_date,agency FROM service_requests_311 WHERE bbl=? ORDER BY created_date DESC LIMIT 50",[bbl])
        if not sr and addr: sr = _q(con,"SELECT complaint_type,descriptor,status,created_date,agency FROM service_requests_311 WHERE UPPER(incident_address) LIKE '%'||?||'%' ORDER BY created_date DESC LIMIT 50",[addr])
        data["service_requests_311"] = sr

    if lat and lon:
        if _has(con,"fire_incidents"):
            data["fire_incidents"] = _q(con,"SELECT incident_type_desc,incident_classification,incident_datetime,engines_assigned,dispatch_response_seconds FROM fire_incidents WHERE latitude BETWEEN ?-0.003 AND ?+0.003 AND longitude BETWEEN ?-0.003 AND ?+0.003 AND latitude IS NOT NULL AND latitude!=0 ORDER BY incident_datetime DESC LIMIT 30",[lat,lat,lon,lon])
        if _has(con,"ems_incidents"):
            data["ems_incidents"] = _q(con,"SELECT initial_call_type,initial_severity_level,incident_datetime,dispatch_response_seconds FROM ems_incidents WHERE latitude BETWEEN ?-0.002 AND ?+0.002 AND longitude BETWEEN ?-0.002 AND ?+0.002 AND latitude IS NOT NULL AND latitude!=0 ORDER BY incident_datetime DESC LIMIT 20",[lat,lat,lon,lon])
        if _has(con,"nypd_complaints"):
            data["nypd_complaints"] = _q(con,"SELECT offense_description,law_category,complaint_date,premises_type FROM nypd_complaints WHERE latitude BETWEEN ?-0.002 AND ?+0.002 AND longitude BETWEEN ?-0.002 AND ?+0.002 AND latitude IS NOT NULL AND latitude!=0 ORDER BY complaint_date DESC LIMIT 20",[lat,lat,lon,lon])

    resources = get_nearby_resources(con, lat, lon)
    data.update(resources)

    if bbl and _has(con,"building_risk_scores"):
        data["risk_score"] = _f(_q(con,"SELECT * FROM building_risk_scores WHERE bbl=?",[bbl]))

    owner = building.get("owner_name")
    if owner and _has(con,"owner_portfolio"):
        data["owner_portfolio"] = _f(_q(con,"SELECT * FROM owner_portfolio WHERE owner_name=?",[owner]))

    return data

# ── FastAPI ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _con, _http
    _http = httpx.AsyncClient(timeout=httpx.Timeout(30))
    if not DB_PATH.exists():
        log.error(f"DB not found: {DB_PATH}"); _con = None
    else:
        _con = duckdb.connect(str(DB_PATH), read_only=True)
        tables = [r[0] for r in _con.execute("SHOW TABLES").fetchall()]
        log.info(f"DuckDB: {len(tables)} tables")
        for t in tables:
            try: log.info(f"  {t}: {_con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:,}")
            except: pass
    if OPENAI_API_KEY:
        log.info(f"OpenAI enabled: text={OPENAI_MODEL_TEXT}, vision={OPENAI_MODEL_VISION}")
    else:
        # Check Ollama
        try:
            r = await _http.get(f"{OLLAMA_BASE}/api/version", timeout=3)
            log.info(f"Ollama: {'connected' if r.status_code==200 else 'not responding'}")
        except:
            log.warning("Ollama not running - triage will use keyword fallback")
    yield
    if _con: _con.close()
    if _http: await _http.aclose()

app = FastAPI(title="NYC Dispatch Intelligence", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["X-Total-Ms"])


@app.post("/triage")
async def triage(req: TriageRequest):
    """Incident triage: text + photo + location → dispatch decision + past history."""
    if not _con: return JSONResponse(status_code=503, content={"error":"DB not loaded"})
    t0 = time.perf_counter()

    # Resolve building
    building = resolve_building(_con, req.address, req.borough, lat=req.latitude, lon=req.longitude)
    bbl = building.get("bbl") if building else None
    bv = building.get("bin") if building else None
    lat = req.latitude or (building.get("latitude") if building else None)
    lon = req.longitude or (building.get("longitude") if building else None)
    addr = req.address

    # Get past incidents at this location
    past = get_past_incidents(_con, bbl, bv, addr, lat, lon)
    context = _build_context(building, past)

    # Vision
    vision = None
    vision_ms = 0
    if req.image_b64:
        tv = time.perf_counter()
        vision = await call_llava(req.image_b64)
        vision_ms = int((time.perf_counter()-tv)*1000)

    # LLM triage
    tl = time.perf_counter()
    triage_result = await call_nemotron(req.text, context, vision)
    llm_ms = int((time.perf_counter()-tl)*1000)

    # Nearby resources
    resources = get_nearby_resources(_con, lat, lon)

    total_ms = int((time.perf_counter()-t0)*1000)

    return JSONResponse(content={
        "triage": triage_result,
        "vision_context": vision,
        "building": building,
        "past_incidents": past,
        "nearby": resources,
        "llm_ms": llm_ms,
        "vision_ms": vision_ms,
        "total_ms": total_ms,
    }, headers={"X-Total-Ms": str(total_ms)})


@app.post("/lookup")
async def lookup(req: LookupRequest):
    """Full building report with all violations, incidents, inspections."""
    if not _con: return JSONResponse(status_code=503, content={"error":"DB not loaded"})
    t0 = time.perf_counter()
    building = resolve_building(_con, req.address, req.borough, req.bbl, req.bin, req.latitude, req.longitude)
    data = full_building_report(_con, building, req.latitude, req.longitude)
    return {"building": building, **data, "query_ms": int((time.perf_counter()-t0)*1000)}


@app.get("/search")
async def search(q: str, borough: str = None, limit: int = 10):
    if not _con or not _has(_con,"buildings"): return []
    p = [f"%{q.upper()}%"]
    w = "WHERE UPPER(address) LIKE ?"
    if borough: w += " AND UPPER(borough)=?"; p.append(borough.upper())
    return _q(_con, f"SELECT bbl,bin,address,borough,zipcode,latitude,longitude FROM buildings {w} ORDER BY LENGTH(address) LIMIT ?", p+[limit])


@app.get("/health")
async def health():
    if not _con: return {"status":"no_db","path":str(DB_PATH)}
    ollama = False
    openai = bool(OPENAI_API_KEY)
    try: ollama = (await _http.get(f"{OLLAMA_BASE}/api/version",timeout=3)).status_code==200
    except: pass
    return {"status":"ok","ollama":ollama,"openai":openai,"tables":{r[0]:_q(_con,f"SELECT COUNT(*) AS n FROM {r[0]}")[0]["n"] for r in _con.execute("SHOW TABLES").fetchall()}}


if __name__ == "__main__":
    import uvicorn; uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
