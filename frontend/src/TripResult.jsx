import React from "react";

// ─── Trip Header ──────────────────────────────────────────────────────────────

function fmtDate(d) {
  if (!d) return "—";
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const [, m, day] = d.split("-");
  return `${months[+m - 1]} ${+day}`;
}

function TripHeader({ trip }) {
  return (
    <div className="trip-header">
      <div className="trip-route">
        <span className="trip-origin">{trip.origin}</span>
        <span className="trip-arrow">→</span>
        <span className="trip-dest">{trip.destination}</span>
      </div>
      <div className="trip-meta-row">
        <span className="trip-meta-pill">{fmtDate(trip.depart_date)} – {fmtDate(trip.return_date)}</span>
        <span className="trip-meta-pill">{trip.nights} night{trip.nights !== 1 ? "s" : ""}</span>
        <span className="trip-meta-pill">{trip.travellers} traveller{trip.travellers !== 1 ? "s" : ""}</span>
        <span className="trip-meta-pill">{trip.currency}</span>
      </div>
    </div>
  );
}

// ─── Generic card wrapper ─────────────────────────────────────────────────────

function Card({ icon, title, color, bg, border, children }) {
  return (
    <div className="trip-card" style={{ "--sc": color, "--sb": bg, "--sbr": border }}>
      <div className="trip-card-hdr">
        <span className="card-icon">{icon}</span>
        <span className="card-title">{title}</span>
      </div>
      <div className="trip-card-body">
        {children}
      </div>
    </div>
  );
}

// ─── Data table ───────────────────────────────────────────────────────────────

function DataTable({ headers, rows }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => <td key={j}>{cell ?? "—"}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Transport ────────────────────────────────────────────────────────────────

function TransportRows({ rows }) {
  if (!rows || rows.length === 0) return null;
  return rows.map((r, i) => (
    <tr key={i}>
      <td className="cell-strong">{r.operator}</td>
      <td>{r.depart}</td>
      <td>{r.arrive}</td>
      <td>{r.duration}</td>
      <td className="cell-price">{r.price_per_person}</td>
      <td>
        {r.url
          ? <a href={r.url} className="book-link" target="_blank" rel="noopener noreferrer">Book →</a>
          : "—"}
      </td>
    </tr>
  ));
}

function TransportSection({ transport, trip }) {
  if (!transport) return null;
  const headers = ["Operator", "Depart", "Arrive", "Duration", "Price/person", "Book"];
  return (
    <Card icon={transport.emoji || "✈️"} title="Travel Options" color="#3b82f6" bg="#eff6ff" border="#bfdbfe">
      {transport.outbound?.length > 0 && (
        <>
          <div className="subtable-label">Outbound · {fmtDate(trip?.depart_date)}</div>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
              <tbody><TransportRows rows={transport.outbound} /></tbody>
            </table>
          </div>
        </>
      )}
      {transport.return_trips?.length > 0 && (
        <>
          <div className="subtable-label" style={{ marginTop: 16 }}>Return · {fmtDate(trip?.return_date)}</div>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
              <tbody><TransportRows rows={transport.return_trips} /></tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

// ─── Accommodation ────────────────────────────────────────────────────────────

function Stars({ n }) {
  if (!n) return <span className="no-val">—</span>;
  return <span className="stars">{"★".repeat(n)}{"☆".repeat(Math.max(0, 5 - n))}</span>;
}

function AccommodationSection({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <Card icon="🏨" title="Accommodation" color="#f97316" bg="#fff7ed" border="#fed7aa">
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Property</th><th>Type</th><th>Area</th><th>Stars</th>
              <th>Per night</th><th>Stay total</th><th>Book</th>
            </tr>
          </thead>
          <tbody>
            {items.map((h, i) => (
              <tr key={i}>
                <td className="cell-strong">{h.name}</td>
                <td>{h.type}</td>
                <td>{h.neighbourhood || "—"}</td>
                <td><Stars n={h.stars} /></td>
                <td className="cell-price">{h.price_per_night}</td>
                <td className="cell-price">{h.total_stay}</td>
                <td>
                  {h.url
                    ? <a href={h.url} className="book-link" target="_blank" rel="noopener noreferrer">Book →</a>
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ─── Budget ───────────────────────────────────────────────────────────────────

const TIER_CFG = [
  { key: "economy",   label: "Economy",   color: "#3b82f6", bg: "#eff6ff", border: "#bfdbfe" },
  { key: "mid_range", label: "Mid-Range", color: "#f59e0b", bg: "#fffbeb", border: "#fde68a" },
  { key: "comfort",   label: "Comfort",   color: "#8b5cf6", bg: "#f5f3ff", border: "#ddd6fe" },
];

const BUDGET_ROWS = [
  { key: "transport",     label: "Transport" },
  { key: "accommodation", label: "Accommodation" },
  { key: "meals",         label: "Meals" },
  { key: "activities",    label: "Activities" },
];

function BudgetSection({ budget, trip }) {
  if (!budget) return null;
  return (
    <Card
      icon="💰"
      title={`Budget · ${trip?.travellers ?? ""} traveller${trip?.travellers !== 1 ? "s" : ""} · ${trip?.nights ?? ""} nights`}
      color="#10b981" bg="#ecfdf5" border="#a7f3d0"
    >
      <div className="tier-grid">
        {TIER_CFG.map(({ key, label, color, bg, border }) => {
          const tier = budget[key];
          if (!tier) return null;
          return (
            <div key={key} className="tier-card" style={{ "--tc": color, "--tb": bg, "--tbr": border }}>
              <div className="tier-name">{label}</div>
              <div className="tier-total">{tier.total}</div>
              {tier.per_person && (
                <div className="tier-pp">{tier.per_person}<span> / person</span></div>
              )}
              <div className="tier-items">
                {BUDGET_ROWS.map(({ key: rk, label: rl }) => (
                  <div key={rk} className="tier-item">
                    <span className="tier-item-label">{rl}</span>
                    <span className="tier-item-val">{tier[rk] || "—"}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {budget.notes && <p className="budget-note">{budget.notes}</p>}
    </Card>
  );
}

// ─── Itinerary ────────────────────────────────────────────────────────────────

function TimeSlot({ icon, label, text }) {
  if (!text) return null;
  return (
    <div className="time-slot">
      <div className="time-label">{icon} {label}</div>
      <div className="time-text">{text}</div>
    </div>
  );
}

function ItinerarySection({ days }) {
  if (!days || days.length === 0) return null;
  return (
    <Card icon="📅" title="Itinerary" color="#8b5cf6" bg="#f5f3ff" border="#ddd6fe">
      <div className="day-list">
        {days.map((d, i) => (
          <div key={i} className="day-card">
            <div className="day-hdr">
              <span className="day-number">Day {d.day}</span>
              <span className="day-date">{d.date}</span>
              {d.label && <span className="day-label">{d.label}</span>}
            </div>
            <div className="day-body">
              <TimeSlot icon="🌅" label="Morning" text={d.morning} />
              <TimeSlot icon="☀️" label="Afternoon" text={d.afternoon} />
              <TimeSlot icon="🌙" label="Evening" text={d.evening} />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ─── Getting Around ───────────────────────────────────────────────────────────

function GettingAroundSection({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <Card icon="🚇" title="Getting Around" color="#0ea5e9" bg="#f0f9ff" border="#bae6fd">
      <DataTable
        headers={["Option", "Cost", "Notes"]}
        rows={items.map(r => [
          <span className="cell-strong">{r.option}</span>,
          <span className="cell-price">{r.cost}</span>,
          r.notes,
        ])}
      />
    </Card>
  );
}

// ─── Data Notes ───────────────────────────────────────────────────────────────

function DataNotesSection({ notes }) {
  const items = [
    ...(notes.fetch_failed || []).map(s => ({ type: "failed",   text: s })),
    ...(notes.estimates    || []).map(s => ({ type: "estimate", text: s })),
    ...(notes.missing      || []).map(s => ({ type: "missing",  text: s })),
  ];
  if (items.length === 0) return null;
  return (
    <Card icon="⚠️" title="Data Quality Notes" color="#ef4444" bg="#fef2f2" border="#fecaca">
      <ul className="notes-list">
        {items.map((n, i) => (
          <li key={i} className={`note-item note-item--${n.type}`}>{n.text}</li>
        ))}
      </ul>
    </Card>
  );
}

// ─── Main Export ──────────────────────────────────────────────────────────────

export default function TripResult({ data }) {
  if (!data) return null;
  const { trip, transport, accommodation, budget, itinerary, getting_around, data_notes } = data;
  return (
    <div className="trip-result">
      {trip && <TripHeader trip={trip} />}
      <TransportSection transport={transport} trip={trip} />
      <AccommodationSection items={accommodation} />
      <BudgetSection budget={budget} trip={trip} />
      <ItinerarySection days={itinerary} />
      <GettingAroundSection items={getting_around} />
      {data_notes && <DataNotesSection notes={data_notes} />}
    </div>
  );
}
