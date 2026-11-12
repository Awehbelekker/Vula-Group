/**
 * OwnerBriefs.jsx — Team › Notices: the owner's own WhatsApp briefs (11 Nov 2026).
 *
 * Morning brief (weekdays, 07:00–09:00): what needs a person today — customers waiting, follow-ups,
 * emails, quotes Vula drafted, documents to check, today's bookings — plus money paid, owed and due.
 * Weekly advisor (Mondays): money owed, unusual spending, low stock, unanswered questions.
 * Each can be switched on or off for this business; "See today's" shows exactly what would be sent.
 * Owners and managers only. Backend: vula/api/owner_briefs.py.
 */
import { useCallback, useEffect, useState } from "react";
import { Eye, Sunrise } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { Button, Card, toast } from "../ui/index.jsx";

async function call(path, opts = {}) {
  const r = await fetch(`${VULA_API}${path}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || d.error || `Request failed (${r.status})`);
  return d;
}

const BRIEFS = [
  { id: "morning", label: "Morning brief — weekdays around 07:00",
    help: "What needs you today (waiting customers, follow-ups, emails, quotes to approve, documents, bookings) and your money: paid yesterday, overdue, due this week." },
  { id: "weekly", label: "Weekly advisor — Monday mornings",
    help: "Money owed, spending that's higher than usual, low stock and questions Vula couldn't answer." },
];

export default function OwnerBriefs({ tenantId }) {
  const [settings, setSettings] = useState(null);
  const [preview, setPreview] = useState({});
  const base = `/v1/team/${tenantId}/briefs`;

  const load = useCallback(async () => {
    if (!tenantId) return;
    try { setSettings((await call(base)).settings || {}); }
    catch { setSettings(null); }        // migration not applied yet → the card stays hidden
  }, [tenantId, base]);
  useEffect(() => { load(); }, [load]);

  const save = async (patch) => {
    try { setSettings((await call(base, { method: "PUT", body: JSON.stringify(patch) })).settings); }
    catch (e) { toast(e.message, "danger"); }
  };
  const show = async (kind) => {
    try {
      const d = await call(`${base}/preview?kind=${kind}`);
      setPreview({ ...preview, [kind]: d.empty ? "Nothing to send today, so nothing would go out." : d.text });
    } catch (e) { toast(e.message, "danger"); }
  };

  if (!settings) return null;
  return (
    <Card className="vtm-block">
      <h2 className="vtm-h"><Sunrise size={18} aria-hidden="true" />Your briefs on WhatsApp</h2>
      <p className="vtm-sub">Sent to the business owner's WhatsApp. Built only from your own records — nothing is sent on a day with nothing to say.</p>
      {BRIEFS.map((b) => (
        <div key={b.id}>
          <label className="vtm-check">
            <input type="checkbox" checked={!!settings[b.id]} onChange={(e) => save({ [b.id]: e.target.checked })} />
            <span>{b.label}<br /><span className="vtm-sub">{b.help}</span></span>
          </label>
          <Button size="sm" variant="secondary" Icon={Eye} onClick={() => show(b.id)}>See today's</Button>
          {preview[b.id] && <pre className="vtm-brief-preview">{preview[b.id]}</pre>}
        </div>
      ))}
    </Card>
  );
}
