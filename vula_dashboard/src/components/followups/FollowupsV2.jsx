/**
 * FollowupsV2.jsx — the redesigned Follow-ups (2026-10-18, UX redesign Phase F): emails Vula
 * thinks are waiting on a reply. Same data and actions (followups/useFollowups.js): Done, Dismiss,
 * Not relevant (which also teaches Vula's spam filter).
 */
import { Ban, Check, MailCheck, X } from "lucide-react";
import { Button, Card, EmptyState, PageHeader, StatusChip } from "../ui/index.jsx";
import { daysWaiting, useFollowups } from "./useFollowups";
import "./followups.css";

const REASON = { schedule: { label: "Schedule", tone: "info" }, request: { label: "Request", tone: "highlight" }, question: { label: "Question", tone: "brand" } };

export default function FollowupsV2({ tenantId }) {
  const { rows, setStatus } = useFollowups(tenantId);
  return (
    <div>
      <PageHeader sub="Emails Vula thinks are waiting on you. You get a WhatsApp nudge each day until they're cleared." />
      {rows.length === 0 ? (
        <EmptyState Icon={MailCheck} title="All caught up">Nothing waiting on a reply. Checked at every mailbox sync.</EmptyState>
      ) : (
        <div className="vfu-list" role="list">
          {rows.map((r) => {
            const d = daysWaiting(r.received_at);
            const re = REASON[r.reason];
            return (
              <Card key={r.id} pad={0} role="listitem" className="vfu-item">
                <div className="vfu-main">
                  <div className="vfu-top">
                    <span className="vfu-who">{r.sender_name || r.sender}</span>
                    {re ? <StatusChip tone={re.tone}>{re.label}</StatusChip> : r.reason ? <StatusChip>{r.reason}</StatusChip> : null}
                    {r.urgency === "high" && <StatusChip tone="danger">Urgent</StatusChip>}
                    {d >= 2 && <StatusChip tone="warm">{d}d waiting</StatusChip>}
                    {r.tone && <span className="vfu-sub vfu-tone">{r.tone}</span>}
                  </div>
                  <div className="vfu-subject">{r.subject || "(no subject)"}</div>
                  <div className="vfu-sub">{r.summary || (r.preview || "").slice(0, 110)}</div>
                </div>
                <div className="vfu-actions">
                  <Button size="sm" Icon={Check} onClick={() => setStatus(r.id, "done")}>Done</Button>
                  <Button size="sm" variant="secondary" Icon={X} onClick={() => setStatus(r.id, "snoozed")}>Dismiss</Button>
                  <Button size="sm" variant="quiet" Icon={Ban} title="Not a real follow-up — helps Vula's spam filter improve"
                    onClick={() => setStatus(r.id, "spam")}>Not relevant</Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
