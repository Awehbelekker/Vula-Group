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
import { Button, Modal, Input, Textarea, Skeleton, ErrorCard } from "./ui/index.jsx";
import { useAppointmentForm, useMinutesForm, useRepToday } from "./rep/useRepToday";
import RepTodayV2 from "./rep/RepTodayV2";

function MinutesModal({ open, onClose, tenantId, repPhone, appointment, onSaved }) {
  const { notes, setNotes, contact, setContact, busy, dict, save } = useMinutesForm({ open, onClose, tenantId, repPhone, appointment, onSaved });
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
  const { f, set, busy, save } = useAppointmentForm({ open, onClose, tenantId, repPhone, onSaved });
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

function VulaRepTodayV1({ tenantId, repPhone }) {
  const { data, failed, load, aps, notes, now, minutesFor, setMinutesFor, adding, setAdding } = useRepToday(tenantId, repPhone);

  if (failed) return <ErrorCard what="today's appointments" onRetry={load} />;
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

export default function VulaRepToday({ v2 = false, ...props }) {
  return v2 ? <RepTodayV2 {...props} /> : <VulaRepTodayV1 {...props} />;
}
