/**
 * RecurringV2.jsx — the redesigned recurring orders (2026-10-19, UX redesign Phase F). Same data
 * and actions as the current tab (recurring/useRecurring.js): one card per standing order with
 * its status and Pause / Resume / Cancel, and New / Edit in a Sheet with labelled fields.
 */
import { Pause, Pencil, Play, Plus, Repeat, Trash2, X } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, IconButton, Input, PageHeader, Select, Sheet, Skeleton, StatusChip } from "../ui/index.jsx";
import { CAD, PAY, itemsTotal, useRecurring, useSubForm } from "./useRecurring";
import "../delivery/sell.css";

const money = (c) => formatMoney(c || 0, { cents: "always" });
const STATUS = { active: ["Active", "ok"], paused: ["Paused", "warn"], cancelled: ["Cancelled", "muted"] };
const plain = (t) => String(t || "").replace(" ✓", "");

export default function RecurringV2({ tenantId }) {
  const r = useRecurring(tenantId);
  const editing = r.subs.find((s) => s.id === r.editingId);
  const closeForm = () => { r.setShowNew(false); r.setEditingId(null); };
  return (
    <div className="vsl">
      <PageHeader sub="Standing orders create a real order each cycle and message the customer. They can reply STOP to pause."
        actions={<>
          <Button variant="secondary" onClick={r.runDue}>Run due now</Button>
          <Button Icon={Plus} onClick={() => r.setShowNew(true)}>New recurring order</Button>
        </>} />
      {r.msg && <p className="vsl-msg" role="status">{plain(r.msg)}</p>}
      {r.loading ? <Card><Skeleton lines={3} /></Card> : r.subs.length === 0 ? (
        <EmptyState Icon={Repeat} title="No recurring orders yet">A customer's regular order — say, bread every Friday — set up once.</EmptyState>
      ) : (
        <ul className="vsl-grid" aria-label="Recurring orders">
          {r.subs.map((s) => {
            const [st, tone] = STATUS[s.status] || [s.status, "muted"];
            const who = s.customer_name || s.customer_phone || "Customer";
            return (
              <li key={s.id}>
                <Card className="vsl-card">
                  <div className="vsl-top">
                    <span className="vsl-name">{who}</span>
                    <StatusChip tone={tone}>{st}</StatusChip>
                  </div>
                  <p className="vsl-body">{(s.items || []).map((i) => `${i.quantity} × ${i.product_name}`).join(", ") || "—"}</p>
                  <p className="vsl-sub">
                    <span className="vula-money">{money(itemsTotal(s.items) + (s.delivery_cents || 0))}</span> · {CAD[s.cadence] || s.cadence} · {PAY[s.payment_method] || s.payment_method}
                    {" · "}next {s.next_run}{s.last_run ? ` · last ${String(s.last_run).slice(0, 10)}` : ""}
                  </p>
                  {s.status !== "cancelled" && (
                    <div className="vsl-actions">
                      <Button size="sm" variant="secondary" Icon={Pencil} onClick={() => r.setEditingId(s.id)} aria-label={`Edit ${who}'s order`}>Edit</Button>
                      {s.status === "active"
                        ? <Button size="sm" variant="secondary" Icon={Pause} onClick={() => r.setStatus(s.id, "paused")} aria-label={`Pause ${who}'s order`}>Pause</Button>
                        : <Button size="sm" variant="secondary" Icon={Play} onClick={() => r.setStatus(s.id, "active")} aria-label={`Resume ${who}'s order`}>Resume</Button>}
                      <Button size="sm" variant="quiet" Icon={Trash2} onClick={() => r.setStatus(s.id, "cancelled")} aria-label={`Cancel ${who}'s order`}>Cancel</Button>
                    </div>)}
                </Card>
              </li>
            );
          })}
        </ul>
      )}

      <Sheet open={r.showNew || !!editing} title={editing ? `Edit ${editing.customer_name || "recurring order"}` : "New recurring order"} onClose={closeForm}>
        {(r.showNew || editing) && (
          <SubForm key={editing?.id || "new"} tenantId={tenantId} products={r.products} sub={editing} flash={r.flash} onCancel={closeForm}
            onDone={() => { closeForm(); r.load(); r.flash(editing ? "Recurring order updated" : "Recurring order created"); }} />)}
      </Sheet>
    </div>
  );
}

function SubForm({ tenantId, products, sub, flash, onDone, onCancel }) {
  const x = useSubForm({ tenantId, products, sub, flash, onDone });
  return (
    <div className="vsl-form">
      <Field label="Customer name"><Input value={x.f.customer_name} onChange={(e) => x.setF({ ...x.f, customer_name: e.target.value })} /></Field>
      {!x.editing && <Field label="Customer WhatsApp number"><Input type="tel" placeholder="27…" value={x.f.customer_phone} onChange={(e) => x.setF({ ...x.f, customer_phone: e.target.value })} /></Field>}
      <div className="vsl-two">
        <Field label="How often">
          <Select value={x.f.cadence} onChange={(e) => x.setF({ ...x.f, cadence: e.target.value })}>
            {Object.entries(CAD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Payment">
          <Select value={x.f.payment_method} onChange={(e) => x.setF({ ...x.f, payment_method: e.target.value })}>
            {Object.entries(PAY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
      </div>
      <fieldset className="vsl-lines">
        <legend className="vsl-legend">Items</legend>
        {x.lines.map((l, i) => (
          <div key={i} className="vsl-line">
            <Select aria-label={`Product ${i + 1}`} value={l.product_id} onChange={(e) => x.setLine(i, { product_id: e.target.value })}>
              <option value="">Product…</option>
              {products.map((p) => <option key={p.id} value={p.id}>{p.name} ({money(p.price_cents)})</option>)}
            </Select>
            <Input aria-label={`Quantity ${i + 1}`} type="number" min="0.5" step="0.5" value={l.quantity} onChange={(e) => x.setLine(i, { quantity: e.target.value })} />
            {x.lines.length > 1 && <IconButton Icon={X} quiet label={`Remove item ${i + 1}`} onClick={() => x.removeLine(i)} />}
          </div>
        ))}
        <Button size="sm" variant="quiet" Icon={Plus} onClick={x.addLine}>Add an item</Button>
      </fieldset>
      <div className="vsl-actions">
        <Button disabled={x.saving} onClick={x.save}>{x.saving ? "Saving…" : x.editing ? "Save changes" : "Create recurring order"}</Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
