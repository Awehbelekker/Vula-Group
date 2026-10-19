/**
 * BookingsV2.jsx — the redesigned Bookings (2026-10-18, UX redesign Phase F). Same data and actions
 * as the current screen (bookings/useBookings.js): Day (book a free slot; mark done / no-show /
 * cancel), Week (seven days; a day opens its Day view), Services and Availability. The week is a
 * seven-column grid from 1024px and a list of days below it.
 */
import { useState } from "react";
import { CalendarDays, CalendarPlus, ChevronLeft, ChevronRight, Clock, Plus, Trash2 } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, IconButton, Input, PageHeader, PillTabs, Select, StatusChip } from "../ui/index.jsx";
import { DAYNAMES, bookSlot, deleteService, onDay, saveRules, saveService, shift, timeOf, today, useBookings } from "./useBookings";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import "./bookings.css";

const STATUS = {
  pending: { label: "Pending", tone: "highlight" }, confirmed: { label: "Confirmed", tone: "info" },
  completed: { label: "Completed", tone: "ok" }, cancelled: { label: "Cancelled", tone: "danger" }, no_show: { label: "No-show", tone: "muted" },
};
const statusOf = (b) => STATUS[b.status] || { label: b.status, tone: "muted" };
const longDate = (d) => new Date(d + "T12:00:00").toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" });

export default function BookingsV2({ tenantId }) {
  const x = useBookings(tenantId);
  return (
    <div>
      <PageHeader sub="Appointments, the services people can book, and when you're open." />
      <div className="vbk-bar">
        <PillTabs label="Bookings view" active={x.view} onChange={x.setView}
          tabs={[{ id: "day", label: "Day" }, { id: "week", label: "Week" }, { id: "services", label: "Services" }, { id: "settings", label: "Availability" }]} />
        {x.msg && <span className="vbk-msg" role="status">{x.msg}</span>}
      </div>
      {x.view === "day" && <DayView x={x} tenantId={tenantId} />}
      {x.view === "week" && <WeekView x={x} />}
      {x.view === "services" && <ServicesView x={x} tenantId={tenantId} />}
      {x.view === "settings" && x.settings && <RulesView x={x} tenantId={tenantId} />}
    </div>
  );
}

function DateNav({ label, onPrev, onNext, onToday, todayLabel, children }) {
  return (
    <div className="vbk-nav">
      <IconButton Icon={ChevronLeft} label={`Previous ${label}`} onClick={onPrev} />
      {children}
      <IconButton Icon={ChevronRight} label={`Next ${label}`} onClick={onNext} />
      <Button size="sm" variant="secondary" onClick={onToday}>{todayLabel}</Button>
    </div>
  );
}

function DayView({ x, tenantId }) {
  return (
    <>
      <DateNav label="day" todayLabel="Today" onPrev={() => x.setDate(shift(x.date, -1))} onNext={() => x.setDate(shift(x.date, 1))} onToday={() => x.setDate(today())}>
        <Input type="date" value={x.date} onChange={(e) => x.setDate(e.target.value)} aria-label="Day" className="vbk-date" />
      </DateNav>
      <QuickBookV2 x={x} tenantId={tenantId} />
      <h2 className="vbk-h">{longDate(x.date)} <span className="vbk-sub">{x.bookings.length} appointment{x.bookings.length === 1 ? "" : "s"}</span></h2>
      {x.bookings.length === 0 ? (
        <EmptyState Icon={CalendarDays} title="No appointments this day">Book one above, or customers book themselves on WhatsApp.</EmptyState>
      ) : (
        <div className="vbk-list" role="list">
          {x.bookings.map((b) => {
            const st = statusOf(b);
            const open = b.status !== "cancelled" && b.status !== "completed";
            return (
              <Card key={b.id} pad={0} role="listitem" className="vbk-appt">
                <span className="vbk-time">{timeOf(b.start_at)}</span>
                <span className="vbk-appt-main">
                  <span className="vbk-strong">{b.service_name || "Appointment"}</span>
                  <span className="vbk-sub">{[b.customer_name || "—", b.customer_phone, `via ${b.channel}`].filter(Boolean).join(" · ")}</span>
                  {b.notes && <span className="vbk-sub">Note: {b.notes}</span>}
                </span>
                <StatusChip tone={st.tone}>{st.label}</StatusChip>
                {open && (
                  <span className="vbk-actions">
                    <Button size="sm" variant="secondary" onClick={() => x.setStatus(b.id, "completed")}>Done</Button>
                    <Button size="sm" variant="secondary" onClick={() => x.setStatus(b.id, "no_show")}>No-show</Button>
                    <Button size="sm" variant="danger" onClick={() => x.setStatus(b.id, "cancelled")}>Cancel</Button>
                  </span>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}

function QuickBookV2({ x, tenantId }) {
  const empty = { start: "", service_id: "", customer_name: "", customer_phone: "", notes: "" };
  const [f, setF] = useState(empty);
  const slots = x.avail?.slots || [];
  const book = async () => {
    if (!f.start) return x.flash("Pick a time");
    x.setBusy(true);
    const r = await bookSlot(tenantId, x.date, f, x.services);
    x.setBusy(false);
    if (r.error) return x.flash(r.error);
    setF(empty);
    x.loadDay(); x.flash("Booked ✓");
  };
  // on a phone the form folds away (open it with "New booking") so the day's list comes first
  const phone = useBreakpoint() === "mobile";
  return (
    <Card className="vbk-block">
      <details open={!phone || undefined} className="vbk-qb">
      <summary className="vbk-h vbk-h--card"><CalendarPlus size={18} aria-hidden="true" />New booking</summary>
      {x.avail?.closed ? <p className="vbk-sub">Closed on this day (see Availability).</p>
        : slots.length === 0 ? <p className="vbk-sub">No free slots this day.</p>
        : (
          <div className="vbk-form">
            <Field label="Time">
              <Select value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })}>
                <option value="">Pick a time…</option>
                {slots.map((s) => <option key={s.start_utc} value={s.label}>{s.label}</option>)}
              </Select>
            </Field>
            <Field label="Service">
              <Select value={f.service_id} onChange={(e) => setF({ ...f, service_id: e.target.value })}>
                <option value="">Any / not set</option>
                {x.services.filter((s) => s.active).map((s) => <option key={s.id} value={s.id}>{s.name} ({s.duration_min} min)</option>)}
              </Select>
            </Field>
            <Field label="Customer name"><Input value={f.customer_name} onChange={(e) => setF({ ...f, customer_name: e.target.value })} /></Field>
            <Field label="Phone"><Input inputMode="tel" value={f.customer_phone} onChange={(e) => setF({ ...f, customer_phone: e.target.value })} /></Field>
            <Field label="Notes" style={{ gridColumn: "1 / -1" }}><Input value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
            <div className="vbk-go"><Button Icon={CalendarPlus} disabled={x.busy} onClick={book}>{x.busy ? "Booking…" : "Book"}</Button></div>
          </div>
        )}
      </details>
    </Card>
  );
}

function WeekView({ x }) {
  return (
    <>
      <DateNav label="week" todayLabel="This week" onPrev={() => x.setDate(shift(x.weekStart, -7))} onNext={() => x.setDate(shift(x.weekStart, 7))} onToday={() => x.setDate(today())}>
        <span className="vbk-strong vbk-range">{x.weekStart} – {shift(x.weekStart, 6)}</span>
      </DateNav>
      <div className="vbk-week">
        {DAYNAMES.map((label, i) => {
          const d = shift(x.weekStart, i);
          const items = x.weekBookings.filter((b) => onDay(b, d));
          return (
            <button key={d} type="button" className={`vbk-day${d === today() ? " vbk-day--today" : ""}`}
              onClick={() => { x.setDate(d); x.setView("day"); }} aria-label={`${label} ${d}: ${items.length} appointment${items.length === 1 ? "" : "s"} — open the day`}>
              <span className="vbk-day-h"><span className="vbk-strong">{label}</span> <span className="vbk-sub">{d.slice(5)}</span>
                {items.length > 0 && <span className="vbk-count">{items.length}</span>}</span>
              {items.length === 0 ? <span className="vbk-sub">—</span> : items.slice(0, 5).map((b) => (
                <span key={b.id} className={`vbk-pill vbk-pill--${statusOf(b).tone}`}><b>{timeOf(b.start_at)}</b> {b.customer_name || b.service_name || "—"}</span>
              ))}
              {items.length > 5 && <span className="vbk-sub">+{items.length - 5} more</span>}
            </button>
          );
        })}
      </div>
    </>
  );
}

function ServicesView({ x, tenantId }) {
  const blank = { name: "", duration_min: 30, price_cents: 0, active: true };
  const [f, setF] = useState(blank);
  const save = async (body) => {
    const r = await saveService(tenantId, body);
    if (r.error) return x.flash(r.error);
    x.loadServices();
  };
  return (
    <>
      <div className="vbk-list" role="list">
        {x.services.map((s) => (
          <Card key={s.id} pad={0} role="listitem" className="vbk-appt">
            <span className="vbk-time"><Clock size={16} aria-hidden="true" />{s.duration_min}m</span>
            <span className="vbk-appt-main">
              <span className="vbk-strong">{s.name}</span>
              <span className="vbk-sub">{s.price_cents ? formatMoney(s.price_cents, { cents: "always" }) : "Price on the day"}</span>
            </span>
            {!s.active && <StatusChip>Inactive</StatusChip>}
            <span className="vbk-actions">
              <Button size="sm" variant="secondary" onClick={() => save({ ...s, active: !s.active })}>{s.active ? "Disable" : "Enable"}</Button>
              <Button size="sm" variant="danger" Icon={Trash2} onClick={async () => { await deleteService(tenantId, s.id); x.loadServices(); }}>Delete</Button>
            </span>
          </Card>
        ))}
      </div>
      <Card className="vbk-block">
        <h2 className="vbk-h vbk-h--card"><Plus size={18} aria-hidden="true" />Add a service</h2>
        <div className="vbk-form">
          <Field label="Service name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Minutes"><Input type="number" inputMode="numeric" value={f.duration_min} onChange={(e) => setF({ ...f, duration_min: +e.target.value })} /></Field>
          <Field label="Price (R)"><Input type="number" inputMode="decimal" value={f.price_cents / 100 || ""} onChange={(e) => setF({ ...f, price_cents: Math.round(+e.target.value * 100) })} /></Field>
          <div className="vbk-go"><Button Icon={Plus} disabled={!f.name} onClick={() => { if (f.name) { save(f); setF(blank); } }}>Add</Button></div>
        </div>
      </Card>
    </>
  );
}

function RulesView({ x, tenantId }) {
  const [s, setS] = useState(x.settings);
  const toggleDay = (d) => {
    const days = new Set(s.workdays || []);
    days.has(d) ? days.delete(d) : days.add(d);
    setS({ ...s, workdays: [...days].sort() });
  };
  const save = async () => {
    const r = await saveRules(tenantId, s);
    if (r.settings) { x.setSettings(r.settings); x.flash("Saved ✓"); }
  };
  const num = (k, label) => (
    <Field label={label}><Input type="number" inputMode="numeric" value={s[k]} onChange={(e) => setS({ ...s, [k]: e.target.value })} /></Field>
  );
  return (
    <Card className="vbk-block">
      <h2 className="vbk-h vbk-h--card">Working days</h2>
      <div className="vbk-days" role="group" aria-label="Working days">
        {DAYNAMES.map((d, i) => {
          const iso = i + 1;
          return <button key={d} type="button" className="vui-chip" aria-pressed={(s.workdays || []).includes(iso)} onClick={() => toggleDay(iso)}>{d}</button>;
        })}
      </div>
      <div className="vbk-form">
        <Field label="Open"><Input type="time" value={s.open_time} onChange={(e) => setS({ ...s, open_time: e.target.value })} /></Field>
        <Field label="Close"><Input type="time" value={s.close_time} onChange={(e) => setS({ ...s, close_time: e.target.value })} /></Field>
        {num("slot_minutes", "Slot (min)")}
        {num("buffer_min", "Buffer between (min)")}
        {num("lead_hours", "Minimum notice (hours)")}
        {num("horizon_days", "Book ahead (days)")}
        <div className="vbk-go"><Button onClick={save}>Save rules</Button></div>
      </div>
    </Card>
  );
}
