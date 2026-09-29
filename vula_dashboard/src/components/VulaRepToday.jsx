/**
 * VulaRepToday.jsx — a sales rep's day: today's appointments, and minutes on each meeting.
 *
 * 2026-09-29 (Ian: "sales reps need their appointments for the day and a note button / minutes
 * on the meeting"). Appointments are the rep's own diary (commerce_bookings.booked_by). "Minutes"
 * runs the same pipeline as a WhatsApp voice note (log_meeting): a summary, the action items as
 * real reminders, filed against the client, added to the call sheet, and the meeting-minutes PDF
 * sent to the rep's WhatsApp. Minutes can be typed or dictated (the phone's own speech-to-text).
 * Without a repPhone (an owner's Home) it shows the whole business's day.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { VULA_API } from "../lib/authFetch";
import { Button, Modal, Input, Textarea, Skeleton, ErrorCard, toast } from "./ui/index.jsx";

const API = (t) => `${VULA_API}/v1/commerce/${t}/admin`;
const todayLocal = () => new Date().toLocaleDateString("en-CA");        // YYYY-MM-DD

function useDictation(onText) {
  const rec = useRef(null);
  const [on, setOn] = useState(false);
  const SR = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);
  const start = () => {
    if (!SR) return;
    const r = new SR();
    r.lang = "en-ZA"; r.continuous = true; r.interimResults = false;
    r.onresult = (e) => {
      const text = Array.from(e.results).slice(e.resultIndex).map((x) => x[0].transcript).join(" ");
      if (text.trim()) onText(text.trim());
    };
    r.onend = () => setOn(false);
    r.onerror = () => setOn(false);
    rec.current = r; r.start(); setOn(true);
  };
  const stop = () => { rec.current?.stop(); setOn(false); };
  return { supported: !!SR, on, start, stop };
}

function MinutesModal({ open, onClose, tenantId, repPhone, appointment, onSaved }) {
  const [notes, setNotes] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const dict = useDictation((t) => setNotes((n) => (n ? n + " " : "") + t));
  useEffect(() => { if (open) { setNotes(""); setContact(""); } }, [open]);
  const save = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${API(tenantId)}/meetings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes, rep_phone: repPhone, booking_id: appointment?.id, contact: contact || undefined }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(d.detail || "Couldn't save the minutes.", "danger"); return; }
      const n = (d.action_items || []).length;
      toast(`Minutes saved${n ? ` · ${n} action item${n === 1 ? "" : "s"} set as reminders` : ""}${d.pdf_sent ? " · PDF sent to your WhatsApp" : ""}.`);
      onSaved?.(); onClose();
    } finally { setBusy(false); }
  };
  const who = appointment ? `${appointment.customer_name || "the client"} · ${appointment.start_local || ""}` : "";
  return (
    <Modal open={open} onClose={onClose} title={appointment ? "Meeting minutes" : "Notes for a meeting"} width={560}
      actions={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button onClick={save} disabled={busy || notes.trim().length < 3}>{busy ? "Saving…" : "Save minutes"}</Button>
      </>}>
      {who && <p style={{ margin: "0 0 8px", color: "var(--muted)", fontSize: 13 }}>{who}</p>}
      {!appointment && <Input placeholder="Who was it with? (name or phone)" value={contact} onChange={(e) => setContact(e.target.value)} style={{ marginBottom: 8 }} />}
      <Textarea placeholder="What was discussed, what was agreed, who does what by when…" value={notes}
        onChange={(e) => setNotes(e.target.value)} style={{ minHeight: 160 }} autoFocus />
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
        {dict.supported && (dict.on
          ? <Button variant="danger" size="sm" onClick={dict.stop}>■ Stop dictating</Button>
          : <Button variant="soft" size="sm" onClick={dict.start}>🎙 Dictate</Button>)}
        <span style={{ fontSize: 12, color: "var(--muted)" }}>Vula writes the summary, turns action items into reminders and sends you the minutes PDF.</span>
      </div>
    </Modal>
  );
}

function AddAppointmentModal({ open, onClose, tenantId, repPhone, onSaved }) {
  const blank = { customer_name: "", customer_phone: "", date: todayLocal(), time: "10:00", duration_min: 60, location: "", notes: "" };
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setF(blank); }, [open]);  // eslint-disable-line
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${API(tenantId)}/rep/appointments`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...f, start: `${f.date}T${f.time}`, rep_phone: repPhone }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(d.detail || "Couldn't book that.", "danger"); return; }
      toast("Appointment added."); onSaved?.(); onClose();
    } finally { setBusy(false); }
  };
  const grid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 };
  return (
    <Modal open={open} onClose={onClose} title="New appointment" width={520}
      actions={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button onClick={save} disabled={busy || !f.customer_name.trim()}>{busy ? "Saving…" : "Add appointment"}</Button>
      </>}>
      <div style={{ display: "grid", gap: 8 }}>
        <Input placeholder="Client or company" value={f.customer_name} onChange={set("customer_name")} autoFocus />
        <div style={grid}>
          <Input type="date" value={f.date} onChange={set("date")} aria-label="Date" />
          <Input type="time" value={f.time} onChange={set("time")} aria-label="Time" />
          <Input type="number" min="15" step="15" value={f.duration_min} onChange={set("duration_min")} aria-label="Minutes" />
        </div>
        <Input placeholder="Where (site, office, address)" value={f.location} onChange={set("location")} />
        <Input placeholder="Client phone (optional)" value={f.customer_phone} onChange={set("customer_phone")} />
        <Textarea placeholder="What it's about (optional)" value={f.notes} onChange={set("notes")} style={{ minHeight: 60 }} />
      </div>
    </Modal>
  );
}

export default function VulaRepToday({ tenantId, repPhone }) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [minutesFor, setMinutesFor] = useState(undefined);   // undefined = closed, null = unscheduled
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    setFailed(false);
    try {
      const q = repPhone ? `?rep_phone=${encodeURIComponent(repPhone)}` : "";
      const r = await fetch(`${API(tenantId)}/rep/today${q}`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
    } catch { setFailed(true); }
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  if (failed) return <ErrorCard what="today's appointments" onRetry={load} />;
  const aps = data?.appointments || [];
  const notes = data?.notes || [];
  const now = new Date().toTimeString().slice(0, 5);
  const dayLabel = new Date().toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="vula-panel" style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--r-card)", padding: 16, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{ flex: "1 1 200px" }}>
          <div className="vula-display" style={{ fontSize: 20, fontWeight: 600, color: "var(--ink)" }}>📅 Today</div>
          <div style={{ fontSize: 12.5, color: "var(--muted)" }}>{dayLabel}</div>
        </div>
        <Button size="sm" variant="soft" onClick={() => setMinutesFor(null)}>📝 Meeting notes</Button>
        <Button size="sm" onClick={() => setAdding(true)}>＋ Appointment</Button>
      </div>

      {!data && <><Skeleton height={16} /><Skeleton height={16} style={{ marginTop: 8 }} /></>}
      {data && aps.length === 0 && (
        <p style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0" }}>No appointments today. Add one, or log notes for a meeting you had.</p>)}
      {aps.map((a) => {
        const past = (a.end_local || a.start_local || "") < now;
        return (
          <div key={a.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "10px 0", borderTop: "1px solid var(--border-soft)", flexWrap: "wrap" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: past ? "var(--muted)" : "var(--ink)", minWidth: 92 }}>
              {a.start_local}{a.end_local ? `–${a.end_local}` : ""}
            </div>
            <div style={{ flex: "1 1 180px", minWidth: 0 }}>
              <div style={{ fontWeight: 600, color: "var(--text)" }}>{a.customer_name || a.service_name || "Meeting"}</div>
              <div style={{ fontSize: 12, color: "var(--muted)" }}>
                {[a.location, a.service_name && a.service_name !== "Meeting" ? a.service_name : null, a.notes].filter(Boolean).join(" · ") || " "}
              </div>
            </div>
            {a.has_minutes
              ? <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ok)", padding: "3px 10px", borderRadius: "var(--r-pill)", background: "var(--ok-soft)" }}>✓ Minutes saved</span>
              : <Button size="sm" variant={past ? "primary" : "ghost"} onClick={() => setMinutesFor(a)}>📝 Minutes</Button>}
          </div>);
      })}

      {notes.filter((n) => !n.booking_id).length > 0 && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--border-soft)" }}>
          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)", marginBottom: 4 }}>Other notes today</div>
          {notes.filter((n) => !n.booking_id).map((n) => (
            <div key={n.id} style={{ fontSize: 13, color: "var(--text)", padding: "3px 0" }}>• {n.summary}</div>))}
        </div>)}

      <MinutesModal open={minutesFor !== undefined} onClose={() => setMinutesFor(undefined)} tenantId={tenantId}
        repPhone={repPhone} appointment={minutesFor || null} onSaved={load} />
      <AddAppointmentModal open={adding} onClose={() => setAdding(false)} tenantId={tenantId} repPhone={repPhone} onSaved={load} />
    </div>
  );
}
