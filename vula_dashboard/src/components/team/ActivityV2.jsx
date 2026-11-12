/**
 * ActivityV2.jsx — Team › Activity (12 Nov 2026): what each person has on and has done.
 *
 * One row per person for the chosen period — meetings held and coming up, bookings, meeting
 * notes, call-sheet entries, follow-ups done and overdue, new contacts — listed by name, never
 * ranked. Tap a person for their week: the diary with addresses, and what they logged. CSV export.
 * Owners and managers only; the tab only shows where it fits (TeamV2). On WhatsApp the same is
 * "how does Thabo's week look?" / "what did the team do this week?".
 * Backend: vula/api/team_activity.py, vula/team_activity.py.
 */
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Download } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { downloadCsv } from "../../lib/csv";
import { Button, Card, EmptyState, Field, Select, Table, toast } from "../ui/index.jsx";

const PERIODS = [["this_week", "This week"], ["last_week", "Last week"], ["next_week", "Next week"],
  ["this_month", "This month"], ["last_month", "Last month"]];
const COLS = [
  { key: "name", label: "Person" },
  { key: "meetings_held", label: "Meetings held", align: "right" },
  { key: "meetings_coming", label: "Coming up", align: "right" },
  { key: "bookings", label: "Bookings", align: "right" },
  { key: "notes", label: "Notes", align: "right" },
  { key: "call_sheet_entries", label: "Call sheet", align: "right" },
  { key: "follow_ups_done", label: "Follow-ups done", align: "right" },
  { key: "follow_ups_overdue", label: "Overdue", align: "right" },
  { key: "contacts_added", label: "New contacts", align: "right" },
];
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-ZA", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
const mapsLink = (a) => `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(a)}`;

async function call(path) {
  const r = await fetch(`${VULA_API}${path}`);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `Request failed (${r.status})`);
  return d;
}

export default function ActivityV2({ tenantId }) {
  const [period, setPeriod] = useState("this_week");
  const [data, setData] = useState(null);
  const [person, setPerson] = useState(null);
  const base = `/v1/team/${tenantId}/activity`;

  const load = useCallback(async () => {
    setData(null);
    try { setData(await call(`${base}?period=${period}`)); } catch (e) { toast(e.message, "danger"); setData({ people: [] }); }
  }, [base, period]);
  useEffect(() => { load(); }, [load]);

  const open = async (row) => {
    try { setPerson(await call(`${base}?period=${period}&member_id=${encodeURIComponent(row.member_id)}`)); }
    catch (e) { toast(e.message, "danger"); }
  };

  if (person) return <PersonWeek p={person} onBack={() => setPerson(null)} />;
  const rows = data?.people || [];
  return (
    <Card className="vtm-block">
      <p className="vtm-sub">What each person has on and has done, from what they booked and logged with Vula. Quotes, invoices and orders aren't counted per person yet. Tap a person for their week.</p>
      <div className="vtm-form">
        <Field label="Period">
          <Select value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </Select>
        </Field>
        <Button size="sm" variant="secondary" Icon={Download} disabled={!rows.length}
          onClick={() => downloadCsv(`team-activity-${data.since}`, rows, COLS)}>CSV</Button>
      </div>
      <Table columns={COLS} rows={rows} loading={!data} rowKey="member_id" onRowClick={open}
        empty="Nobody on the team has a WhatsApp number yet." />
    </Card>
  );
}

function PersonWeek({ p, onBack }) {
  const done = [
    ...p.notes.map((n) => ({ at: n.at, text: `Meeting note: ${n.summary || ""}` })),
    ...p.call_sheet.map((c) => ({ at: c.at, text: `Call sheet: ${c.text || ""}` })),
    ...p.follow_ups_done.map((f) => ({ at: f.at, text: `Follow-up done: ${f.text || ""}` })),
    ...p.contacts_added.map((c) => ({ at: c.at, text: `New contact: ${c.name || ""}` })),
  ].sort((a, b) => String(a.at).localeCompare(String(b.at)));
  return (
    <Card className="vtm-block">
      <Button size="sm" variant="secondary" Icon={ArrowLeft} onClick={onBack}>Team</Button>
      <h2 className="vtm-h">{p.name} — {p.label}</h2>
      <h3 className="vtm-sub">Diary</h3>
      {p.diary.length === 0 ? <EmptyState title="No meetings or bookings" /> : (
        <ul className="vtm-activity-list">
          {p.diary.map((d, i) => (
            <li key={i}>{when(d.at)} — {d.title}
              {d.address && <> · <a href={mapsLink(d.address)} target="_blank" rel="noreferrer">{d.address}</a></>}</li>))}
        </ul>)}
      <h3 className="vtm-sub">Logged</h3>
      {done.length === 0 ? <p className="vtm-sub">Nothing logged in this period.</p> : (
        <ul className="vtm-activity-list">{done.map((d, i) => <li key={i}>{when(d.at)} — {d.text}</li>)}</ul>)}
      {p.overdue.length > 0 && (<>
        <h3 className="vtm-sub">Overdue follow-ups</h3>
        <ul className="vtm-activity-list">{p.overdue.map((o, i) => <li key={i}>{when(o.due)} — {o.text}</li>)}</ul>
      </>)}
    </Card>
  );
}
