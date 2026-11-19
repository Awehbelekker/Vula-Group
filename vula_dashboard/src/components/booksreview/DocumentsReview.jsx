/**
 * DocumentsReview.jsx — Money › Books review › Documents (19 Nov 2026, Phase 0b of Xero-quality books;
 * vula/commerce/doc_audit.py). The documents behind the money, as Vula's check found them:
 *   Figures to check        a document whose figures aren't in its own file, or don't add up — correct
 *                           what was read (the old values are kept), or say they're right
 *   On file, not booked     an invoice on file with no bill — book it, or say it isn't a bill
 *   Bills and documents     a bill booked at a different amount from its document — book it at the
 *                           document's figures (unpaid bills only)
 *   Twice                   the same document on file twice; the same bill in the books twice (a paid
 *                           copy is never cancelled here — that's for the accountant)
 *   Suppliers               one supplier on file more than once — merge; VAT numbers to check
 *   Prices and VAT          documents whose prices don't say whether VAT was in them
 *   Access                  who can see and change the books
 * Every figure is the server's; amounts typed in are read as written (randToCents), never worked out.
 */
import { useState } from "react";
import { Button, Card, Field, Input, Select, StatusChip, confirmDialog, promptDialog, toast } from "../ui/index.jsx";
import { formatMoney } from "../../lib/money.js";
import { openDocument } from "../accountant/useAccountant.js";
import { randToCents, reason } from "./useBooksReview.js";

const money = (c) => (c == null ? "—" : formatMoney(c, { cents: "always" }));
const label = (d) => [d.category || "Document", d.supplier && `from ${d.supplier}`, d.number, d.date && `(${d.date})`]
  .filter(Boolean).join(" ");

async function done(res, okText, failText) {
  if (res.ok) { toast(okText); return true; }
  toast(reason(res, failText), "danger");
  return false;
}

export const DOC_GROUPS = ["doc_figures", "not_booked", "bill_mismatch", "doc_duplicates", "bill_duplicates", "suppliers"];
export const docCount = (open) => DOC_GROUPS.reduce((n, k) => n + (open?.[k] || 0), 0);

export default function DocumentsReview({ x, r, tenantId }) {
  const d = r.documents || {};
  const open = (id) => openDocument(tenantId, id);
  return (
    <div className="vbr2-stack">
      <p className="vbr2-sub">Each money document was checked against its own file and against the books. Fix what's wrong here; every change is kept with who made it and why.</p>
      <Figures x={x} items={d.doc_figures || []} open={open} />
      <NotBooked x={x} items={d.not_booked || []} open={open} />
      <Mismatch x={x} items={d.bill_mismatch || []} open={open} />
      <Twice x={x} docs={d.doc_duplicates || []} bills={d.bill_duplicates || []} open={open} />
      <Suppliers x={x} items={d.suppliers || []} />
      <PriceVat x={x} pv={d.price_vat} open={open} />
      <Access access={d.access} />
    </div>
  );
}

function DocRow({ doc, open, children, as: Tag = "div" }) {
  return (
    <Tag className="vbr2-row vbr2-wrap">
      <span className="vbr2-text"><b>{label(doc)}</b>{doc.total_cents != null ? ` · ${money(doc.total_cents)}` : ""}
        {doc.text ? <><br /><span className="vbr2-muted">{doc.text}</span></> : null}</span>
      <span className="vbr2-actions">
        <Button size="sm" variant="ghost" onClick={() => open(doc.id)} aria-label={`Open ${label(doc)}`}>Open</Button>
        {children}
      </span>
    </Tag>
  );
}

const DocLine = (props) => <DocRow {...props} as="li" />;

// ── Figures to check ─────────────────────────────────────────────────────────

function Figures({ x, items, open }) {
  const [editing, setEditing] = useState(null);
  async function right(doc) {
    const note = await promptDialog(`The figures on ${label(doc)} are right as read? Add a note if you like:`,
      { initial: "Checked against the paper copy." });
    if (note === null) return;
    if (await done(await x.docConfirm(doc.id, note), "Noted as right.", "That didn't save — try again.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">Figures to check</h3>
      <p className="vbr2-sub">A figure Vula read that isn't in the document itself, or figures that don't add up. Open the document, then correct what was read — or say it's right.</p>
      {items.length ? (
        <ul className="vbr2-list">
          {items.map((doc) => (
            <li key={doc.id} className="vbr2-group">
              <DocRow doc={doc} open={open}>
                <StatusChip tone={doc.severity === "problem" ? "danger" : "warn"}>{doc.severity === "problem" ? "To fix" : "To look at"}</StatusChip>
                <Button size="sm" variant="secondary" onClick={() => setEditing(editing === doc.id ? null : doc.id)}>Correct the figures</Button>
                <Button size="sm" variant="ghost" onClick={() => right(doc)}>They're right</Button>
              </DocRow>
              {editing === doc.id && <FigureForm x={x} doc={doc} onDone={() => { setEditing(null); x.reload(); }} />}
            </li>
          ))}
        </ul>
      ) : <p className="vbr2-text">Every document's figures match its file.</p>}
    </Card>
  );
}

const FIELDS = [
  { key: "total_cents", label: "Total (R)", money: true },
  { key: "vat_cents", label: "VAT (R)", money: true },
  { key: "subtotal_cents", label: "Subtotal (R)", money: true },
  { key: "document_number", label: "Document number" },
  { key: "date", label: "Date (YYYY-MM-DD)" },
  { key: "supplier", label: "Supplier" },
];
const SHOWN = { document_number: "number" };

function FigureForm({ x, doc, onDone }) {
  const start = Object.fromEntries(FIELDS.map((f) => {
    const v = doc[SHOWN[f.key] || f.key];
    return [f.key, v == null ? "" : f.money ? (v / 100).toFixed(2) : String(v)];
  }));
  const [vals, setVals] = useState(start);
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    const changes = {};
    for (const f of FIELDS) {
      const v = vals[f.key].trim();
      if (v === start[f.key] || v === "") continue;
      if (f.money) {
        const c = randToCents(v);
        if (c == null) { toast(`${f.label.replace(" (R)", "")} isn't an amount — e.g. 2300.00`, "danger"); return; }
        changes[f.key] = c;
      } else changes[f.key] = v;
    }
    if (!Object.keys(changes).length) { toast("Nothing has changed.", "danger"); return; }
    if (why.trim().length < 3) { toast("Say why — it's kept for the accountant.", "danger"); return; }
    setBusy(true);
    const res = await x.docFigures(doc.id, changes, why.trim());
    setBusy(false);
    if (await done(res, res.data?.bill_differs ? "Corrected — its bill still has the old figures: book it at the document's figures below."
      : "Corrected.", "That didn't save — try again.")) onDone();
  }
  return (
    <div className="vbr2-form" role="group" aria-label={`Correct the figures on ${label(doc)}`}>
      {FIELDS.map((f) => (
        <Field key={f.key} label={f.label}>
          <Input value={vals[f.key]} inputMode={f.money ? "decimal" : undefined}
            onChange={(e) => setVals((s) => ({ ...s, [f.key]: e.target.value }))} />
        </Field>
      ))}
      <Field label="Why (kept for the accountant)">
        <Input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. the PDF says R2 300.00" />
      </Field>
      <Button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save the corrected figures"}</Button>
    </div>
  );
}

// ── On file, not in the books ────────────────────────────────────────────────

function NotBooked({ x, items, open }) {
  async function book(doc) {
    if (!(await confirmDialog(`Put ${label(doc)} for ${money(doc.total_cents)} into the books as a supplier bill?`,
      { confirmLabel: "Book it" }))) return;
    if (await done(await x.docBook(doc.id), "Booked.", "It couldn't be booked.")) x.reload();
  }
  async function notBill(doc) {
    const why = await promptDialog(`Why isn't ${label(doc)} a bill to book?`, { initial: "It's our own invoice to a client." });
    if (!why) return;
    if (await done(await x.docNotABill(doc.id, why), "Noted — it won't be asked about again.", "That didn't save — try again.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">On file, not in the books</h3>
      <p className="vbr2-sub">Invoices Vula has on file that never became a bill — so they're not in what you owe, your costs or VAT.</p>
      {items.length ? (
        <ul className="vbr2-list">
          {items.map((doc) => (
            <DocLine key={doc.id} doc={{ ...doc, text: null }} open={open}>
              <Button size="sm" variant="secondary" onClick={() => book(doc)}>Book it</Button>
              <Button size="sm" variant="ghost" onClick={() => notBill(doc)}>Not a bill</Button>
            </DocLine>
          ))}
        </ul>
      ) : <p className="vbr2-text">Every invoice on file is in the books.</p>}
    </Card>
  );
}

// ── Bills that differ from their document ────────────────────────────────────

function Mismatch({ x, items, open }) {
  async function match(it) {
    const why = await promptDialog(`Book bill ${it.bill.number || ""} at its document's figures? Say why:`,
      { initial: "The bill was booked at a misread total." });
    if (!why) return;
    if (await done(await x.billMatchDocument(it.bill.id, why), "The bill now matches its document.", "That didn't save.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">Bills that differ from their document</h3>
      {items.length ? (
        <ul className="vbr2-list">
          {items.map((it, i) => (
            <li key={i} className="vbr2-row vbr2-wrap">
              <span className="vbr2-text">{it.text}</span>
              <span className="vbr2-actions">
                {it.doc && <Button size="sm" variant="ghost" onClick={() => open(it.doc.id)} aria-label={`Open the document for ${it.bill?.number || "this bill"}`}>Open</Button>}
                {it.kind === "bill_amount" && it.bill && (it.bill.paid_cents ? (
                  <span className="vbr2-muted">Paid — check with the supplier; your accountant decides the correction.</span>
                ) : <Button size="sm" variant="secondary" onClick={() => match(it)}>Book it at the document's figures</Button>)}
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="vbr2-text">Every bill matches its document.</p>}
    </Card>
  );
}

// ── Twice ────────────────────────────────────────────────────────────────────

function Twice({ x, docs, bills, open }) {
  async function keepFirst(g) {
    const rest = g.docs.filter((d) => d.id !== g.keep);
    for (const d of rest) {
      const res = await x.docDuplicate(d.id, g.keep);
      if (!res.ok) { toast(reason(res, "That didn't save."), "danger"); x.reload(); return; }
    }
    toast("The extra copies are kept as duplicates.");
    x.reload();
  }
  async function cancelCopy(g, b) {
    const why = await promptDialog(`Cancel bill ${b.number} (${b.date}) as a second copy?`, { initial: "The same invoice booked twice." });
    if (!why) return;
    if (await done(await x.billCancelDuplicate(b.id, g.keep, why), "The second copy is cancelled.", "That didn't save.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">The same document or bill twice</h3>
      {bills.map((g, i) => (
        <div key={`b${i}`} className="vbr2-group">
          <p className="vbr2-text"><b>{g.text}</b></p>
          <ul className="vbr2-list">
            {g.bills.map((b) => (
              <li key={b.id} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text">Bill {b.number} · {b.date} · {money(b.total_cents)} · {b.status}</span>
                {b.id === g.keep ? <StatusChip tone="ok">Kept</StatusChip>
                  : b.paid_cents ? <span className="vbr2-muted">Paid — it may have been paid twice: leave for your accountant.</span>
                    : <Button size="sm" variant="secondary" onClick={() => cancelCopy(g, b)}>Cancel this copy</Button>}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {docs.map((g, i) => (
        <div key={`d${i}`} className="vbr2-group">
          <p className="vbr2-text">{g.text}</p>
          <ul className="vbr2-list">
            {g.docs.map((d) => <DocLine key={d.id} doc={d} open={open}>{d.id === g.keep && <StatusChip tone="ok">Kept</StatusChip>}</DocLine>)}
          </ul>
          <Button variant="secondary" onClick={() => keepFirst(g)}>Keep the first, the rest are copies</Button>
        </div>
      ))}
      {!docs.length && !bills.length && <p className="vbr2-text">Nothing is on file or in the books twice.</p>}
    </Card>
  );
}

// ── Suppliers ────────────────────────────────────────────────────────────────

function Suppliers({ x, items }) {
  async function merge(f) {
    const keep = f.suppliers.find((s) => s.id === f.keep) || f.suppliers[0];
    const others = f.suppliers.filter((s) => s.id !== keep.id);
    if (!(await confirmDialog(`Make ${others.map((s) => s.name).join(", ")} part of ${keep.name}? Their bills and documents move to it.`,
      { confirmLabel: "Merge" }))) return;
    if (await done(await x.mergeSuppliers(keep.id, others.map((s) => s.id)), "Merged.", "That didn't merge.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">Suppliers</h3>
      {items.length ? (
        <ul className="vbr2-list">
          {items.map((f, i) => (
            <li key={i} className="vbr2-row vbr2-wrap">
              <span className="vbr2-text">{f.text}</span>
              {f.kind === "supplier_duplicate" && <Button size="sm" variant="secondary" onClick={() => merge(f)}>Merge into one</Button>}
            </li>
          ))}
        </ul>
      ) : <p className="vbr2-text">Each supplier is on file once.</p>}
    </Card>
  );
}

// ── Prices and VAT ───────────────────────────────────────────────────────────

const BASIS = [["incl", "Prices include VAT"], ["excl", "Prices exclude VAT"], ["no_vat", "Supplier not VAT registered"]];

function PriceVat({ x, pv, open }) {
  async function set(doc, basis) {
    if (!basis) return;
    if (await done(await x.docVatBasis(doc.id, basis), "Saved for every price from that document.", "That didn't save.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">Prices with no VAT basis</h3>
      {pv ? (
        <>
          <p className="vbr2-sub">{pv.text}</p>
          <ul className="vbr2-list">
            {(pv.docs || []).map((doc) => (
              <DocLine key={doc.id} doc={doc} open={open}>
                <Select aria-label={`VAT on the prices in ${label(doc)}`} defaultValue="" onChange={(e) => set(doc, e.target.value)}>
                  <option value="">Do its prices include VAT?</option>
                  {BASIS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </Select>
              </DocLine>
            ))}
          </ul>
        </>
      ) : <p className="vbr2-text">Every learned price says whether VAT was in it.</p>}
    </Card>
  );
}

// ── Access ───────────────────────────────────────────────────────────────────

function Access({ access }) {
  const people = (access?.people || []).filter((p) => p.active !== false);
  if (!people.length) return null;
  return (
    <Card>
      <h3 className="vbr2-h">Who can see and change the books</h3>
      <ul className="vbr2-list">
        {people.map((p, i) => (
          <li key={i} className="vbr2-row vbr2-wrap">
            <span className="vbr2-text"><b>{p.name || "—"}</b> · {p.role}{p.expires_on ? ` · until ${p.expires_on}` : ""}</span>
            {p.full ? <StatusChip tone={p.why?.startsWith("no sections") ? "warn" : "muted"}>
              {p.why?.startsWith("no sections") ? "Everything — no sections chosen" : "Everything"}</StatusChip>
              : <StatusChip tone="muted">{p.role === "accountant" ? "View only" : "Some sections"}</StatusChip>}
          </li>
        ))}
      </ul>
      <p className="vbr2-sub">Choose each person's sections in Team › People.</p>
    </Card>
  );
}
