import { useState, useCallback, useEffect, useRef } from "react";
const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

/* ── Types ──────────────────────────────────────────────────────────────── */
const SEV = {
  1: { color: "#15803d", bg: "#dcfce7", label: "LOW", accent: "#22c55e" },
  2: { color: "#a16207", bg: "#fef9c3", label: "MODERATE", accent: "#eab308" },
  3: { color: "#c2410c", bg: "#ffedd5", label: "URGENT", accent: "#f97316" },
  4: { color: "#b91c1c", bg: "#fee2e2", label: "CRITICAL", accent: "#ef4444" },
  5: { color: "#7f1d1e", bg: "#fecdd3", label: "LIFE-THREAT", accent: "#dc2626" },
};

const AGENCY_COLORS = {
  FDNY: { bg: "#dc2626", text: "#fff" },
  EMS: { bg: "#ea580c", text: "#fff" },
  NYPD: { bg: "#1d4ed8", text: "#fff" },
  Multi: { bg: "#7c3aed", text: "#fff" },
  Buildings: { bg: "#475569", text: "#fff" },
  Housing: { bg: "#4f46e5", text: "#fff" },
  Sanitation: { bg: "#16a34a", text: "#fff" },
  "311": { bg: "#64748b", text: "#fff" },
};

const SOURCE_COLORS = {
  FDNY: "#dc2626", EMS: "#ea580c", NYPD: "#1d4ed8",
  "HPD Violation": "#7c3aed", "DOB Violation": "#4f46e5", "311": "#16a34a",
};

/* ── Google Fonts injection ─────────────────────────────────────────────── */
const fontLink = document.createElement("link");
fontLink.rel = "stylesheet";
fontLink.href = "https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap";
if (!document.head.querySelector('link[href*="Instrument+Sans"]')) {
  document.head.appendChild(fontLink);
}

/* ── Inject global styles ───────────────────────────────────────────────── */
const styleEl = document.createElement("style");
styleEl.textContent = `
  :root {
    --bg: #F8F7F4;
    --surface: #FFFFFF;
    --surface-alt: #F1F0EC;
    --border: #E2E0D9;
    --border-strong: #C8C5BC;
    --text-primary: #1A1815;
    --text-secondary: #5C584E;
    --text-muted: #8C8880;
    --red: #C62828;
    --red-soft: #FFEBEE;
    --blue: #1565C0;
    --blue-soft: #E3F2FD;
    --font-sans: 'Instrument Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'JetBrains Mono', ui-monospace, monospace;
    --radius: 10px;
    --radius-sm: 6px;
    --shadow-sm: 0 1px 3px rgba(26,24,21,0.06), 0 1px 2px rgba(26,24,21,0.04);
    --shadow-md: 0 4px 12px rgba(26,24,21,0.08), 0 2px 4px rgba(26,24,21,0.04);
    --shadow-lg: 0 12px 40px rgba(26,24,21,0.12), 0 4px 12px rgba(26,24,21,0.06);
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); }
  input, textarea, button { font-family: var(--font-sans); }
  input::placeholder, textarea::placeholder { color: var(--text-muted); }
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
  @keyframes fadeUp { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }
  .fade-up { animation: fadeUp 0.4s ease-out both; }
  .fade-up-1 { animation-delay: 0.05s; }
  .fade-up-2 { animation-delay: 0.1s; }
  .fade-up-3 { animation-delay: 0.15s; }
  .fade-up-4 { animation-delay: 0.2s; }
  .fade-up-5 { animation-delay: 0.25s; }
`;
if (!document.head.querySelector('style[data-dispatch]')) {
  styleEl.setAttribute("data-dispatch", "1");
  document.head.appendChild(styleEl);
}

/* ── Small components ───────────────────────────────────────────────────── */
function SourceBadge({ s }) {
  const c = SOURCE_COLORS[s] || "#64748b";
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: "3px 8px", borderRadius: 4,
      background: c + "14", color: c, textTransform: "uppercase",
      letterSpacing: 0.6, fontFamily: "var(--font-mono)", whiteSpace: "nowrap",
    }}>{s}</span>
  );
}

function SectionHeader({ icon, title, count }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "12px 16px", borderBottom: "1px solid var(--border)",
    }}>
      <span style={{ fontSize: 15 }}>{icon}</span>
      <span style={{
        fontSize: 13, fontWeight: 700, color: "var(--text-primary)",
        letterSpacing: -0.2,
      }}>{title}</span>
      {count != null && (
        <span style={{
          marginLeft: "auto", fontSize: 11, fontWeight: 600,
          color: "var(--text-muted)", fontFamily: "var(--font-mono)",
          background: "var(--surface-alt)", padding: "2px 8px",
          borderRadius: 20,
        }}>{count}</span>
      )}
    </div>
  );
}

function DataTable({ title, icon, rows, cols }) {
  if (!rows?.length) return null;
  return (
    <div className="fade-up" style={{
      background: "var(--surface)", borderRadius: "var(--radius)",
      border: "1px solid var(--border)", overflow: "hidden", marginBottom: 14,
      boxShadow: "var(--shadow-sm)",
    }}>
      <SectionHeader icon={icon} title={title} count={rows.length} />
      <div style={{ overflowX: "auto", maxHeight: 280 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr>
              {cols.map(c => (
                <th key={c.key} style={{
                  textAlign: "left", padding: "9px 14px", fontSize: 10,
                  color: "var(--text-muted)", borderBottom: "1px solid var(--border)",
                  textTransform: "uppercase", letterSpacing: 0.8, fontWeight: 600,
                  whiteSpace: "nowrap", width: c.w, background: "var(--surface-alt)",
                  fontFamily: "var(--font-mono)",
                }}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none" }}>
                {cols.map(c => (
                  <td key={c.key} style={{
                    padding: "8px 14px", color: "var(--text-secondary)",
                    maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}>{r[c.key] == null ? "—" : String(r[c.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Main App ───────────────────────────────────────────────────────────── */
export default function App() {
  const [mode, setMode] = useState("incident");
  const [address, setAddress] = useState("");
  const [incidentText, setIncidentText] = useState("");
  const [imageB64, setImageB64] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [triageResult, setTriageResult] = useState(null);
  const [lookupResult, setLookupResult] = useState(null);
  const [error, setError] = useState(null);
  const debRef = useRef();
  const fileRef = useRef(null);

  useEffect(() => {
    if (address.length < 3) { setSuggestions([]); return; }
    clearTimeout(debRef.current);
    debRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/search?q=${encodeURIComponent(address)}&limit=6`);
        if (r.ok) setSuggestions(await r.json());
      } catch (e) { /* silent */ }
    }, 250);
  }, [address]);

  const handleImage = (file) => {
    const reader = new FileReader();
    reader.onload = () => {
      const u = reader.result;
      setImagePreview(u);
      setImageB64(u.split(",")[1]);
    };
    reader.readAsDataURL(file);
  };

  const doTriage = useCallback(async () => {
    if (!incidentText.trim()) return;
    setLoading(true); setTriageResult(null); setError(null); setSuggestions([]);
    try {
      const body = { text: incidentText };
      if (imageB64) body.image_b64 = imageB64;
      if (address.trim()) body.address = address;
      const r = await fetch(`${API}/triage`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await r.text());
      setTriageResult(await r.json());
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); }
  }, [incidentText, imageB64, address]);

  const doLookup = useCallback(async (params) => {
    setLoading(true); setLookupResult(null); setError(null); setSuggestions([]);
    try {
      const r = await fetch(`${API}/lookup`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!r.ok) throw new Error(await r.text());
      setLookupResult(await r.json());
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); }
  }, []);

  const selectSuggestion = (s) => {
    setAddress(s.address); setSuggestions([]);
    if (mode === "location") doLookup({ bbl: s.bbl, bin: s.bin });
  };

  const submitLocation = () => {
    if (!address.trim()) return;
    const c = address.replace(/[\s-]/g, "");
    if (/^\d{10}$/.test(c)) doLookup({ bbl: c });
    else doLookup({ address });
  };

  const switchMode = (m) => {
    setMode(m); setTriageResult(null); setLookupResult(null); setError(null);
  };

  const tri = triageResult?.triage;
  const sev = tri ? SEV[tri.severity] || SEV[2] : null;
  const ac = tri ? AGENCY_COLORS[tri.agency] || AGENCY_COLORS["311"] : null;
  const past = (triageResult?.past_incidents || []);
  const nearby = (triageResult?.nearby || {});
  const bldg = triageResult?.building || lookupResult?.building;

  return (
    <div style={{
      minHeight: "100vh", background: "var(--bg)",
      color: "var(--text-primary)", fontFamily: "var(--font-sans)",
    }}>
      {/* ═══ HEADER ═══ */}
      <header style={{
        background: "var(--surface)", borderBottom: "1px solid var(--border)",
        padding: "0 24px", height: 60, display: "flex", alignItems: "center", gap: 14,
        boxShadow: "var(--shadow-sm)", position: "sticky", top: 0, zIndex: 100,
      }}>
        {/* Logo mark */}
        <div style={{
          width: 36, height: 36, borderRadius: 8, background: "var(--red)",
          display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: "0 2px 8px rgba(198,40,40,0.3)",
        }}>
          <span style={{
            fontFamily: "var(--font-mono)", fontWeight: 800, fontSize: 12,
            color: "#fff", letterSpacing: -0.5,
          }}>911</span>
        </div>
        <div>
          <div style={{
            fontSize: 15, fontWeight: 700, color: "var(--text-primary)",
            letterSpacing: -0.3,
          }}>NYC Dispatch Intelligence</div>
          <div style={{
            fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
            letterSpacing: 0.3,
          }}>86M Records · 27 Datasets · Ollama + DuckDB</div>
        </div>

        {/* Mode switch */}
        <div style={{
          marginLeft: "auto", display: "flex", gap: 2,
          background: "var(--surface-alt)", borderRadius: 8, padding: 3,
          border: "1px solid var(--border)",
        }}>
          {[
            { key: "incident", icon: "🚨", label: "Incident" },
            { key: "location", icon: "📍", label: "Location" },
          ].map(m => (
            <button key={m.key} onClick={() => switchMode(m.key)} style={{
              padding: "7px 16px", borderRadius: 6, border: "none",
              fontSize: 12, fontWeight: 600, cursor: "pointer",
              transition: "all 0.2s",
              background: mode === m.key
                ? (m.key === "incident" ? "var(--red)" : "var(--blue)")
                : "transparent",
              color: mode === m.key ? "#fff" : "var(--text-muted)",
              boxShadow: mode === m.key ? "var(--shadow-sm)" : "none",
            }}>{m.icon} {m.label}</button>
          ))}
        </div>
      </header>

      {/* ═══ MAIN CONTENT ═══ */}
      <div style={{ maxWidth: 960, margin: "24px auto 0", padding: "0 24px", position: "relative" }}>

        {/* Address bar */}
        <div style={{ display: "flex", gap: 8, marginBottom: mode === "incident" ? 12 : 0 }}>
          <div style={{ flex: 1, position: "relative" }}>
            <div style={{
              position: "absolute", left: 14, top: "50%", transform: "translateY(-50%)",
              fontSize: 14, opacity: 0.4, pointerEvents: "none",
            }}>📍</div>
            <input
              value={address}
              onChange={e => setAddress(e.target.value)}
              onKeyDown={e => e.key === "Enter" && mode === "location" && submitLocation()}
              placeholder={mode === "incident"
                ? "Location of incident (address or area)..."
                : "Enter NYC address or BBL..."}
              style={{
                width: "100%", padding: "12px 16px 12px 38px",
                borderRadius: "var(--radius)", background: "var(--surface)",
                border: "1px solid var(--border)", color: "var(--text-primary)",
                fontSize: 14, outline: "none", transition: "border-color 0.2s, box-shadow 0.2s",
                boxShadow: "var(--shadow-sm)",
              }}
              onFocus={e => {
                e.target.style.borderColor = mode === "incident" ? "var(--red)" : "var(--blue)";
                e.target.style.boxShadow = mode === "incident"
                  ? "0 0 0 3px rgba(198,40,40,0.1)" : "0 0 0 3px rgba(21,101,192,0.1)";
              }}
              onBlur={e => {
                e.target.style.borderColor = "var(--border)";
                e.target.style.boxShadow = "var(--shadow-sm)";
              }}
            />
          </div>
          {mode === "location" && (
            <button onClick={submitLocation} disabled={loading || !address.trim()} style={{
              padding: "12px 24px", borderRadius: "var(--radius)", border: "none",
              background: loading ? "var(--border)" : "var(--blue)",
              color: "#fff", fontWeight: 700, fontSize: 13.5, cursor: "pointer",
              boxShadow: loading ? "none" : "0 2px 8px rgba(21,101,192,0.3)",
              transition: "all 0.2s", opacity: loading || !address.trim() ? 0.6 : 1,
            }}>{loading ? "..." : "Lookup"}</button>
          )}
        </div>

        {/* Suggestions dropdown */}
        {suggestions.length > 0 && (
          <div style={{
            position: "absolute", top: 56, left: 24, right: 24,
            background: "var(--surface)", borderRadius: "var(--radius)",
            border: "1px solid var(--border)", zIndex: 50, overflow: "hidden",
            boxShadow: "var(--shadow-lg)",
          }}>
            {suggestions.map((s, i) => (
              <div key={i} onClick={() => selectSuggestion(s)} style={{
                padding: "11px 16px", cursor: "pointer", fontSize: 13,
                borderBottom: i < suggestions.length - 1 ? "1px solid var(--border)" : "none",
                color: "var(--text-primary)", transition: "background 0.15s",
              }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--surface-alt)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <span style={{ fontWeight: 600 }}>{s.address}</span>
                <span style={{
                  color: "var(--text-muted)", marginLeft: 10, fontSize: 11,
                  fontFamily: "var(--font-mono)",
                }}>{s.borough}</span>
              </div>
            ))}
          </div>
        )}

        {/* ═══ INCIDENT INPUTS ═══ */}
        {mode === "incident" && (
          <>
            <textarea
              value={incidentText}
              onChange={e => setIncidentText(e.target.value)}
              placeholder="Describe the incident... (e.g. 'Heavy smoke from 3rd floor window, people yelling for help')"
              rows={4}
              style={{
                width: "100%", padding: "14px 16px",
                borderRadius: "var(--radius)", background: "var(--surface)",
                border: "1px solid var(--border)", color: "var(--text-primary)",
                fontSize: 13.5, outline: "none", resize: "none",
                marginBottom: 12, lineHeight: 1.6,
                boxShadow: "var(--shadow-sm)", transition: "border-color 0.2s, box-shadow 0.2s",
              }}
              onFocus={e => {
                e.target.style.borderColor = "var(--red)";
                e.target.style.boxShadow = "0 0 0 3px rgba(198,40,40,0.08)";
              }}
              onBlur={e => {
                e.target.style.borderColor = "var(--border)";
                e.target.style.boxShadow = "var(--shadow-sm)";
              }}
            />
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <div
                onClick={() => fileRef.current?.click()}
                style={{
                  padding: "10px 18px", borderRadius: "var(--radius-sm)",
                  background: imagePreview ? "#E8F5E9" : "var(--surface)",
                  border: `1px ${imagePreview ? "solid #4CAF50" : "dashed var(--border-strong)"}`,
                  cursor: "pointer", fontSize: 12.5, fontWeight: 500,
                  color: imagePreview ? "#2E7D32" : "var(--text-secondary)",
                  display: "flex", alignItems: "center", gap: 7,
                  transition: "all 0.2s",
                }}
              >
                📷 {imagePreview ? "Photo attached ✓" : "Add photo"}
              </div>
              {imagePreview && (
                <button onClick={() => { setImageB64(null); setImagePreview(null); }} style={{
                  fontSize: 12, color: "var(--red)", background: "none",
                  border: "none", cursor: "pointer", fontWeight: 500,
                }}>Remove</button>
              )}
              <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }}
                onChange={e => { const f = e.target.files?.[0]; if (f) handleImage(f); }} />

              <button onClick={doTriage} disabled={loading || !incidentText.trim()} style={{
                marginLeft: "auto", padding: "12px 30px",
                borderRadius: "var(--radius)", border: "none",
                background: loading ? "var(--border)" : "var(--red)",
                color: "#fff", fontWeight: 700, fontSize: 13.5, cursor: "pointer",
                boxShadow: loading ? "none" : "0 3px 12px rgba(198,40,40,0.35)",
                transition: "all 0.2s",
                opacity: loading || !incidentText.trim() ? 0.6 : 1,
              }}>
                {loading ? "Analyzing..." : "🚨 Dispatch Triage"}
              </button>
            </div>
          </>
        )}
      </div>

      {/* ═══ ERROR ═══ */}
      {error && (
        <div style={{
          maxWidth: 960, margin: "18px auto", padding: "14px 20px",
          background: "var(--red-soft)", border: "1px solid #FFCDD2",
          borderRadius: "var(--radius)", color: "var(--red)", fontSize: 13,
          fontWeight: 500,
        }}>⚠ {error}</div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════
          INCIDENT TRIAGE RESULT
          ═══════════════════════════════════════════════════════════════════ */}
      {triageResult && tri && sev && ac && (
        <div style={{ maxWidth: 1100, margin: "24px auto", padding: "0 24px" }}>

          {/* Dispatch decision card */}
          <div className="fade-up" style={{
            background: "var(--surface)", borderRadius: 14,
            padding: "28px 32px", border: `2px solid ${sev.accent}30`,
            marginBottom: 18, boxShadow: "var(--shadow-md)",
            borderLeft: `5px solid ${sev.accent}`,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 24 }}>
              {/* Severity circle */}
              <div style={{
                width: 88, height: 88, borderRadius: "50%",
                background: sev.bg, display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center", flexShrink: 0,
                border: `3px solid ${sev.accent}40`,
              }}>
                <span style={{
                  fontSize: 34, fontWeight: 800, color: sev.color,
                  fontFamily: "var(--font-mono)", lineHeight: 1,
                }}>{tri.severity}</span>
                <span style={{
                  fontSize: 8, fontWeight: 700, color: sev.color,
                  textTransform: "uppercase", letterSpacing: 1.2,
                  fontFamily: "var(--font-mono)", marginTop: 2,
                }}>{sev.label}</span>
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                {/* Category + Agency */}
                <div style={{
                  display: "flex", alignItems: "center", gap: 10,
                  marginBottom: 10, flexWrap: "wrap",
                }}>
                  <span style={{
                    fontSize: 20, fontWeight: 800, color: "var(--text-primary)",
                    letterSpacing: -0.5,
                  }}>{tri.category}</span>
                  <span style={{
                    padding: "4px 14px", borderRadius: 20, fontSize: 12,
                    fontWeight: 700, background: ac.bg, color: ac.text,
                    letterSpacing: 0.3,
                  }}>{tri.agency}</span>
                  <span style={{
                    fontSize: 11, color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                  }}>{Math.round(tri.confidence * 100)}% conf</span>
                </div>

                {/* DISPATCH — key info */}
                <div style={{
                  background: "var(--surface-alt)", borderRadius: 8,
                  padding: "12px 16px", marginBottom: 10,
                  border: "1px solid var(--border)",
                }}>
                  <div style={{
                    fontSize: 9, color: "var(--text-muted)",
                    textTransform: "uppercase", letterSpacing: 1.2,
                    fontWeight: 700, marginBottom: 4,
                    fontFamily: "var(--font-mono)",
                  }}>DISPATCH</div>
                  <div style={{
                    fontSize: 15, fontWeight: 700,
                    color: tri.severity >= 4 ? "var(--red)" : "#B45309",
                  }}>{tri.send}</div>
                </div>

                <div style={{
                  fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.65,
                }}>{tri.summary}</div>
              </div>
            </div>
          </div>

          {/* Vision analysis */}
          {triageResult.vision_context && (
            <div className="fade-up fade-up-1" style={{
              background: "var(--blue-soft)", borderRadius: "var(--radius)",
              border: "1px solid #BBDEFB", padding: "16px 20px", marginBottom: 18,
            }}>
              <div style={{
                display: "flex", alignItems: "center", gap: 8, marginBottom: 8,
              }}>
                <span>👁️</span>
                <span style={{
                  fontSize: 10, fontWeight: 700, color: "var(--blue)",
                  textTransform: "uppercase", letterSpacing: 1,
                  fontFamily: "var(--font-mono)",
                }}>Scene Analysis (LLaVA)</span>
              </div>
              <div style={{
                fontSize: 13, color: "#1565C0", fontStyle: "italic", lineHeight: 1.55,
              }}>{String(triageResult.vision_context)}</div>
            </div>
          )}

          {/* Building info */}
          {bldg && (
            <div className="fade-up fade-up-2" style={{
              background: "var(--surface)", borderRadius: "var(--radius)",
              padding: "16px 20px", border: "1px solid var(--border)",
              marginBottom: 18, boxShadow: "var(--shadow-sm)",
            }}>
              <div style={{
                fontSize: 9, color: "var(--text-muted)", textTransform: "uppercase",
                letterSpacing: 1.2, fontWeight: 700, marginBottom: 6,
                fontFamily: "var(--font-mono)",
              }}>BUILDING AT LOCATION</div>
              <div style={{
                fontSize: 15, fontWeight: 700, color: "var(--text-primary)",
              }}>{String(bldg.address || "")}</div>
              <div style={{
                fontSize: 12.5, color: "var(--text-muted)", marginTop: 4,
                fontFamily: "var(--font-mono)",
              }}>
                {bldg.borough}
                {bldg.year_built ? ` · Built ${bldg.year_built}` : ""}
                {bldg.num_floors ? ` · ${bldg.num_floors} floors` : ""}
                {bldg.owner_name ? ` · Owner: ${bldg.owner_name}` : ""}
                {bldg.risk_score && Number(bldg.risk_score) > 0 ? ` · Risk: ${bldg.risk_score}` : ""}
              </div>
            </div>
          )}

          {/* Past incidents */}
          {past.length > 0 && (
            <div className="fade-up fade-up-3" style={{
              background: "var(--surface)", borderRadius: "var(--radius)",
              border: "1px solid var(--border)", overflow: "hidden",
              marginBottom: 18, boxShadow: "var(--shadow-sm)",
            }}>
              <SectionHeader icon="📋" title="Past Incidents at This Location" count={past.length} />
              <div style={{
                display: "flex", flexDirection: "column", gap: 1,
                maxHeight: 420, overflowY: "auto", background: "var(--border)",
              }}>
                {past.map((p, i) => (
                  <div key={i} style={{
                    display: "flex", gap: 12, alignItems: "flex-start",
                    padding: "10px 16px", background: "var(--surface)",
                  }}>
                    <SourceBadge s={p.source} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
                      }}>{p.type || "—"}</div>
                      {p.detail && (
                        <div style={{
                          fontSize: 12, color: "var(--text-secondary)", marginTop: 2,
                          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        }}>{p.detail}</div>
                      )}
                      {p.resolution && (
                        <div style={{
                          fontSize: 11, color: "var(--text-muted)", marginTop: 2,
                        }}>Resolution: {p.resolution}</div>
                      )}
                    </div>
                    <div style={{
                      fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap",
                      textAlign: "right", fontFamily: "var(--font-mono)", flexShrink: 0,
                    }}>
                      {p.date || ""}
                      {p.status && (
                        <div style={{
                          color: p.status === "ACTIVE" || p.status === "Open"
                            ? "var(--red)" : "var(--text-muted)",
                          fontWeight: 600,
                        }}>{p.status}</div>
                      )}
                      {p.response_sec != null && (
                        <div style={{ color: "var(--text-muted)" }}>{p.response_sec}s</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Nearby resources */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 14, marginBottom: 24,
          }}>
            {nearby.hospitals?.length > 0 && (
              <div className="fade-up fade-up-4" style={{
                background: "var(--surface)", borderRadius: "var(--radius)",
                border: "1px solid var(--border)", overflow: "hidden",
                boxShadow: "var(--shadow-sm)",
              }}>
                <SectionHeader icon="🏥" title="Nearest Hospitals" count={nearby.hospitals.length} />
                <div style={{ padding: "12px 16px" }}>
                  {nearby.hospitals.map((h, i) => (
                    <div key={i} style={{ marginBottom: 8, fontSize: 12.5 }}>
                      <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                        {String(h.facility_name)}
                      </span>
                      <span style={{
                        color: "var(--blue)", marginLeft: 8, fontWeight: 600,
                        fontFamily: "var(--font-mono)", fontSize: 11,
                      }}>{String(h.dist_mi)} mi</span>
                      <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                        {String(h.address)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {nearby.hydrants?.length > 0 && (
              <div className="fade-up fade-up-4" style={{
                background: "var(--surface)", borderRadius: "var(--radius)",
                border: "1px solid var(--border)", overflow: "hidden",
                boxShadow: "var(--shadow-sm)",
              }}>
                <SectionHeader icon="🧯" title="Nearest Hydrants" count={nearby.hydrants.length} />
                <div style={{ padding: "12px 16px" }}>
                  {nearby.hydrants.map((h, i) => (
                    <div key={i} style={{ marginBottom: 5, fontSize: 12.5 }}>
                      <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                        {String(h.unitid || "Hydrant")}
                      </span>
                      <span style={{
                        color: "var(--blue)", marginLeft: 8, fontWeight: 600,
                        fontFamily: "var(--font-mono)", fontSize: 11,
                      }}>{String(h.dist_ft)} ft</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {nearby.facilities?.length > 0 && (
              <div className="fade-up fade-up-5" style={{
                background: "var(--surface)", borderRadius: "var(--radius)",
                border: "1px solid var(--border)", overflow: "hidden",
                boxShadow: "var(--shadow-sm)",
              }}>
                <SectionHeader icon="🏛️" title="Nearby Facilities" count={nearby.facilities.length} />
                <div style={{ padding: "12px 16px" }}>
                  {nearby.facilities.map((f, i) => (
                    <div key={i} style={{ marginBottom: 5, fontSize: 12.5 }}>
                      <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                        {String(f.facname)}
                      </span>
                      <span style={{
                        color: "var(--text-muted)", marginLeft: 8, fontSize: 11,
                        fontFamily: "var(--font-mono)",
                      }}>{String(f.dist_ft)} ft · {String(f.factype)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Timing footer */}
          <div style={{
            textAlign: "center", fontSize: 11, color: "var(--text-muted)",
            fontFamily: "var(--font-mono)", marginBottom: 28,
            padding: "12px 0", borderTop: "1px solid var(--border)",
          }}>
            LLM: {triageResult.llm_ms}ms · Vision: {triageResult.vision_ms}ms · Total: {triageResult.total_ms}ms · Ollama + DuckDB · Zero Cloud
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════
          LOCATION LOOKUP RESULT
          ═══════════════════════════════════════════════════════════════════ */}
      {lookupResult && lookupResult.building && (
        <div style={{ maxWidth: 1100, margin: "24px auto", padding: "0 24px" }}>
          {(() => {
            const b = lookupResult.building;
            const rs = lookupResult.risk_score;
            const score = rs?.overall_risk_score || b?.risk_score || 0;
            const sv = score >= 60 ? SEV[5] : score >= 30 ? SEV[4] : score >= 10 ? SEV[3] : SEV[1];
            return (
              <div className="fade-up" style={{
                background: "var(--surface)", borderRadius: 14,
                padding: "22px 26px", border: `2px solid ${sv.accent}25`,
                marginBottom: 20, display: "flex", alignItems: "center", gap: 20,
                boxShadow: "var(--shadow-md)", borderLeft: `5px solid ${sv.accent}`,
              }}>
                <div style={{
                  width: 72, height: 72, borderRadius: "50%",
                  background: sv.bg, display: "flex", flexDirection: "column",
                  alignItems: "center", justifyContent: "center",
                  border: `3px solid ${sv.accent}40`, flexShrink: 0,
                }}>
                  <span style={{
                    fontSize: 24, fontWeight: 800, color: sv.color,
                    fontFamily: "var(--font-mono)", lineHeight: 1,
                  }}>{Math.round(score)}</span>
                  <span style={{
                    fontSize: 7.5, fontWeight: 700, color: sv.color,
                    textTransform: "uppercase", letterSpacing: 1,
                    fontFamily: "var(--font-mono)",
                  }}>{sv.label}</span>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{
                    fontSize: 17, fontWeight: 800, color: "var(--text-primary)",
                    letterSpacing: -0.3,
                  }}>{String(b.address)}</div>
                  <div style={{
                    fontSize: 12.5, color: "var(--text-muted)", marginTop: 4,
                    fontFamily: "var(--font-mono)",
                  }}>
                    {b.borough}
                    {b.year_built ? ` · Built ${b.year_built}` : ""}
                    {b.num_floors ? ` · ${b.num_floors} floors` : ""}
                  </div>
                  {b.owner_name && (
                    <div style={{
                      fontSize: 12, color: "var(--text-muted)", marginTop: 2,
                    }}>Owner: {String(b.owner_name)}</div>
                  )}
                </div>
                <div style={{
                  fontSize: 10, color: "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                }}>{lookupResult.query_ms}ms</div>
              </div>
            );
          })()}

          <DataTable title="DOB Violations" icon="🏗️"
            rows={lookupResult.dob_violations}
            cols={[
              { key: "violation_type", label: "Type" },
              { key: "description", label: "Description", w: "40%" },
              { key: "issue_date", label: "Issued" },
              { key: "severity", label: "Sev" },
              { key: "is_active", label: "Active" },
            ]} />
          <DataTable title="ECB Violations" icon="💰"
            rows={lookupResult.ecb_violations}
            cols={[
              { key: "violation_type", label: "Type" },
              { key: "violation_description", label: "Description", w: "30%" },
              { key: "penalty_imposed", label: "Penalty" },
              { key: "balance_due", label: "Balance" },
              { key: "is_active", label: "Active" },
            ]} />
          <DataTable title="HPD Violations" icon="🏠"
            rows={lookupResult.hpd_violations}
            cols={[
              { key: "violation_class", label: "Class" },
              { key: "nov_description", label: "Description", w: "40%" },
              { key: "inspection_date", label: "Inspected" },
              { key: "current_status", label: "Status" },
            ]} />
          <DataTable title="HPD Complaints" icon="📋"
            rows={lookupResult.hpd_complaints}
            cols={[
              { key: "major_category", label: "Category" },
              { key: "minor_category", label: "Detail" },
              { key: "status", label: "Status" },
              { key: "received_date", label: "Date" },
            ]} />
          <DataTable title="311 Requests" icon="📞"
            rows={lookupResult.service_requests_311}
            cols={[
              { key: "complaint_type", label: "Type" },
              { key: "descriptor", label: "Detail" },
              { key: "status", label: "Status" },
              { key: "created_date", label: "Date" },
              { key: "agency", label: "Agency" },
            ]} />
          <DataTable title="Fire Incidents" icon="🔥"
            rows={lookupResult.fire_incidents}
            cols={[
              { key: "incident_type_desc", label: "Type" },
              { key: "incident_classification", label: "Class" },
              { key: "incident_datetime", label: "When" },
              { key: "engines_assigned", label: "Engines" },
            ]} />
          <DataTable title="EMS" icon="🚑"
            rows={lookupResult.ems_incidents}
            cols={[
              { key: "initial_call_type", label: "Call" },
              { key: "initial_severity_level", label: "Sev" },
              { key: "incident_datetime", label: "When" },
            ]} />
          <DataTable title="NYPD" icon="🚔"
            rows={lookupResult.nypd_complaints}
            cols={[
              { key: "offense_description", label: "Offense" },
              { key: "law_category", label: "Category" },
              { key: "complaint_date", label: "Date" },
            ]} />
          <DataTable title="Fire Inspections" icon="🔍"
            rows={lookupResult.fire_inspections}
            cols={[
              { key: "inspection_date", label: "Date" },
              { key: "result", label: "Result" },
              { key: "is_compliant", label: "Pass" },
              { key: "violation_description", label: "Violation" },
            ]} />
          <DataTable title="Elevators" icon="🛗"
            rows={lookupResult.elevators}
            cols={[
              { key: "device_type", label: "Type" },
              { key: "status", label: "Status" },
              { key: "speed", label: "Speed" },
              { key: "capacity", label: "Cap" },
            ]} />
        </div>
      )}

      {/* ═══ EMPTY STATE ═══ */}
      {!triageResult && !lookupResult && !loading && !error && (
        <div style={{
          textAlign: "center", padding: "80px 24px",
          color: "var(--text-muted)",
        }}>
          <div style={{
            width: 80, height: 80, borderRadius: 20,
            background: mode === "incident" ? "var(--red-soft)" : "var(--blue-soft)",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontSize: 36, marginBottom: 20,
          }}>
            {mode === "incident" ? "🚨" : "🏢"}
          </div>
          <div style={{
            fontSize: 17, fontWeight: 700, color: "var(--text-primary)",
            marginBottom: 8,
          }}>
            {mode === "incident"
              ? "Describe an incident to get dispatch recommendations"
              : "Enter any NYC address for a full building report"}
          </div>
          <div style={{
            fontSize: 13, maxWidth: 440, margin: "0 auto",
            lineHeight: 1.7, color: "var(--text-muted)",
          }}>
            {mode === "incident"
              ? "Who to send · Severity · Past incidents · Nearest hospitals & hydrants · Vision analysis"
              : "Violations · Complaints · Fire history · EMS calls · NYPD reports · Elevators · Risk score"}
          </div>
        </div>
      )}

      {/* Loading state */}
      {loading && !triageResult && !lookupResult && (
        <div style={{
          textAlign: "center", padding: "80px 24px",
        }}>
          <div style={{
            width: 48, height: 48, border: `3px solid var(--border)`,
            borderTopColor: mode === "incident" ? "var(--red)" : "var(--blue)",
            borderRadius: "50%", margin: "0 auto 20px",
            animation: "spin 0.8s linear infinite",
          }} />
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
          <div style={{
            fontSize: 14, fontWeight: 600, color: "var(--text-secondary)",
          }}>
            {mode === "incident" ? "Analyzing incident..." : "Looking up property..."}
          </div>
        </div>
      )}
    </div>
  );
}