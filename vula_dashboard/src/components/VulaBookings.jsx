/**
 * VulaBookings.jsx — appointments/scheduling for health/wellness/services tenants.
 * Day view of bookings, quick-book into a free slot, service types, and availability rules.
 * Backend: /v1/bookings/{tenant} (migration 050).
 */
import { useState, useEffect, useCallback } from "react";

const VULA_API = import.meta.env.VITE_API_URL || "https://vula-group-production.up.railway.app";
const C = { surface: "#FFFFFF", border: "#DDD8CE", green: "var(--accent)", text: "#2A2A2A", muted: "#8A8680", alt: "#F0EDE5" };
const STATUS = {
  pending:   { label: "Pending",   color: "#f59e0b" },
  confirmed: { label: "Confirmed", color: "#3b82f6" },
  completed: { label: "Completed", color: "#10b981" },
  cancelled: { label: "Cancelled", color: "#ef4444" },
  no_show:   { label: "No-show",   color: "#6b7280" },
};
const DAYNAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; // ISO 1..7

const today = () => new Date().toISOString().slice(0, 10);
const fmtR = (c) => `R${((c || 0) / 100).toFixed(2)}`;

export default function VulaBookings({ tenantId }) {
  const [view, setView] = useState("day");     // day | services | settings
  const [date, setDate] = useState(today());
  const [bookings, setBookings] = useState([]);
  const [services, setServices] = useState([]);
  const [avail, setAvail] = useState(null);
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const loadDay = useCallback(async () => {
    if (!tenantId) return;
    const from = `${date}T00:00:00+02:00`;
    const [b, a] = await Promise.all([
      fetch(`${VULA_API}/v1/bookings/${tenantId}?from=${encodeURIComponent(from)}`).then(r => r.json()).catch(() => ({})),
      fetch(`${VULA_API}/v1/bookings/${tenantId}/availability?date=${date}`).then(r => r.json()).catch(() => ({})),
    ]);
    const all = (b.bookings || []).filter(x => (x.start_at || "").slice(0, 10) === date
      || new Date(x.start_at).toISOString().slice(0, 10) === date);
    setBookings(all);
    setAvail(a);
  }, [tenantId, date]);

  const loadServices = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/bookings/${tenantId}/services?all=true`).then(r => r.json()).catch(() => ({}));
    setServices(d.services || []);
  }, [tenantId]);

  const loadSettings = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/bookings/${tenantId}/settings`).then(r => r.json()).catch(() => ({}));
    setSettings(d.settings || null);
  }, [tenantId]);

  useEffect(() => { loadDay(); }, [loadDay]);
  useEffect(() => { loadServices(); }, [loadServices]);
  useEffect(() => { if (view === "settings") loadSettings(); }, [view, loadSettings]);

  const setStatus = async (id, status) => {
    await fetch(`${VULA_API}/v1/bookings/${tenantId}/${id}/status`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    loadDay();
  };

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3000); };

  return (
    <div style={{ fontFamily: "system-ui", color: C.text }}>
      {/* Sub-nav */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
        {["day", "services", "settings"].map(v => (
          <button key={v} onClick={() => setView(v)} style={{
            ...btn, ...(view === v ? btnOn : {}),
          }}>{v === "day" ? "📅 Calendar" : v === "services" ? "🧾 Services" : "⚙️ Availability"}</button>
        ))}
        {msg && <span style={{ color: C.green, fontSize: 13 }}>{msg}</span>}
      </div>

      {view === "day" && (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
            <button style={btn} onClick={() => setDate(shift(date, -1))}>← Prev</button>
            <input type="date" value={date} onChange={e => setDate(e.target.value)} style={input} />
            <button style={btn} onClick={() => setDate(shift(date, 1))}>Next →</button>
            <button style={btn} onClick={() => setDate(today())}>Today</button>
          </div>

          <QuickBook tenantId={tenantId} date={date} services={services} avail={avail}
                     busy={busy} setBusy={setBusy} onBooked={() => { loadDay(); flash("Booked ✓"); }} flash={flash} />

          <h4 style={h4}>Appointments — {date}</h4>
          {bookings.length === 0 && <p style={{ color: C.muted, fontSize: 14 }}>No appointments this day.</p>}
          {bookings.map(b => {
            const st = STATUS[b.status] || { label: b.status, color: C.muted };
            const t = new Date(b.start_at).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Johannesburg" });
            return (
              <div key={b.id} style={card}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>{t} · {b.service_name || "Appointment"}</div>
                  <div style={{ fontSize: 13, color: C.muted }}>
                    {b.customer_name || "—"} {b.customer_phone ? `· ${b.customer_phone}` : ""} · via {b.channel}
                  </div>
                  {b.notes && <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>📝 {b.notes}</div>}
                </div>
                <span style={{ ...pill, color: st.color, borderColor: st.color }}>{st.label}</span>
                {b.status !== "cancelled" && b.status !== "completed" && (
                  <div style={{ display: "flex", gap: 4, marginLeft: 8 }}>
                    <button style={miniBtn} onClick={() => setStatus(b.id, "completed")}>Done</button>
                    <button style={miniBtn} onClick={() => setStatus(b.id, "no_show")}>No-show</button>
                    <button style={{ ...miniBtn, color: "#ef4444" }} onClick={() => setStatus(b.id, "cancelled")}>Cancel</button>
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}

      {view === "services" && (
        <ServicesTab tenantId={tenantId} services={services} reload={loadServices} flash={flash} />
      )}

      {view === "settings" && settings && (
        <SettingsTab tenantId={tenantId} settings={settings} onSaved={(s) => { setSettings(s); flash("Saved ✓"); }} />
      )}
    </div>
  );
}

function QuickBook({ tenantId, date, services, avail, busy, setBusy, onBooked, flash }) {
  const [f, setF] = useState({ start: "", service_id: "", customer_name: "", customer_phone: "", notes: "" });
  const slots = avail?.slots || [];
  const book = async () => {
    if (!f.start) return flash("Pick a time");
    setBusy(true);
    const svc = services.find(s => s.id === f.service_id);
    const r = await fetch(`${VULA_API}/v1/bookings/${tenantId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: `${date}T${f.start}`, service_id: f.service_id || null,
        service_name: svc?.name, customer_name: f.customer_name,
        customer_phone: f.customer_phone, notes: f.notes, channel: "dashboard",
      }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    setBusy(false);
    if (r.error) return flash(r.error);
    setF({ start: "", service_id: "", customer_name: "", customer_phone: "", notes: "" });
    onBooked();
  };
  return (
    <div style={{ ...card, flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <div style={{ fontWeight: 600, fontSize: 13 }}>➕ New booking</div>
      {avail?.closed
        ? <div style={{ color: C.muted, fontSize: 13 }}>Closed on this day (see Availability).</div>
        : slots.length === 0
          ? <div style={{ color: C.muted, fontSize: 13 }}>No free slots this day.</div>
          : (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <select value={f.start} onChange={e => setF({ ...f, start: e.target.value })} style={input}>
                <option value="">Time…</option>
                {slots.map(s => <option key={s.start_utc} value={s.label}>{s.label}</option>)}
              </select>
              <select value={f.service_id} onChange={e => setF({ ...f, service_id: e.target.value })} style={input}>
                <option value="">Service…</option>
                {services.filter(s => s.active).map(s => <option key={s.id} value={s.id}>{s.name} ({s.duration_min}m)</option>)}
              </select>
              <input placeholder="Customer name" value={f.customer_name} onChange={e => setF({ ...f, customer_name: e.target.value })} style={input} />
              <input placeholder="Phone" value={f.customer_phone} onChange={e => setF({ ...f, customer_phone: e.target.value })} style={input} />
              <input placeholder="Notes" value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} style={{ ...input, flex: 1 }} />
              <button style={{ ...btn, ...btnOn }} disabled={busy} onClick={book}>{busy ? "…" : "Book"}</button>
            </div>
          )}
    </div>
  );
}

function ServicesTab({ tenantId, services, reload, flash }) {
  const [f, setF] = useState({ name: "", duration_min: 30, price_cents: 0, active: true });
  const save = async (body) => {
    const r = await fetch(`${VULA_API}/v1/bookings/${tenantId}/services`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) return flash(r.error);
    reload();
  };
  const del = async (id) => {
    await fetch(`${VULA_API}/v1/bookings/${tenantId}/services/${id}`, { method: "DELETE" });
    reload();
  };
  return (
    <>
      <h4 style={h4}>Bookable services</h4>
      {services.map(s => (
        <div key={s.id} style={card}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>{s.name} {!s.active && <span style={{ color: C.muted, fontSize: 12 }}>(inactive)</span>}</div>
            <div style={{ fontSize: 13, color: C.muted }}>{s.duration_min} min · {s.price_cents ? fmtR(s.price_cents) : "price on day"}</div>
          </div>
          <button style={miniBtn} onClick={() => save({ ...s, active: !s.active })}>{s.active ? "Disable" : "Enable"}</button>
          <button style={{ ...miniBtn, color: "#ef4444" }} onClick={() => del(s.id)}>Delete</button>
        </div>
      ))}
      <div style={{ ...card, gap: 8 }}>
        <input placeholder="Service name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} style={{ ...input, flex: 1 }} />
        <input type="number" placeholder="Mins" value={f.duration_min} onChange={e => setF({ ...f, duration_min: +e.target.value })} style={{ ...input, width: 80 }} />
        <input type="number" placeholder="Price R" value={f.price_cents / 100 || ""} onChange={e => setF({ ...f, price_cents: Math.round(+e.target.value * 100) })} style={{ ...input, width: 100 }} />
        <button style={{ ...btn, ...btnOn }} onClick={() => { if (f.name) { save(f); setF({ name: "", duration_min: 30, price_cents: 0, active: true }); } }}>Add</button>
      </div>
    </>
  );
}

function SettingsTab({ tenantId, settings, onSaved }) {
  const [s, setS] = useState(settings);
  const toggleDay = (d) => {
    const days = new Set(s.workdays || []);
    days.has(d) ? days.delete(d) : days.add(d);
    setS({ ...s, workdays: [...days].sort() });
  };
  const save = async () => {
    const r = await fetch(`${VULA_API}/v1/bookings/${tenantId}/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slot_minutes: +s.slot_minutes, workdays: s.workdays, open_time: s.open_time,
        close_time: s.close_time, buffer_min: +s.buffer_min, lead_hours: +s.lead_hours,
        horizon_days: +s.horizon_days,
      }),
    }).then(r => r.json()).catch(() => ({}));
    if (r.settings) onSaved(r.settings);
  };
  return (
    <>
      <h4 style={h4}>Availability rules</h4>
      <div style={{ ...card, flexDirection: "column", alignItems: "stretch", gap: 12 }}>
        <div>
          <div style={lbl}>Working days</div>
          <div style={{ display: "flex", gap: 6 }}>
            {DAYNAMES.map((d, i) => {
              const iso = i + 1, on = (s.workdays || []).includes(iso);
              return <button key={d} onClick={() => toggleDay(iso)} style={{ ...miniBtn, ...(on ? btnOn : {}) }}>{d}</button>;
            })}
          </div>
        </div>
        <Row><Field label="Open" ><input type="time" value={s.open_time} onChange={e => setS({ ...s, open_time: e.target.value })} style={input} /></Field>
          <Field label="Close"><input type="time" value={s.close_time} onChange={e => setS({ ...s, close_time: e.target.value })} style={input} /></Field>
          <Field label="Slot (min)"><input type="number" value={s.slot_minutes} onChange={e => setS({ ...s, slot_minutes: e.target.value })} style={{ ...input, width: 90 }} /></Field></Row>
        <Row><Field label="Buffer (min)"><input type="number" value={s.buffer_min} onChange={e => setS({ ...s, buffer_min: e.target.value })} style={{ ...input, width: 90 }} /></Field>
          <Field label="Min notice (hrs)"><input type="number" value={s.lead_hours} onChange={e => setS({ ...s, lead_hours: e.target.value })} style={{ ...input, width: 90 }} /></Field>
          <Field label="Book ahead (days)"><input type="number" value={s.horizon_days} onChange={e => setS({ ...s, horizon_days: e.target.value })} style={{ ...input, width: 90 }} /></Field></Row>
        <button style={{ ...btn, ...btnOn, alignSelf: "flex-start" }} onClick={save}>Save rules</button>
      </div>
    </>
  );
}

const Row = ({ children }) => <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>{children}</div>;
const Field = ({ label, children }) => <div><div style={lbl}>{label}</div>{children}</div>;

function shift(d, n) { const x = new Date(d + "T12:00:00"); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); }

const btn = { padding: "8px 14px", border: `1px solid ${C.border}`, borderRadius: 6, background: C.surface, color: C.text, fontSize: 13, cursor: "pointer" };
const btnOn = { background: C.green, color: "#fff", borderColor: C.green };
const miniBtn = { padding: "4px 10px", border: `1px solid ${C.border}`, borderRadius: 5, background: C.surface, color: C.text, fontSize: 12, cursor: "pointer" };
const input = { padding: "7px 10px", border: `1px solid ${C.border}`, borderRadius: 6, fontSize: 13, background: C.surface, color: C.text };
const card = { display: "flex", alignItems: "center", gap: 10, padding: "12px 14px", border: `1px solid ${C.border}`, borderRadius: 8, background: C.surface, marginBottom: 8 };
const pill = { fontSize: 11, padding: "2px 8px", borderRadius: 10, border: "1px solid", fontWeight: 600 };
const h4 = { fontSize: 14, fontWeight: 600, margin: "16px 0 10px" };
const lbl = { fontSize: 12, color: C.muted, marginBottom: 4 };
