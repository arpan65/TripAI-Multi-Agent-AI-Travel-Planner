import React, { useEffect, useRef, useState } from "react";
import TripResult from "./TripResult";

class ResultBoundary extends React.Component {
  state = { err: null };
  static getDerivedStateFromError(e) { return { err: e }; }
  render() {
    if (this.state.err) return (
      <div className="error-banner">
        <span>⚠️</span>
        <span>Render error: {this.state.err.message}</span>
      </div>
    );
    return this.props.children;
  }
}

const API_BASE = import.meta.env.VITE_API_URL || "";

const PHASES = [
  { icon: "🗺️", label: "Planning your route" },
  { icon: "🔍", label: "Researching travel options" },
  { icon: "💰", label: "Extracting live prices" },
  { icon: "📊", label: "Calculating budget tiers" },
  { icon: "📋", label: "Finalising your dossier" },
];

const BUDGET_OPTIONS = [
  { value: "any", label: "Any" },
  { value: "economy", label: "Economy" },
  { value: "mid-range", label: "Mid-Range" },
  { value: "comfort", label: "Comfort" },
];

const TRANSPORT_OPTIONS = [
  { value: "any",    label: "Any" },
  { value: "flight", label: "✈️ Flight" },
  { value: "train",  label: "🚆 Train" },
  { value: "bus",    label: "🚌 Bus" },
  { value: "drive",  label: "🚗 Car" },
];

const toSafeString = (value) => {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
};

const today = () => new Date().toISOString().split("T")[0];
const addDays = (base, n) => {
  const d = new Date(base);
  d.setDate(d.getDate() + n);
  return d.toISOString().split("T")[0];
};

export default function App() {
  const [form, setForm] = useState({
    from: "",
    to: "",
    depart: addDays(today(), 7),
    returnDate: addDays(today(), 10),
    pax: 2,
    budget: "any",
    transport: "any",
  });
  const [loading, setLoading] = useState(false);
  const [phaseIdx, setPhaseIdx] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [testMode, setTestMode] = useState(false);
  const resultsRef = useRef(null);

  useEffect(() => {
    if (!loading) return;
    setPhaseIdx(0);
    const timer = setInterval(() => {
      setPhaseIdx((i) => Math.min(i + 1, PHASES.length - 1));
    }, 22000);
    return () => clearInterval(timer);
  }, [loading]);

  useEffect(() => {
    if (result && resultsRef.current) {
      resultsRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [result]);

  const set = (key) => (e) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const buildMessage = () => {
    const fmt = (d) => {
      const [y, m, day] = d.split("-");
      return `${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m-1]} ${+day} ${y}`;
    };
    const budgetPart = form.budget !== "any" ? `, ${form.budget} budget` : "";
    return `${form.from} to ${form.to}, ${fmt(form.depart)} to ${fmt(form.returnDate)}, ${form.pax} ${form.pax === 1 ? "person" : "people"}${budgetPart}`;
  };

  const handleTestModeToggle = async (enabled) => {
    setTestMode(enabled);
    if (!enabled) return;
    try {
      const res = await fetch(`${API_BASE}/api/latest-run`);
      if (!res.ok) return;
      const data = await res.json();
      // Parse "City to City, Mon DD YYYY to Mon DD YYYY, N people" back into form fields
      const msg = data.input_message || "";
      // Strip optional "Preferred transport: X. " prefix
      const stripped = msg.replace(/^Preferred transport:[^.]+\.\s*/i, "");
      const m = stripped.match(/^(.+?) to (.+?),\s*(.+?) to (.+?),\s*(\d+)/);
      if (m) {
        const parseDate = (s) => {
          const d = new Date(s);
          return isNaN(d) ? "" : d.toISOString().split("T")[0];
        };
        setForm((f) => ({
          ...f,
          from: m[1].trim(),
          to: m[2].trim(),
          depart: parseDate(m[3]),
          returnDate: parseDate(m[4]),
          pax: parseInt(m[5], 10) || f.pax,
        }));
      }
    } catch { /* silent — backend may not have runs yet */ }
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!form.from.trim() || !form.to.trim() || loading) return;
    setError("");
    setResult(null);
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: buildMessage(),
          session_id: sessionId,
          test_mode: testMode,
          transport_mode: form.transport,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Unable to reach backend");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let data;
          try { data = JSON.parse(line.slice(6)); } catch { continue; }

          if (data.type === "session") setSessionId(data.session_id);
          else if (data.type === "phase") {
            const idx = PHASES.findIndex((p) => data.phase.includes(p.icon));
            if (idx >= 0) setPhaseIdx(idx);
          }
          else if (data.type === "result") {
            try {
              const parsed = JSON.parse(data.reply);
              if (parsed.error) throw new Error(parsed.error);
              setResult(parsed);
            } catch {
              throw new Error("Failed to parse travel data from agent");
            }
          }
          else if (data.type === "error") throw new Error(data.message || "Agent error");
        }
      }
    } catch (err) {
      setError(toSafeString(err?.message ?? err) || "Unexpected error");
    } finally {
      setLoading(false);
    }
  };

  const handleNewSearch = () => {
    setResult(null);
    setError("");
  };

  return (
    <div className="page">
      {/* ── NAV ── */}
      <nav className="nav">
        <div className="nav-brand">
          <span className="nav-logo">✈️</span>
          <span className="nav-title">TripAI</span>
        </div>
        {result && (
          <button className="btn-ghost" onClick={handleNewSearch}>
            + New Search
          </button>
        )}
      </nav>

      {/* ── HERO ── */}
      <section className="hero">
        <div className="hero-inner">
          <h1 className="hero-headline">Where to next?</h1>
          <p className="hero-sub">
            AI agents search real booking sites, pull live prices, and build your trip dossier.
          </p>

          {/* ── SEARCH CARD ── */}
          <form className="search-card" onSubmit={handleSearch}>
            <div className="search-fields">

              <div className="field-group">
                <label>From</label>
                <input
                  type="text"
                  placeholder="Toronto"
                  value={form.from}
                  onChange={set("from")}
                  disabled={loading}
                  required
                />
              </div>

              <div className="divider" />

              <div className="field-group">
                <label>To</label>
                <input
                  type="text"
                  placeholder="Montreal"
                  value={form.to}
                  onChange={set("to")}
                  disabled={loading}
                  required
                />
              </div>

              <div className="divider" />

              <div className="field-group">
                <label>Depart</label>
                <input
                  type="date"
                  value={form.depart}
                  min={today()}
                  onChange={set("depart")}
                  disabled={loading}
                />
              </div>

              <div className="divider" />

              <div className="field-group">
                <label>Return</label>
                <input
                  type="date"
                  value={form.returnDate}
                  min={form.depart}
                  onChange={set("returnDate")}
                  disabled={loading}
                />
              </div>

              <div className="divider" />

              <div className="field-group field-group--narrow">
                <label>Travellers</label>
                <div className="pax-control">
                  <button
                    type="button"
                    className="pax-btn"
                    onClick={() => setForm((f) => ({ ...f, pax: Math.max(1, f.pax - 1) }))}
                    disabled={loading || form.pax <= 1}
                  >−</button>
                  <span className="pax-val">{form.pax}</span>
                  <button
                    type="button"
                    className="pax-btn"
                    onClick={() => setForm((f) => ({ ...f, pax: Math.min(9, f.pax + 1) }))}
                    disabled={loading || form.pax >= 9}
                  >+</button>
                </div>
              </div>

              <div className="divider" />

              <div className="field-group field-group--narrow">
                <label>Budget</label>
                <select value={form.budget} onChange={set("budget")} disabled={loading}>
                  {BUDGET_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              <div className="divider" />

              <div className="field-group field-group--narrow">
                <label>Transport</label>
                <select value={form.transport} onChange={set("transport")} disabled={loading}>
                  {TRANSPORT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

            </div>

            <div className="search-card-footer">
              <label className="toggle-label">
                <input
                  type="checkbox"
                  className="toggle-input"
                  checked={testMode}
                  onChange={(e) => handleTestModeToggle(e.target.checked)}
                  disabled={loading}
                />
                <span className="toggle-switch" />
                <span className="toggle-text">Test Mode</span>
              </label>
              {testMode && (
                <span className="test-mode-badge">Uses last stored result — no API calls</span>
              )}
            </div>

            <button
              type="submit"
              className="btn-search"
              disabled={loading || !form.from.trim() || !form.to.trim()}
            >
              {loading ? (
                <span className="spinner" />
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
                </svg>
              )}
            </button>
          </form>
        </div>
      </section>

      {/* ── PROGRESS ── */}
      {loading && (
        <section className="progress-section">
          <div className="progress-track">
            {PHASES.map((p, i) => (
              <div key={i} className={`progress-step ${i < phaseIdx ? "done" : i === phaseIdx ? "active" : ""}`}>
                <div className="step-dot">
                  {i < phaseIdx ? (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : (
                    <span>{p.icon}</span>
                  )}
                </div>
                <span className="step-label">{p.label}</span>
              </div>
            ))}
          </div>
          <p className="progress-hint">This takes 2–4 minutes — agents are browsing real booking pages.</p>
        </section>
      )}

      {/* ── ERROR ── */}
      {error && (
        <div className="error-banner">
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {/* ── RESULTS ── */}
      {result && (
        <section className="results-section" ref={resultsRef}>
          <div className="results-meta">
            <span className="results-route">{form.from} → {form.to}</span>
            <span className="results-badge">{form.pax} traveller{form.pax !== 1 ? "s" : ""}</span>
          </div>
          <ResultBoundary>
            <TripResult data={result} />
          </ResultBoundary>
        </section>
      )}

      <footer className="footer">
        Powered by Claude AI · live data via Playwright · not affiliated with any booking platform
      </footer>
    </div>
  );
}
