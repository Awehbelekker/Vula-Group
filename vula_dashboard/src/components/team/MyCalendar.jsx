/**
 * MyCalendar.jsx — "My Outlook calendar" (13 Nov 2026): the signed-in person connects their OWN
 * calendar, so their meetings come into their Vula diary (reminders only if they ask on WhatsApp).
 * Calendar only — Vula never asks for their email or files. Nobody can connect someone else's.
 * The button opens a 15-minute link (the same one "connect my calendar" sends on WhatsApp).
 * Backend: vula/api/microsoft.py (my-calendar), vula/microsoft/member_credentials.py.
 */
import { useCallback, useEffect, useState } from "react";
import { CalendarDays, Link2, Unlink } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { Button, Card, toast } from "../ui/index.jsx";

export default function MyCalendar({ tenantId }) {
  const [s, setS] = useState(null);
  const base = `${VULA_API}/v1/microsoft/my-calendar`;
  const q = `?tenant_id=${encodeURIComponent(tenantId || "")}`;

  const load = useCallback(async () => {
    if (!tenantId) return;
    try {
      const r = await fetch(base + q);
      setS(r.ok ? await r.json() : null);
    } catch { setS(null); }
  }, [base, q, tenantId]);
  useEffect(() => { load(); }, [load]);

  const connect = async () => {
    try {
      const r = await fetch(`${base}/link${q}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok || !d.url) throw new Error(d.detail || "Couldn't start — try again.");
      const w = window.open(d.url, "my-calendar", "width=520,height=700");
      const t = setInterval(() => { if (!w || w.closed) { clearInterval(t); load(); } }, 1500);
    } catch (e) { toast(e.message, "danger"); }
  };
  const disconnect = async () => {
    if (!window.confirm("Disconnect your calendar? Its upcoming meetings leave your Vula diary.")) return;
    try { await fetch(base + q, { method: "DELETE" }); load(); } catch (e) { toast(e.message, "danger"); }
  };

  if (!s) return null;                 // not on the team, or not signed in
  return (
    <Card className="vtm-block">
      <h2 className="vtm-h"><CalendarDays size={18} aria-hidden="true" />My Outlook calendar</h2>
      {s.connected ? (
        <>
          <p className="vtm-sub">Connected: {s.email || "Outlook"}. Your meetings come into your Vula diary.
            {s.last_sync_error ? ` Last check failed: ${s.last_sync_error}` : ""} For reminders, tell Vula on WhatsApp, e.g. "remind me 30 minutes before my Outlook meetings".</p>
          <Button size="sm" variant="secondary" Icon={Unlink} onClick={disconnect}>Disconnect</Button>
        </>
      ) : (
        <>
          <p className="vtm-sub">Let Vula see your own meetings — calendar only, never your email. You can also say "connect my calendar" to Vula on WhatsApp.</p>
          <Button size="sm" Icon={Link2} onClick={connect}>Connect my calendar</Button>
        </>
      )}
    </Card>
  );
}
