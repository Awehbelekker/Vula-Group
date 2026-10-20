/**
 * RepTodayV2.jsx — the redesigned "Today" card on a sales rep's Home (2026-10-20, UX redesign
 * Phase F). Same calls as VulaRepToday (rep/useRepToday.js): today's appointments from the rep's
 * own diary, minutes on a meeting (typed or dictated; Vula summarises, turns action items into
 * reminders and sends the rep the minutes PDF), and adding an appointment. Forms open in a Sheet.
 */
import { CalendarPlus, Check, Mic, NotebookPen, Square } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { Button, Card, ErrorCard, Field, Input, Sheet, Skeleton, StatusChip, Textarea } from "../ui/index.jsx";
import { useAppointmentForm, useMinutesForm, useRepToday } from "./useRepToday";
import "./rep.css";

export default function RepTodayV2({ tenantId, repPhone }) {
  const x = useRepToday(tenantId, repPhone);
  if (x.failed) return <ErrorCard what="today's appointments" onRetry={x.load} />;
  const dayLabel = new Date().toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long", timeZone: SA_TZ });
  const loose = x.notes.filter((n) => !n.booking_id);
  return (
    <Card className="vrp-card" role="group" aria-labelledby="vrp-today">
      <div className="vrp-row">
        <span className="vrp-grow"><h2 className="vrp-h" id="vrp-today">Appointments</h2><span className="vrp-muted">{dayLabel}</span></span>
        <Button size="sm" variant="secondary" Icon={NotebookPen} onClick={() => x.setMinutesFor(null)}>Meeting notes</Button>
        <Button size="sm" Icon={CalendarPlus} onClick={() => x.setAdding(true)}>Appointment</Button>
      </div>
      {!x.data && <Skeleton lines={2} />}
      {x.data && x.aps.length === 0 && <p className="vrp-muted">No appointments today. Add one, or log notes for a meeting you had.</p>}
      {x.aps.length > 0 && (
        <ul className="vrp-list" aria-label="Today's appointments">
          {x.aps.map((a) => {
            const past = (a.end_local || a.start_local || "") < x.now;
            const name = a.customer_name || a.service_name || "Meeting";
            const detail = [a.location, a.service_name && a.service_name !== "Meeting" ? a.service_name : null, a.notes].filter(Boolean).join(" · ");
            return (
              <li key={a.id} className="vrp-item">
                <span className={`vrp-time${past ? " vrp-time--past" : ""}`}>{a.start_local}{a.end_local ? `–${a.end_local}` : ""}</span>
                <span className="vrp-grow"><b>{name}</b>{detail && <span className="vrp-muted">{detail}</span>}</span>
                {a.has_minutes
                  ? <StatusChip tone="ok"><Check size={14} aria-hidden="true" /> Minutes saved</StatusChip>
                  : <Button size="sm" variant={past ? "primary" : "secondary"} Icon={NotebookPen} aria-label={`Minutes for ${name}`}
                      onClick={() => x.setMinutesFor(a)}>Minutes</Button>}
              </li>);
          })}
        </ul>)}
      {loose.length > 0 && (
        <>
          <h3 className="vrp-overline">Other notes today</h3>
          <ul className="vrp-list">{loose.map((n) => <li key={n.id} className="vrp-item"><span className="vrp-body">{n.summary}</span></li>)}</ul>
        </>)}
      <MinutesSheet open={x.minutesFor !== undefined} onClose={() => x.setMinutesFor(undefined)} tenantId={tenantId}
        repPhone={repPhone} appointment={x.minutesFor || null} onSaved={x.load} />
      <AppointmentSheet open={x.adding} onClose={() => x.setAdding(false)} tenantId={tenantId} repPhone={repPhone} onSaved={x.load} />
    </Card>
  );
}

function MinutesSheet({ open, onClose, tenantId, repPhone, appointment, onSaved }) {
  const m = useMinutesForm({ open, onClose, tenantId, repPhone, appointment, onSaved });
  const who = appointment ? `${appointment.customer_name || "the client"} · ${appointment.start_local || ""}` : "";
  return (
    <Sheet open={open} onClose={onClose} title={appointment ? "Meeting minutes" : "Notes for a meeting"}
      footer={<>
        <Button variant="quiet" onClick={onClose}>Cancel</Button>
        <Button Icon={Check} onClick={m.save} disabled={m.busy || m.notes.trim().length < 3}>{m.busy ? "Saving…" : "Save minutes"}</Button>
      </>}>
      <div className="vrp-form">
        {who && <p className="vrp-muted">{who}</p>}
        {!appointment && <Field label="Who was it with?" hint="Name or phone"><Input value={m.contact} onChange={(e) => m.setContact(e.target.value)} /></Field>}
        <Field label="Minutes" hint="What was discussed, what was agreed, who does what by when.">
          <Textarea rows={7} value={m.notes} onChange={(e) => m.setNotes(e.target.value)} autoFocus />
        </Field>
        {m.dict.supported && (m.dict.on
          ? <div><Button variant="secondary" size="sm" Icon={Square} onClick={m.dict.stop}>Stop dictating</Button></div>
          : <div><Button variant="secondary" size="sm" Icon={Mic} onClick={m.dict.start}>Dictate</Button></div>)}
        <p className="vrp-muted">Vula writes the summary, turns action items into reminders and sends you the minutes PDF on WhatsApp.</p>
      </div>
    </Sheet>
  );
}

function AppointmentSheet({ open, onClose, tenantId, repPhone, onSaved }) {
  const a = useAppointmentForm({ open, onClose, tenantId, repPhone, onSaved });
  return (
    <Sheet open={open} onClose={onClose} title="New appointment"
      footer={<>
        <Button variant="quiet" onClick={onClose}>Cancel</Button>
        <Button Icon={Check} onClick={a.save} disabled={a.busy || !a.f.customer_name.trim()}>{a.busy ? "Saving…" : "Add appointment"}</Button>
      </>}>
      <div className="vrp-form">
        <Field label="Client or company"><Input value={a.f.customer_name} onChange={a.set("customer_name")} autoFocus /></Field>
        <div className="vrp-grid">
          <Field label="Date"><Input type="date" value={a.f.date} onChange={a.set("date")} /></Field>
          <Field label="Time"><Input type="time" value={a.f.time} onChange={a.set("time")} /></Field>
          <Field label="Minutes long"><Input type="number" min="15" step="15" value={a.f.duration_min} onChange={a.set("duration_min")} /></Field>
        </div>
        <Field label="Where" hint="Site, office or address"><Input value={a.f.location} onChange={a.set("location")} /></Field>
        <Field label="Client phone" hint="Optional"><Input type="tel" value={a.f.customer_phone} onChange={a.set("customer_phone")} /></Field>
        <Field label="What it's about" hint="Optional"><Textarea rows={2} value={a.f.notes} onChange={a.set("notes")} /></Field>
      </div>
    </Sheet>
  );
}
