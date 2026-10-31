/**
 * EmployeesV2.jsx — Team › AI employees (2026-10-31, Ian: "a ready-made team every business gets").
 *
 * The business's AI employees (bookkeeper, sales and customer service, office administrator,
 * marketing, operations — core/skills/employees.py) behind its one WhatsApp number. The owner
 * switches one off, or gives it instructions in plain words. Every message still goes to the same
 * WhatsApp number; Vula hands it to the right employee. Backend: vula/api/commerce.py
 * (/admin/employees).
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { Button, Card, EmptyState, Field, StatusChip, Textarea, toast } from "../ui/index.jsx";

async function call(path, opts = {}) {
  const r = await fetch(`${VULA_API}${path}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || d.error || `Request failed (${r.status})`);
  return d;
}

function Employee({ tenantId, e, onSaved }) {
  const [text, setText] = useState(e.instructions || "");
  const [busy, setBusy] = useState(false);
  useEffect(() => setText(e.instructions || ""), [e.instructions]);
  const save = async (patch) => {
    setBusy(true);
    try {
      await call(`/v1/commerce/${tenantId}/admin/employees/${e.id}`, { method: "PUT", body: JSON.stringify(patch) });
      onSaved();
      toast("Saved");
    } catch (err) {
      toast(err.message || "That couldn't be saved.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className={`vtm-member${e.enabled ? "" : " vtm-member--off"}`}>
      <h3 className="vtm-h"><span aria-hidden="true">{e.emoji}</span> {e.title}
        {!e.enabled && <StatusChip>Off</StatusChip>}</h3>
      <p className="vtm-sub">{e.summary}</p>
      <Field label="Instructions for this employee" hint="In your own words, e.g. “Always quote on 30-day terms.” Only this employee follows them.">
        <Textarea rows={2} maxLength={600} value={text} onChange={(ev) => setText(ev.target.value)} />
      </Field>
      <div className="vtm-go">
        <Button size="sm" disabled={busy || text === (e.instructions || "")} onClick={() => save({ instructions: text })}>Save instructions</Button>{" "}
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => save({ enabled: !e.enabled })}>
          {e.enabled ? "Switch off" : "Switch on"}
        </Button>
      </div>
    </Card>
  );
}

export default function EmployeesV2({ tenantId }) {
  const [team, setTeam] = useState(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    call(`/v1/commerce/${tenantId}/admin/employees`).then((d) => { setTeam(d.employees || []); setErr(""); })
      .catch((e) => setErr(e.message || "Couldn't load your team."));
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  return (
    <div>
      <p className="vtm-sub">Your AI employees work behind your one WhatsApp number. Message it as usual — Vula hands each message to the right one, and anything unclear goes to the whole team. Switch one off and its work stops; give one instructions and only it follows them.</p>
      {err && <p className="vtm-msg" role="alert">{err}</p>}
      {team && team.length === 0 && <EmptyState title="No AI employees" />}
      {(team || []).map((e) => <Employee key={e.id} tenantId={tenantId} e={e} onSaved={load} />)}
    </div>
  );
}
