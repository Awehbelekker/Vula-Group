import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL ?? "/api";

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", surfaceAlt: "#F0EDE5",
  border: "#DDD8CE", green: "#2C5545", greenLight: "#3D7260",
  amber: "#D97706", red: "#DC2626", blue: "#1D4ED8",
  charcoal: "#1E1E1E", text: "#2A2A2A", muted: "#8A8680",
};

const PLAN_PRICE = { starter: 1500, growth: 3500, business: 7500 };
const PLAN_COLOR = { starter: C.blue, growth: C.green, business: "#7C3AED" };

function daysUntil(isoDate) {
  if (!isoDate) return null;
  return Math.ceil((new Date(isoDate).getTime() - Date.now()) / 86400000);
}

function subState(t) {
  if (t.paid) return { label: "Paid", color: C.green, priority: 0 };
  const d = daysUntil(t.trial_ends);
  if (d === null) return { label: "Unknown", color: C.muted, priority: 4 };
  if (d < 0) return { label: "Expired", color: C.red, priority: 1 };
  if (d <= 3) return { label: `${d}d left`, color: C.red, priority: 2 };
  if (d <= 7) return { label: `${d}d left`, color: C.amber, priority: 3 };
  return { label: `${d}d trial`, color: C.muted, priority: 5 };
}

function StatCard({ label, value, sub, color = C.green }) {
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: "20px 24px",
    }}>
      <div style={{ fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: C.muted, fontFamily: "'Source Code Pro', monospace", marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 36, fontWeight: 700, color, lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: C.muted, marginTop: 6, fontFamily: "'Source Code Pro', monospace" }}>{sub}</div>}
    </div>
  );
}

function TrialBar({ daysLeft, paid }) {
  if (paid) return null;
  if (daysLeft === null) return null;
  const pct = Math.max(0, Math.min(100, ((30 - Math.max(0, daysLeft)) / 30) * 100));
  const color = daysLeft < 0 ? C.red : daysLeft <= 3 ? C.red : daysLeft <= 7 ? C.amber : C.green;
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2, transition: "width 0.4s" }} />
      </div>
    </div>
  );
}

function TenantRow({ t, selected, onSelect }) {
  const state = subState(t);
  const days = daysUntil(t.trial_ends);
  const planColor = PLAN_COLOR[t.plan] ?? C.muted;

  return (
    <div
      onClick={() => onSelect(selected ? null : t)}
      style={{
        background: selected ? `${C.green}06` : C.surface,
        border: `1px solid ${selected ? C.green + "40" : C.border}`,
        borderRadius: 10, padding: "14px 20px",
        cursor: "pointer", transition: "all 0.15s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>

        {/* Company + contact */}
        <div style={{ flex: "1 1 200px", minWidth: 0 }}>
          <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 17, color: C.charcoal, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {t.company_name}
          </div>
          <div style={{ fontSize: 11, color: C.muted, marginTop: 1 }}>{t.contact_name}</div>
        </div>

        {/* Plan badge */}
        <span style={{
          padding: "3px 10px", borderRadius: 6,
          background: planColor + "14", color: planColor,
          border: `1px solid ${planColor}30`,
          fontSize: 10, fontWeight: 700,
          fontFamily: "'Source Code Pro', monospace",
          letterSpacing: "0.06em", textTransform: "uppercase",
          flexShrink: 0,
        }}>
          {t.plan}
        </span>

        {/* Subscription state */}
        <span style={{
          padding: "3px 10px", borderRadius: 6,
          background: state.color + "14", color: state.color,
          border: `1px solid ${state.color}30`,
          fontSize: 10, fontWeight: 600,
          fontFamily: "'Source Code Pro', monospace",
          flexShrink: 0,
        }}>
          {state.label}
        </span>

        {/* MRR contribution */}
        {t.paid && (
          <span style={{ fontSize: 12, color: C.green, fontFamily: "'Source Code Pro', monospace", flexShrink: 0 }}>
            R{(PLAN_PRICE[t.plan] || 0).toLocaleString("en-ZA")}/mo
          </span>
        )}

        <span style={{ color: C.muted, fontSize: 16, marginLeft: "auto", transform: selected ? "rotate(90deg)" : "none", transition: "transform 0.2s", flexShrink: 0 }}>›</span>
      </div>

      <TrialBar daysLeft={days} paid={t.paid} />

      {/* Expanded detail */}
      {selected && (
        <div style={{
          marginTop: 16, paddingTop: 16,
          borderTop: `1px solid ${C.border}`,
          display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 16,
        }}>
          <Detail label="Email" value={t.email} />
          <Detail label="WhatsApp" value={t.whatsapp || "Not provided"} />
          <Detail label="Trial ends" value={t.trial_ends ? t.trial_ends.slice(0, 10) : "—"} />
          <Detail label="Payment" value={t.paid ? "Paid ✓" : "Unpaid"} color={t.paid ? C.green : C.amber} />
          <Detail label="Status" value={t.status} />
          <Detail label="Signup" value={t.created_at ? new Date(t.created_at).toLocaleDateString("en-ZA") : "—"} />
        </div>
      )}
    </div>
  );
}

function Detail({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: C.muted, fontFamily: "'Source Code Pro', monospace", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, color: color ?? C.text, fontFamily: "system-ui" }}>{value}</div>
    </div>
  );
}

function UrgencySection({ title, color, tenants, selected, onSelect }) {
  if (tenants.length === 0) return null;
  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block", flexShrink: 0 }} />
        <span style={{ fontSize: 12, fontWeight: 700, color: C.text, textTransform: "uppercase", letterSpacing: "0.06em", fontFamily: "'Source Code Pro', monospace" }}>
          {title}
        </span>
        <span style={{ fontSize: 11, color: C.muted, fontFamily: "'Source Code Pro', monospace" }}>({tenants.length})</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {tenants.map((t) => (
          <TenantRow key={t.email} t={t} selected={selected?.email === t.email} onSelect={onSelect} />
        ))}
      </div>
    </div>
  );
}

export default function VulaSubscriptions() {
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState("urgency"); // urgency | all

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${VULA_API}/v1/admin/signups?limit=100`);
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = await resp.json();
      setTenants(data.signups || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Stats
  const paid = tenants.filter((t) => t.paid);
  const mrr = paid.reduce((s, t) => s + (PLAN_PRICE[t.plan] || 0), 0);
  const expiredCount = tenants.filter((t) => !t.paid && daysUntil(t.trial_ends) !== null && daysUntil(t.trial_ends) < 0).length;
  const soonCount = tenants.filter((t) => {
    const d = daysUntil(t.trial_ends);
    return !t.paid && d !== null && d >= 0 && d <= 7;
  }).length;

  // Sort by urgency
  const sorted = [...tenants].sort((a, b) => subState(a).priority - subState(b).priority);

  const sections = [
    { title: "Expired — needs reactivation", color: C.red, filter: (t) => !t.paid && (daysUntil(t.trial_ends) ?? 1) < 0 },
    { title: "Expiring in 3 days", color: C.red, filter: (t) => { const d = daysUntil(t.trial_ends); return !t.paid && d !== null && d >= 0 && d <= 3; } },
    { title: "Expiring this week", color: C.amber, filter: (t) => { const d = daysUntil(t.trial_ends); return !t.paid && d !== null && d > 3 && d <= 7; } },
    { title: "Active paid subscribers", color: C.green, filter: (t) => t.paid },
    { title: "On trial — healthy", color: C.muted, filter: (t) => { const d = daysUntil(t.trial_ends); return !t.paid && d !== null && d > 7; } },
  ];

  return (
    <>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=Source+Code+Pro:wght@400;500&display=swap');`}</style>
      <div style={{ maxWidth: 1000, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 28 }}>
          <div>
            <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 32, fontWeight: 700, color: C.charcoal, marginBottom: 4 }}>
              Subscriptions
            </h1>
            <p style={{ fontSize: 13, color: C.muted, fontFamily: "'Source Code Pro', monospace" }}>
              Trial health · MRR · Conversion pipeline
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            style={{
              padding: "8px 20px", borderRadius: 8,
              border: `1px solid ${C.border}`,
              background: C.surface, color: C.green,
              fontSize: 13, cursor: loading ? "not-allowed" : "pointer",
              fontFamily: "'Source Code Pro', monospace",
            }}
          >
            {loading ? "Loading…" : "↺ Refresh"}
          </button>
        </div>

        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 28 }}>
          <StatCard label="Monthly Recurring Revenue" value={`R${(mrr / 1000).toFixed(1)}k`} sub={`${paid.length} paid client${paid.length !== 1 ? "s" : ""}`} color={C.green} />
          <StatCard label="Total Tenants" value={tenants.length} sub="all plans" />
          <StatCard label="Expiring Soon" value={soonCount} sub="within 7 days" color={soonCount > 0 ? C.amber : C.muted} />
          <StatCard label="Expired" value={expiredCount} sub="needs follow-up" color={expiredCount > 0 ? C.red : C.muted} />
        </div>

        {/* View toggle */}
        <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
          {[{ id: "urgency", label: "By urgency" }, { id: "all", label: "All tenants" }].map((v) => (
            <button
              key={v.id}
              onClick={() => setView(v.id)}
              style={{
                padding: "6px 18px", borderRadius: 20,
                border: `1px solid ${view === v.id ? C.green : C.border}`,
                background: view === v.id ? `${C.green}12` : C.surface,
                color: view === v.id ? C.green : C.muted,
                fontSize: 12, cursor: "pointer",
                fontFamily: "'Source Code Pro', monospace",
              }}
            >
              {v.label}
            </button>
          ))}
        </div>

        {/* Error */}
        {error && (
          <div style={{ padding: 20, borderRadius: 10, background: "#FEF2F2", border: "1px solid #FECACA", fontSize: 13, color: "#991B1B", marginBottom: 20 }}>
            <strong>Could not load:</strong> {error}
          </div>
        )}

        {loading && (
          <div style={{ textAlign: "center", padding: 60, color: C.muted, fontFamily: "'Source Code Pro', monospace", fontSize: 13 }}>
            Loading subscription data…
          </div>
        )}

        {/* Urgency view */}
        {!loading && !error && view === "urgency" && (
          <div>
            {sections.map((s) => (
              <UrgencySection
                key={s.title}
                title={s.title}
                color={s.color}
                tenants={sorted.filter(s.filter)}
                selected={selected}
                onSelect={setSelected}
              />
            ))}
            {tenants.length === 0 && (
              <div style={{ textAlign: "center", padding: 60, background: C.surface, borderRadius: 12, border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 32, marginBottom: 12 }}>💳</div>
                <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 20, color: C.charcoal, marginBottom: 8 }}>No tenants yet</div>
                <p style={{ fontSize: 13, color: C.muted }}>Onboard your first client to see subscription data here.</p>
              </div>
            )}
          </div>
        )}

        {/* All tenants flat list */}
        {!loading && !error && view === "all" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {sorted.map((t) => (
              <TenantRow key={t.email} t={t} selected={selected?.email === t.email} onSelect={setSelected} />
            ))}
          </div>
        )}

        <div style={{ textAlign: "center", marginTop: 40, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
          <p style={{ fontSize: 11, color: C.muted, fontFamily: "'Source Code Pro', monospace" }}>
            Vula Subscriptions · MRR R{mrr.toLocaleString("en-ZA")} · {tenants.length} tenants tracked
          </p>
        </div>
      </div>
    </>
  );
}
