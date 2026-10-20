/**
 * LabourV2.jsx — the redesigned Operate › Labour (2026-10-20, UX redesign Phase F). Same calls as
 * VulaLabour (construction/useLabour.js): the casual-labour register (Vula recognises each
 * worker's payments on the bank statement and costs them to the project) and what labour was paid,
 * by worker and by project. Add / edit opens a Sheet; removing asks first.
 */
import { useState } from "react";
import { Check, HardHat, Pencil, Plus, Trash2 } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, IconButton, Input, Select, Sheet, StatusChip } from "../ui/index.jsx";
import { blank, useLabour } from "./useLabour";
import "./construction.css";

const plain = (t) => String(t || "").replace(/\s*✓$/, "");

export default function LabourV2({ tenantId }) {
  const x = useLabour({ tenantId });
  const [open, setOpen] = useState(false);
  const set = (k) => (e) => x.setF((f) => ({ ...f, [k]: e.target.value }));
  const close = () => { setOpen(false); x.setF(blank); x.setEditing(false); };
  const save = async () => { if (await x.save()) setOpen(false); };
  const r = x.report;
  return (
    <section className="vco" aria-label="Casual labour">
      <p className="vcx-sub">Load a worker once. Vula recognises their payments on the bank statement, files them as casual labour and costs them to the project. Your accountant gets the record for PAYE and UIF; Vula doesn't file returns.</p>
      <div className="vcx-row">
        <h2 className="vcx-h" style={{ flex: 1 }}>Register ({x.workers.length})</h2>
        <Button Icon={Plus} onClick={() => { x.setF(blank); x.setEditing(false); setOpen(true); }}>Add a worker</Button>
      </div>
      {x.msg && !open && <p className="vcx-ok" role="status">{plain(x.msg)}</p>}
      {x.workers.length === 0
        ? <EmptyState Icon={HardHat} title="No workers yet">Add one, and Vula starts recognising their pay on the bank statement.</EmptyState>
        : <Card pad={4}>
            <ul className="vcx-list" aria-label="Workers">
              {x.workers.map((w) => (
                <li key={w.id} className="vcx-item">
                  <span className="vcx-grow"><b>{w.name}</b>
                    <span className="vcx-muted">{[w.bank_account ? `Account ${w.bank_account}` : "No bank account", w.id_number ? `ID ${w.id_number}` : null, w.default_project].filter(Boolean).join(" · ")}</span></span>
                  <StatusChip outline>{w.type === "subcontractor" ? "Subcontractor" : "Casual"}</StatusChip>
                  {w.rate_cents ? <span className="vcx-money">{formatMoney(w.rate_cents)}<span className="vcx-muted"> / {w.rate_period === "weekly" ? "week" : "day"}</span></span> : null}
                  <IconButton quiet Icon={Pencil} label={`Edit ${w.name}`} onClick={() => { x.edit(w); setOpen(true); }} />
                  <IconButton quiet Icon={Trash2} className="vcx-danger" label={`Remove ${w.name}`} onClick={() => x.remove(w.id)} />
                </li>))}
            </ul>
          </Card>}

      {r && r.payment_count > 0 && (
        <Card className="vcx-card" role="group" aria-labelledby="vcx-paid">
          <h2 className="vcx-h" id="vcx-paid">Labour paid</h2>
          <p className="vcx-muted">{formatMoney(r.total_cents)} across {r.payment_count} {r.payment_count === 1 ? "payment" : "payments"}</p>
          <div className="vcx-split">
            <div>
              <h3 className="vcx-overline">By worker</h3>
              {r.by_worker.map((w, i) => <div key={i} className="vcx-kv"><span>{w.worker}{w.id_number ? ` (ID ${w.id_number})` : ""}</span><span className="vcx-money">{formatMoney(w.total_cents)} · {w.payments}×</span></div>)}
            </div>
            {r.by_project.length > 0 && (
              <div>
                <h3 className="vcx-overline">By project</h3>
                {r.by_project.map((p, i) => <div key={i} className="vcx-kv"><span>{p.project}</span><span className="vcx-money">{formatMoney(p.total_cents)}</span></div>)}
              </div>)}
          </div>
        </Card>)}

      <Sheet open={open} onClose={close} title={x.editing ? "Edit worker" : "Add a worker"}
        footer={<><Button variant="quiet" onClick={close}>Cancel</Button><Button Icon={Check} onClick={save}>{x.editing ? "Save changes" : "Add worker"}</Button></>}>
        <form className="vcx-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <Field label="Full name"><Input value={x.f.name} onChange={set("name")} autoFocus /></Field>
          <div className="vcx-grid">
            <Field label="ID number"><Input value={x.f.id_number} onChange={set("id_number")} /></Field>
            <Field label="Bank account number" hint="How Vula spots their pay"><Input value={x.f.bank_account} onChange={set("bank_account")} inputMode="numeric" /></Field>
            <Field label="Phone"><Input type="tel" value={x.f.phone} onChange={set("phone")} /></Field>
            <Field label="Rate (R)"><Input type="number" min="0" inputMode="decimal" value={x.f.rate_rands} onChange={set("rate_rands")} /></Field>
            <Field label="Paid"><Select value={x.f.rate_period} onChange={set("rate_period")}><option value="daily">per day</option><option value="weekly">per week</option></Select></Field>
            <Field label="Type"><Select value={x.f.type} onChange={set("type")}><option value="casual">Casual</option><option value="subcontractor">Subcontractor</option></Select></Field>
          </div>
          <Field label="Default project">
            <Input list="vcx-proj-list" value={x.f.default_project} onChange={set("default_project")} />
            <datalist id="vcx-proj-list">{x.projects.map((p) => <option key={p} value={p} />)}</datalist>
          </Field>
          {x.msg && <p className={/required|error|network/i.test(x.msg) ? "vcx-error" : "vcx-ok"} role="status">{plain(x.msg)}</p>}
        </form>
      </Sheet>
    </section>
  );
}
