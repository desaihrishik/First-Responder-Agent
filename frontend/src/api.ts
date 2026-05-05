// NYC First Responder Dispatch — API Client
// TypeScript interfaces matching backend JSON schema exactly

export interface SimilarIncident {
  id: string;
  complaint_type: string;
  borough: string;
  date: string;
  resolution_days: number;
  source: string;
}

export interface TriageResponse {
  category: string;
  severity: number;
  agency: string;
  summary: string;
  confidence: number;
  vision_context: string | null;
  similar_incidents: SimilarIncident[];
  inference_ms: number;
  search_ms: number;
  total_ms: number;
}

export interface TriageRequest {
  text: string;
  image_b64?: string;
  borough?: string;
}

// Toggle between mock and real API
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// ============================================================================
// Mock data for development without backend
// ============================================================================

const MOCK_RESPONSES: Record<string, TriageResponse> = {
  noise: {
    category: "Noise Complaint — Residential",
    severity: 1,
    agency: "NYPD",
    summary:
      "Recurring noise disturbance reported from residential unit with loud music at late hours. Pattern consistent with repeated weekend violations. Recommend noise violation documentation and tenant advisory.",
    confidence: 0.92,
    vision_context: null,
    similar_incidents: [
      {
        id: "311_8827341",
        complaint_type: "Noise - Residential",
        borough: "MANHATTAN",
        date: "2025-11-14",
        resolution_days: 0.3,
        source: "311",
      },
      {
        id: "311_8834102",
        complaint_type: "Noise - Residential",
        borough: "MANHATTAN",
        date: "2025-10-22",
        resolution_days: 0.5,
        source: "311",
      },
      {
        id: "311_8791003",
        complaint_type: "Noise - Street/Sidewalk",
        borough: "MANHATTAN",
        date: "2025-09-30",
        resolution_days: 1.2,
        source: "311",
      },
    ],
    inference_ms: 47,
    search_ms: 12,
    total_ms: 780,
  },
  medical: {
    category: "Cardiac/Respiratory Emergency",
    severity: 5,
    agency: "EMS",
    summary:
      "Unconscious individual on sidewalk with apparent respiratory failure. Cyanosis reported. Immediate CPR and AED deployment required. Possible cardiac arrest — priority dispatch.",
    confidence: 0.97,
    vision_context: null,
    similar_incidents: [
      {
        id: "ems_4412009",
        complaint_type: "CARDIAC ARREST",
        borough: "BROOKLYN",
        date: "2025-12-01",
        resolution_days: 0.04,
        source: "EMS",
      },
      {
        id: "ems_4398234",
        complaint_type: "RESPIRATORY DISTRESS",
        borough: "BROOKLYN",
        date: "2025-11-18",
        resolution_days: 0.08,
        source: "EMS",
      },
      {
        id: "ems_4387001",
        complaint_type: "UNCONSCIOUS/FAINTING",
        borough: "QUEENS",
        date: "2025-11-02",
        resolution_days: 0.06,
        source: "EMS",
      },
    ],
    inference_ms: 52,
    search_ms: 8,
    total_ms: 620,
  },
  fire: {
    category: "Structure Fire — Residential",
    severity: 5,
    agency: "Multi",
    summary:
      "Active structure fire in residential brownstone with heavy smoke from third floor. Reports of occupants in distress. FDNY full box response plus EMS standby for potential burn and smoke inhalation victims.",
    confidence: 0.95,
    vision_context:
      "Dense gray-black smoke billowing from upper floor windows of a brick building. Orange glow visible through one window. Multiple people gathered on sidewalk pointing upward. No visible flames on exterior yet but conditions suggest active interior fire.",
    similar_incidents: [
      {
        id: "fire_1029384",
        complaint_type: "STRUCTURAL FIRE",
        borough: "BRONX",
        date: "2025-11-20",
        resolution_days: 0.25,
        source: "FIRE",
      },
      {
        id: "fire_1028001",
        complaint_type: "STRUCTURAL FIRE",
        borough: "BROOKLYN",
        date: "2025-10-15",
        resolution_days: 0.33,
        source: "FIRE",
      },
      {
        id: "311_8812003",
        complaint_type: "Smoke",
        borough: "MANHATTAN",
        date: "2025-09-08",
        resolution_days: 0.1,
        source: "311",
      },
    ],
    inference_ms: 890,
    search_ms: 14,
    total_ms: 1340,
  },
  traffic: {
    category: "Vehicle Accident — Entrapment",
    severity: 4,
    agency: "Multi",
    summary:
      "Overturned vehicle on expressway with driver reportedly trapped inside. Requires FDNY extrication unit and EMS for trauma assessment. Traffic control needed for responder safety.",
    confidence: 0.93,
    vision_context: null,
    similar_incidents: [
      {
        id: "nypd_220394",
        complaint_type: "VEHICLE ACCIDENT",
        borough: "QUEENS",
        date: "2025-11-30",
        resolution_days: 0.12,
        source: "NYPD",
      },
      {
        id: "ems_4410002",
        complaint_type: "MVA - ENTRAPMENT",
        borough: "BRONX",
        date: "2025-11-25",
        resolution_days: 0.09,
        source: "EMS",
      },
      {
        id: "nypd_219801",
        complaint_type: "VEHICLE ACCIDENT",
        borough: "BROOKLYN",
        date: "2025-11-12",
        resolution_days: 0.15,
        source: "NYPD",
      },
    ],
    inference_ms: 41,
    search_ms: 11,
    total_ms: 710,
  },
  suspicious: {
    category: "Suspicious Package",
    severity: 4,
    agency: "NYPD",
    summary:
      "Unattended package reported near high-traffic transit entrance. NYPD bomb squad assessment recommended. Establish perimeter and begin controlled evacuation of immediate area pending investigation.",
    confidence: 0.88,
    vision_context: null,
    similar_incidents: [
      {
        id: "nypd_221001",
        complaint_type: "SUSPICIOUS PACKAGE",
        borough: "MANHATTAN",
        date: "2025-12-02",
        resolution_days: 0.08,
        source: "NYPD",
      },
      {
        id: "nypd_218004",
        complaint_type: "SUSPICIOUS PACKAGE",
        borough: "MANHATTAN",
        date: "2025-10-01",
        resolution_days: 0.12,
        source: "NYPD",
      },
      {
        id: "311_8845002",
        complaint_type: "Abandoned Vehicle",
        borough: "QUEENS",
        date: "2025-09-15",
        resolution_days: 3.2,
        source: "311",
      },
    ],
    inference_ms: 38,
    search_ms: 9,
    total_ms: 650,
  },
};

function detectScenario(text: string): string {
  const lower = text.toLowerCase();
  if (lower.includes("fire") || lower.includes("smoke") || lower.includes("burning"))
    return "fire";
  if (
    lower.includes("medical") ||
    lower.includes("breathing") ||
    lower.includes("collapsed") ||
    lower.includes("unconscious") ||
    lower.includes("chest pain")
  )
    return "medical";
  if (
    lower.includes("suspicious") ||
    lower.includes("package") ||
    lower.includes("bomb")
  )
    return "suspicious";
  if (
    lower.includes("car") ||
    lower.includes("accident") ||
    lower.includes("vehicle") ||
    lower.includes("crash") ||
    lower.includes("flipped")
  )
    return "traffic";
  return "noise";
}

async function mockTriage(req: TriageRequest): Promise<TriageResponse> {
  await new Promise((r) => setTimeout(r, 600 + Math.random() * 400));
  const scenario = detectScenario(req.text);
  const response = { ...MOCK_RESPONSES[scenario] };
  if (req.borough && req.borough !== "All") {
    response.similar_incidents = response.similar_incidents.map((inc) => ({
      ...inc,
      borough: req.borough!.toUpperCase(),
    }));
  }
  return response;
}

// ============================================================================
// Real API call
// ============================================================================

async function realTriage(req: TriageRequest): Promise<TriageResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  try {
    const response = await fetch(`${API_BASE}/triage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: req.text,
        image_b64: req.image_b64 || null,
        borough: req.borough || null,
      }),
      signal: controller.signal,
    });

    clearTimeout(timeout);

    if (!response.ok) {
      throw new Error(`API returned ${response.status}`);
    }

    const data: TriageResponse = await response.json();

    // Override total_ms with server header if available
    const serverMs = response.headers.get("X-Total-Ms");
    if (serverMs) {
      data.total_ms = parseInt(serverMs, 10);
    }

    return data;
  } catch (err) {
    clearTimeout(timeout);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("Request timed out after 15 seconds");
    }
    throw err;
  }
}

// ============================================================================
// Exported function
// ============================================================================

export async function triage(
  text: string,
  imageB64?: string,
  borough?: string
): Promise<TriageResponse> {
  const req: TriageRequest = { text, image_b64: imageB64, borough };

  if (USE_MOCK) {
    return mockTriage(req);
  }

  return realTriage(req);
}

export async function checkHealth(): Promise<{
  status: string;
  models: Record<string, boolean>;
}> {
  if (USE_MOCK) {
    return { status: "healthy", models: { nemotron: true, llava: true } };
  }

  const response = await fetch(`${API_BASE}/health`);
  return response.json();
}
