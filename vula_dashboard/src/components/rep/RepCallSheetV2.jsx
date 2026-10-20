/**
 * RepCallSheetV2.jsx — the redesigned My Work › Call sheet (2026-10-20, UX redesign Phase F).
 * Same calls as VulaRepCallSheet (rep/useRepCallSheet.js): when it's emailed and to whom, this
 * week's entries, and a plain-language fix that Vula previews first and applies only on Confirm.
 * The schedule fields are filled from the saved sheet and save when they change.
 */
import { useEffect, useState } from "react";
import { Check, ClipboardList, Eye, X } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { Button, Card, EmptyState, Field, Input, Select, Skeleton, Textarea } from "../ui/index.jsx";
import { DAYS, useRepCallSheet } from "./useRepCallSheet";
import "./rep.css";

const hhmm = (c) => `${String(c.call_sheet_hour ?? 17).padStart(2, "0")}:${String(c.call_sheet_minute ?? 0).padStart(2, "0")}`;
const when = (ts, opts) => new Date(ts).toLocaleString("en-ZA", { timeZone: SA_TZ, ...opts });

export default function RepCallSheetV2({ tenantId, repPhone }) {
  const x = useRepCallSheet(tenantId, repPhone);
  const c = x.config;
  const [email, setEmail] = useState("");
  const [time, setTime] = useState("17:00");
  useEffect(() => { setEmail(c.call_sheet_recipient_email || ""); setTime(hhmm(c)); }, [c]);

  if (!repPhone) return <EmptyState Icon={ClipboardList} title="No rep phone number">Couldn't identify your rep phone number. Ask the owner to check your team login.</EmptyState>;
  if (!x.loaded) return <Card><Skeleton lines={4} /></Card>;
  return (
    <section className="vrp" aria-label="My call sheet">
      <p className="vrp-sub">Every meeting you log on WhatsApp lands here by itself. Correct or add to it below; it's emailed on your schedule.</p>

      <Card className="vrp-card" role="group" aria-labelledby="vrp-cs-schedule">
        <h2 className="vrp-h" id="vrp-cs-schedule">Schedule</h2>
        <div className="vrp-grid">
          <Field label="Email it to">
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              onBlur={() => email !== (c.call_sheet_recipient_email || "") && x.saveConfig({ recipient_email: email })} />
          </Field>
          <Field label="Every">
            <Select value={c.call_sheet_day_of_week ?? 4} onChange={(e) => x.saveConfig({ day_of_week: Number(e.target.value) })}>
              {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </Select>
          </Field>
          <Field label="At">
            <Input type="time" value={time} onChange={(e) => setTime(e.target.value)}
              onBlur={() => { if (time && time !== hhmm(c)) { const [h, m] = time.split(":"); x.saveConfig({ hour: Number(h), minute: Number(m) }); } }} />
          </Field>
        </div>
        {c.call_sheet_last_sent_at && <p className="vrp-muted">Last sent {when(c.call_sheet_last_sent_at, { dateStyle: "medium", timeStyle: "short" })}</p>}
      </Card>

      <Card className="vrp-card" role="group" aria-labelledby="vrp-cs-fix">
        <h2 className="vrp-h" id="vrp-cs-fix">Fix or add something</h2>
        <Field label="What should change?" hint={'e.g. "the Dick meeting was about self-levelling, not HBC"'}>
          <Textarea rows={2} value={x.instruction} onChange={(e) => { x.setInstruction(e.target.value); x.setPreview(null); }} />
        </Field>
        {x.preview
          ? <div className="vrp-preview" role="status">
              <span className="vrp-muted">Vula will make this change:</span>
              <p className="vrp-body">{x.preview.change}</p>
              <div className="vrp-row">
                <Button size="sm" Icon={Check} onClick={() => x.submitInstruction(true)}>Confirm</Button>
                <Button size="sm" variant="secondary" Icon={X} onClick={() => x.setPreview(null)}>Not that</Button>
              </div>
            </div>
          : <div><Button variant="secondary" Icon={Eye} disabled={!x.instruction.trim()} onClick={() => x.submitInstruction(false)}>Preview change</Button></div>}
      </Card>
      {x.msg && <p className="vrp-error" role="alert">{x.msg}</p>}

      <h2 className="vrp-overline">This week so far ({x.entries.length})</h2>
      {x.entries.length === 0
        ? <EmptyState Icon={ClipboardList} title="Nothing logged yet">Log a meeting on WhatsApp and it appears here.</EmptyState>
        : <Card pad={4}>
            <ul className="vrp-list" aria-label="Call sheet entries">
              {x.entries.map((e) => (
                <li key={e.id} className="vrp-item" style={{ padding: "var(--space-3)" }}>
                  <span className="vrp-grow"><span className="vrp-body">{e.text}</span>
                    <span className="vrp-muted">{e.source === "manual" ? "Added by hand" : "From a logged meeting"} · {when(e.created_at, { day: "numeric", month: "short" })}</span></span>
                </li>))}
            </ul>
          </Card>}
    </section>
  );
}
