/**
 * RepRemindersV2.jsx — the redesigned My Work › Reminders (2026-10-20, UX redesign Phase F).
 * Same calls as VulaRepReminders (rep/useRepReminders.js): reminders the rep set on WhatsApp, plus
 * one per action item from a logged meeting. Open / Done / All; "Done" ticks one off.
 */
import { Bell, Check } from "lucide-react";
import { SA_TZ } from "../../lib/dates";
import { Button, Card, EmptyState, PillTabs, StatusChip } from "../ui/index.jsx";
import { useRepReminders } from "./useRepReminders";
import "./rep.css";

const TABS = [{ id: "open", label: "Open" }, { id: "done", label: "Done" }, { id: "all", label: "All" }];

export default function RepRemindersV2({ tenantId, repPhone }) {
  const x = useRepReminders(tenantId, repPhone);
  return (
    <section className="vrp" aria-label="My reminders">
      <p className="vrp-sub">Set them on WhatsApp ("remind me to follow up with X on Friday"). A logged meeting adds one for each action item.</p>
      <PillTabs label="Show reminders" tabs={TABS} active={x.status} onChange={x.setStatus} />
      {x.reminders.length === 0
        ? <EmptyState Icon={Bell} title={x.status === "open" ? "Nothing due" : "Nothing here"}>{x.status === "open" ? "You're all caught up." : "No reminders to show."}</EmptyState>
        : <Card pad={4}>
            <ul className="vrp-list" aria-label="Reminders">
              {x.reminders.map((r) => {
                const late = x.isOverdue(r);
                return (
                  <li key={r.id} className="vrp-item" style={{ padding: "var(--space-3)" }}>
                    <span className="vrp-grow"><span className="vrp-body">{r.text}</span>
                      {r.due_at && <span className={`vrp-muted${late ? " vrp-overdue" : ""}`}>
                        Due {new Date(r.due_at).toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short", timeZone: SA_TZ })}</span>}</span>
                    {late && <StatusChip tone="danger">Overdue</StatusChip>}
                    {r.status === "open"
                      ? <Button size="sm" variant="secondary" Icon={Check} aria-label={`Done: ${r.text}`} onClick={() => x.complete(r.id)}>Done</Button>
                      : <StatusChip tone="ok">Done</StatusChip>}
                  </li>);
              })}
            </ul>
          </Card>}
    </section>
  );
}
