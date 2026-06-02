import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";

const COLORS = {
  bg: "#F7F4EE",
  surface: "#FFFFFF",
  surfaceAlt: "#F0EDE5",
  border: "#DDD8CE",
  green: "#2C5545",
  greenLight: "#3D7260",
  amber: "#C4861A",
  charcoal: "#1E1E1E",
  text: "#2A2A2A",
  muted: "#8A8680",
  mutedLight: "#B5B0A8",
};

const PLAN_COLORS = {
  starter: COLORS.muted,
  growth: COLORS.amber,
  business: COLORS.green,
};

function Badge({ children, color = COLORS.green }) {
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px",
      background: `${color}18`, color,
      borderRadius: 20, fontSize: 11,
      fontFamily: "'Source Code Pro', monospace",
      letterSpacing: "0.05em", fontWeight: 500,
    }}>{children}</span>
  );
}

function StatusDot({ status }) {
  const color = status === "active" ? "#22C55E" : status === "provisioning" ? COLORS.amber : COLORS.muted;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block" }} />
      <span style={{ fontSize: 11, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>{status}</span>
    </span>
  );
}

function SignupCard({ signup }) {
  const [expanded, setExpanded] = useState(false);

  const daysLeft = signup.trial_ends
    ? Math.max(0, Math.ceil((new Date(signup.trial_ends) - Date.now()) / 86400000))
    : null;

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      style={{
        background: COLORS.surface,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 10, padding: "16px 20px",
        cursor: "pointer", transition: "box-shadow 0.15s",
        boxShadow: expanded ? "0 2px 12px rgba(0,0,0,0.08)" : "none",
      }}
    >
      {/* Row */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "0 0 auto" }}>
          <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 18, color: COLORS.charcoal, fontWeight: 600 }}>
            {signup.company_name}
          </div>
          <div style={{ fontSize: 12, color: COLORS.muted, fontFamily: "system-ui" }}>
            {signup.contact_name}
          </div>
        </div>

        <div style={{ flex: 1 }} />

        <Badge color={PLAN_COLORS[signup.plan] || COLORS.muted}>
          {signup.plan}
        </Badge>

        <StatusDot status={signup.status} />

        {daysLeft !== null && (
          <span style={{
            fontSize: 11, color: daysLeft < 7 ? "#EF4444" : COLORS.muted,
            fontFamily: "'Source Code Pro', monospace",
          }}>
            {daysLeft}d trial left
          </span>
        )}

        <span style={{ fontSize: 13, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
          {signup.created_at ? new Date(signup.created_at).toLocaleDateString("en-ZA") : "—"}
        </span>

        <span style={{ color: COLORS.muted, fontSize: 16, transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.2s" }}>›</span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          marginTop: 16, paddingTop: 16,
          borderTop: `1px solid ${COLORS.border}`,
          display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12,
        }}>
          <Detail label="Email" value={signup.email} />
          <Detail label="WhatsApp" value={signup.whatsapp || "Not provided"} />
          <Detail label="Industry" value={signup.industry} />
          <Detail label="Trial ends" value={signup.trial_ends ? signup.trial_ends.slice(0, 10) : "—"} />
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: COLORS.muted, fontFamily: "'Source Code Pro', monospace", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 13, color: COLORS.text, fontFamily: "system-ui" }}>{value}</div>
    </div>
  );
}

function StatCard({ label, value, sub, color = COLORS.green }) {
  return (
    <div style={{
      background: COLORS.surface,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 10, padding: "20px 24px",
    }}>
      <div style={{ fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: COLORS.muted, fontFamily: "'Source Code Pro', monospace", marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 36, fontWeight: 700, color, lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 12, color: COLORS.muted, marginTop: 4, fontFamily: "'Source Code Pro', monospace" }}>{sub}</div>}
    </div>
  );
}

export default function VulaAdmin() {
  const [signups, setSignups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(`${VULA_API}/v1/admin/signups?limit=50`);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const data = await resp.json();
      setSignups(data.signups || []);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = filter === "all" ? signups : signups.filter(s => s.plan === filter || s.status === filter);

  // Stats
  const active = signups.filter(s => s.status === "active").length;
  const mrr = signups.filter(s => s.status === "active").reduce((sum, s) => {
    const prices = { starter: 1500, growth: 3500, business: 7500 };
    return sum + (prices[s.plan] || 0);
  }, 0);
  const byPlan = signups.reduce((acc, s) => { acc[s.plan] = (acc[s.plan] || 0) + 1; return acc; }, {});

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600;700&family=Source+Code+Pro:wght@400;500&display=swap');
      `}</style>

      <div style={{ maxWidth: 1000, margin: "0 auto", padding: "32px 24px", fontFamily: "system-ui" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 28 }}>
          <div>
            <h1 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 32, fontWeight: 700, color: COLORS.charcoal, marginBottom: 4 }}>
              Client Signups
            </h1>
            <p style={{ fontSize: 13, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace" }}>
              Follow-up pipeline — call new signups within 24 hours
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            style={{
              padding: "8px 20px", borderRadius: 8,
              border: `1px solid ${COLORS.border}`,
              background: COLORS.surface, color: COLORS.green,
              fontSize: 13, cursor: loading ? "not-allowed" : "pointer",
              fontFamily: "'Source Code Pro', monospace",
            }}
          >
            {loading ? "Loading..." : "↺ Refresh"}
          </button>
        </div>

        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 28 }}>
          <StatCard label="Total Signups" value={signups.length} />
          <StatCard label="Active Clients" value={active} color={COLORS.greenLight} />
          <StatCard label="MRR" value={`R${(mrr / 1000).toFixed(0)}k`} sub="active clients only" color={COLORS.amber} />
          <StatCard label="Business" value={byPlan.business || 0} sub={`Growth: ${byPlan.growth || 0} · Starter: ${byPlan.starter || 0}`} />
        </div>

        {/* Filters */}
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          {["all", "active", "provisioning", "business", "growth", "starter"].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: "6px 16px", borderRadius: 20,
                border: `1px solid ${filter === f ? COLORS.green : COLORS.border}`,
                background: filter === f ? `${COLORS.green}12` : COLORS.surface,
                color: filter === f ? COLORS.green : COLORS.muted,
                fontSize: 12, cursor: "pointer",
                fontFamily: "'Source Code Pro', monospace",
                transition: "all 0.15s",
              }}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Content */}
        {error && (
          <div style={{
            padding: 20, borderRadius: 10,
            background: "#FEF2F2", border: "1px solid #FECACA",
            fontSize: 13, color: "#991B1B",
            marginBottom: 20,
          }}>
            <strong>Could not load signups:</strong> {error}
            {!error.includes("configured") && (
              <div style={{ marginTop: 8, opacity: 0.8 }}>
                Make sure the API server is running and Supabase is configured in .env
              </div>
            )}
          </div>
        )}

        {loading && (
          <div style={{ textAlign: "center", padding: 60, color: COLORS.muted, fontFamily: "'Source Code Pro', monospace", fontSize: 13 }}>
            Loading signups...
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div style={{
            textAlign: "center", padding: 60,
            background: COLORS.surface, borderRadius: 12,
            border: `1px solid ${COLORS.border}`,
          }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
            <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 20, color: COLORS.charcoal, marginBottom: 8 }}>
              {filter === "all" ? "No signups yet" : `No ${filter} clients`}
            </div>
            <p style={{ fontSize: 13, color: COLORS.muted, maxWidth: 360, margin: "0 auto" }}>
              {filter === "all"
                ? "Once clients use the Onboard tab, they'll appear here for follow-up."
                : "Change the filter to see other clients."}
            </p>
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {filtered.map((s, i) => (
              <SignupCard key={s.email || i} signup={s} />
            ))}
          </div>
        )}

        {/* Footer */}
        <div style={{ textAlign: "center", marginTop: 40, paddingTop: 24, borderTop: `1px solid ${COLORS.border}` }}>
          <p style={{ fontSize: 11, color: COLORS.mutedLight, fontFamily: "'Source Code Pro', monospace" }}>
            Vula Admin · Internal use only · {signups.length} total signups
          </p>
        </div>
      </div>
    </>
  );
}
