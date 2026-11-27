/**
 * BooksReview.jsx — Money › Books review (18 Nov 2026, Phase 0d of Xero-quality books).
 *
 * Before an accountant sees the books, the business works through what Vula's books audit and the
 * rebuild dry run found (vula/commerce/books_review.py) and fixes each item:
 *   Overview     every check with its result, what the books would look like, and — when it's clean,
 *                or with what's still open noted — the business agrees the check (then Vula signs off)
 *   Bank lines   set aside by mistake (put back), read twice (keep one), moved between own accounts
 *                (a transfer), and lines Vula wasn't sure how to categorise
 *   In transit   payments recorded with no bank line — "this bank line is that payment"
 *   Staff claims claims paid with staff's own money, not yet marked paid back
 *   Paperwork    draft bills and invoices, and dates that were misread
 *   Documents    the documents behind the money, checked against their own files and the books
 *                (DocumentsReview.jsx, 19 Nov 2026)
 *   Statements   upload an official bank statement: compared line by line, nothing changes until the
 *                ticked actions are applied
 * Owners and managers only (the server says so with a 403). Every figure is the server's.
 */
import { useMemo, useState } from "react";
import { Button, Card, DataTable, EmptyState, ErrorCard, Field, Input, PageHeader, PillTabs, Select,
  Skeleton, StatusChip, Textarea, confirmDialog, promptDialog, toast } from "../ui/index.jsx";
import { formatMoney } from "../../lib/money.js";
import { reason, useBooksReview } from "./useBooksReview.js";
import DocumentsReview, { docCount } from "./DocumentsReview.jsx";
import "./booksreview.css";

const money = (c) => formatMoney(c, { cents: "always" });
const signed = (ln) => (ln.direction === "in" ? "+" : "−") + money(ln.amount_cents);
const TONE = { ok: "ok", attention: "warn", problem: "danger" };
const STATUS = { ok: "Fine", attention: "To look at", problem: "To fix" };
const KIND = { bill: "Supplier bill", invoice: "Invoice", expense: "Expense" };

async function done(res, okText, failText) {
  if (res.ok) { toast(okText); return true; }
  toast(reason(res, failText), "danger");
  return false;
}

export default function BooksReview({ tenantId }) {
  const x = useBooksReview(tenantId);
  const [tab, setTab] = useState("overview");
  const [busy, setBusy] = useState(false);
  if (x.state === "loading") return <Card><Skeleton lines={6} /></Card>;
  if (x.state === "failed") return <ErrorCard what="the books review" onRetry={x.reload} />;
  if (x.state === "off") {
    return (
      <EmptyState title="Only an owner or manager can review the books">
        Ask the business owner to sign in to work through the books check.
      </EmptyState>
    );
  }

  async function runCheck() {
    setBusy(true);
    const res = await x.runCheck();
    setBusy(false);
    if (await done(res, "Checked again — the list below is up to date.", "The check didn't run — try again.")) x.reload();
  }

  const r = x.review || {};
  const open = r.open || {};
  const header = (
    <PageHeader title="Books review"
      sub="Work through what Vula found in your books before your accountant sees them. Every change is kept with its reason."
      actions={<Button onClick={runCheck} disabled={busy}>{busy ? "Checking… (about a minute)" : r.report ? "Run the check again" : "Run the check"}</Button>} />
  );
  if (!r.report) {
    return (
      <div className="vbr2-page">
        {header}
        <EmptyState title="No books check yet">
          Run the check: Vula reads every bank line, bill, invoice and expense and lists what needs fixing.
        </EmptyState>
      </div>
    );
  }
  const n = (k) => (open[k] ? ` (${open[k]})` : "");
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "lines", label: `Bank lines${lineCount(open) ? ` (${lineCount(open)})` : ""}` },
    { id: "transit", label: `In transit${n("in_transit")}` },
    { id: "claims", label: `Staff claims${n("staff_claims")}` },
    { id: "paperwork", label: `Paperwork${(open.drafts || 0) + (open.odd_dates || 0) ? ` (${(open.drafts || 0) + (open.odd_dates || 0)})` : ""}` },
    { id: "documents", label: `Documents${docCount(open) ? ` (${docCount(open)})` : ""}` },
    { id: "statements", label: "Statements" },
  ];
  return (
    <div className="vbr2-page">
      {header}
      <PillTabs label="Books review" active={tab} onChange={setTab} tabs={tabs} />
      {tab === "overview" && <Overview x={x} r={r} />}
      {tab === "lines" && <Lines x={x} r={r} />}
      {tab === "transit" && <Transit x={x} r={r} />}
      {tab === "claims" && <Claims x={x} r={r} />}
      {tab === "paperwork" && <Paperwork x={x} r={r} />}
      {tab === "documents" && <DocumentsReview x={x} r={r} tenantId={tenantId} />}
      {tab === "statements" && <Statements x={x} />}
    </div>
  );
}

// ── Overview ─────────────────────────────────────────────────────────────────

function Overview({ x, r }) {
  const books = r.books || {};
  const tb = (books.trial_balance || {}).after || {};
  const ctl = books.control || {};
  const balanced = Object.values(tb).length > 0 && Object.values(tb).every((v) => v.balanced);
  const report = r.report;
  const [note, setNote] = useState("");
  const left = Object.entries(r.open || {}).filter(([, v]) => v);

  async function agree() {
    if (!r.clean && note.trim().length < 10) {
      toast("Some items are still open — say in the note what you accept as open, for the accountant.", "danger");
      return;
    }
    if (!(await confirmDialog("Agree this books check? Vula's team then signs it off before your books are rebuilt.",
      { confirmLabel: "Agree" }))) return;
    if (await done(await x.agree(report.id, note.trim()), "Agreed — thank you.", "That didn't save — try again.")) x.reload();
  }

  return (
    <div className="vbr2-stack">
      <Card>
        <h3 className="vbr2-h">What Vula checked</h3>
        <p className="vbr2-sub">Last run {String(report.created_at || "").slice(0, 16).replace("T", " ")}.</p>
        <ul className="vbr2-list">
          {(r.checks || []).map((c) => (
            <li key={c.key} className="vbr2-row">
              <StatusChip tone={TONE[c.status] || "muted"}>{STATUS[c.status] || c.status}</StatusChip>
              <span className="vbr2-text"><b>{c.title}</b> — {c.headline}</span>
            </li>
          ))}
        </ul>
      </Card>
      {!books.error && (
        <Card>
          <h3 className="vbr2-h">Your books, as they would be</h3>
          <ul className="vbr2-list">
            <li className="vbr2-row"><StatusChip tone={balanced ? "ok" : "danger"}>{balanced ? "Balance" : "Don't balance"}</StatusChip>
              <span className="vbr2-text">Both views of the books (accrual and cash)</span></li>
            <li className="vbr2-row"><StatusChip tone={ctl.debtors_tie ? "ok" : "warn"}>{ctl.debtors_tie ? "Tie" : "Don't tie"}</StatusChip>
              <span className="vbr2-text">Owed to you: {money(ctl.debtors_books_cents || 0)}</span></li>
            <li className="vbr2-row"><StatusChip tone={ctl.creditors_tie ? "ok" : "warn"}>{ctl.creditors_tie ? "Tie" : "Don't tie"}</StatusChip>
              <span className="vbr2-text">You owe suppliers: {money(ctl.creditors_books_cents || 0)}</span></li>
            <li className="vbr2-row"><span className="vbr2-text">To categorise: {money(Math.abs((books.suspense || {}).cents || 0))} on {(books.suspense || {}).lines || 0} lines</span></li>
            <li className="vbr2-row"><span className="vbr2-text">In transit (paid, with no bank line): {money(Math.abs(books.in_transit_cents || 0))}</span></li>
            <li className="vbr2-row"><span className="vbr2-text">Owed to staff for claims: {money(books.owed_to_staff_cents || 0)}</span></li>
            {r.no_account ? <li className="vbr2-row"><span className="vbr2-text">{r.no_account} bank lines have no account number — upload your official bank statements under Statements.</span></li> : null}
          </ul>
        </Card>
      )}
      <Card>
        <h3 className="vbr2-h">Agree the check</h3>
        {report.signed_off_by ? (
          <p className="vbr2-text">Signed off by {report.signed_off_by} on {String(report.signed_off_at).slice(0, 10)}.</p>
        ) : report.agreed_by ? (
          <p className="vbr2-text">You agreed this check on {String(report.agreed_at).slice(0, 10)}. Vula's team signs it off next.</p>
        ) : (
          <>
            {left.length ? (
              <p className="vbr2-sub">Still open: {left.map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.
                Fix them, or say in the note what you accept as open.</p>
            ) : <p className="vbr2-sub">Nothing is left to fix.</p>}
            <Field label="Note for your accountant">
              <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. the two drafts from Build It are being checked with the supplier" />
            </Field>
            <Button onClick={agree}>{r.clean ? "Agree the check" : "Agree, with what's still open noted"}</Button>
          </>
        )}
      </Card>
    </div>
  );
}

// ── Bank lines ───────────────────────────────────────────────────────────────

function LineTable({ rows, caption, select }) {
  const cols = [
    ...(select ? [{ key: "pick", label: "Choose", width: 70, render: (ln) => (
      <input type="checkbox" aria-label={`Choose ${ln.description}`} checked={select.picked.has(ln.id)}
        onChange={() => select.toggle(ln.id)} />) }] : []),
    { key: "date", label: "Date", width: 110 },
    { key: "description", label: "What the bank says" },
    { key: "amount_cents", label: "Amount", align: "right", render: (ln) => signed(ln) },
    { key: "bank_account", label: "Account", render: (ln) => ln.bank_account || "—" },
  ];
  return <DataTable columns={cols} rows={rows} caption={caption} stack />;
}

const lineCount = (open) => ["restore", "duplicates", "transfers", "to_categorise", "bill_payments", "invoice_payments", "refile"]
  .reduce((t, k) => t + (open[k] || 0), 0);

function usePick(rows) {
  const [picked, setPicked] = useState(() => new Set(rows.map((r) => r.id)));
  return { picked, toggle: (id) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }) };
}

function Lines({ x, r }) {
  const restore = r.restore || [];
  const sel = usePick(restore);
  const options = useMemo(() => x.accounts || [], [x.accounts]);

  async function putBack() {
    const ids = restore.filter((ln) => sel.picked.has(ln.id)).map((ln) => ln.id);
    if (!ids.length) return;
    const why = await promptDialog(`Put ${ids.length} line${ids.length === 1 ? "" : "s"} back in the books? Say why (kept for the accountant):`,
      { initial: "The running balance shows these lines are real." });
    if (!why) return;
    if (await done(await x.restore(ids, why), "Put back in the books.", "That didn't save — try again.")) x.reload();
  }
  async function keepOne(group) {
    const rest = group.lines.slice(1).map((ln) => ln.id);
    if (await done(await x.setAside(rest, "The same bank line read twice; one copy kept."),
      "The extra copies are set aside.", "That didn't save — try again.")) x.reload();
  }
  async function isTransfer(pair) {
    if (await done(await x.transfer(pair.lines.map((ln) => ln.id)), "Booked as a transfer between your accounts.",
      "That didn't save — try again.")) x.reload();
  }
  async function categorise(ln, code) {
    if (!code) return;
    if (await done(await x.categorise([ln.id], code), "Categorised.", "That didn't save — try again.")) x.reload();
  }
  const nameOf = (code) => (options.find((a) => a.code === code) || {}).name || code || "nothing";
  async function refile(g, code) {
    if (!code) return;
    const what = `${g.count} line${g.count === 1 ? "" : "s"} for ${g.label}`;
    if (!(await confirmDialog(`File ${what} as ${nameOf(code)}? Vula remembers this for the next one from them.`,
      { confirmLabel: `File as ${nameOf(code)}` }))) return;
    if (await done(await x.categorise(g.ids, code), "Re-filed.", "That didn't save — try again.")) x.reload();
  }
  // 25 Nov 2026: the suggested account in one tap — keeping a payee where all its lines already are,
  // or bank charges as bank charges. Saved as the owner's choice, so Vula remembers the payee.
  async function refileSuggested(g) {
    const code = g.keep ? g.current : g.suggested;
    if (await done(await x.categorise(g.ids, code),
      `${g.count} line${g.count === 1 ? "" : "s"} for ${g.label} ${g.keep ? "kept as" : "filed as"} ${nameOf(code)}.`,
      "That didn't save — try again.")) x.reload();
  }

  return (
    <div className="vbr2-stack">
      <Card>
        <h3 className="vbr2-h">Set aside by mistake</h3>
        <p className="vbr2-sub">Each of these fills a gap in its statement's running balance, so it's a real line that was set aside with a misread copy.</p>
        {restore.length ? (
          <>
            <LineTable rows={restore} caption="Lines set aside by mistake" select={sel} />
            <Button onClick={putBack}>Put the chosen lines back</Button>
          </>
        ) : <p className="vbr2-text">Nothing here — well done.</p>}
      </Card>
      <Card>
        <h3 className="vbr2-h">Read twice</h3>
        {(r.duplicates || []).length ? (r.duplicates || []).map((g, i) => (
          <div key={i} className="vbr2-group">
            <LineTable rows={g.lines} caption={`Copies of the same line, group ${i + 1}`} />
            <Button variant="secondary" onClick={() => keepOne(g)}>Keep the first, set the rest aside</Button>
          </div>
        )) : <p className="vbr2-text">No line is in the books twice.</p>}
      </Card>
      <Card>
        <h3 className="vbr2-h">Moved between your own accounts</h3>
        <p className="vbr2-sub">Money out of one of your accounts and into another isn't income or a cost.</p>
        {(r.transfers || []).length ? (r.transfers || []).map((p, i) => (
          <div key={i} className="vbr2-group">
            <LineTable rows={p.lines} caption={`Possible transfer ${i + 1}`} />
            <Button variant="secondary" onClick={() => isTransfer(p)}>Yes, it's a transfer</Button>
          </div>
        )) : <p className="vbr2-text">None found.</p>}
      </Card>
      <Card>
        <h3 className="vbr2-h">To categorise</h3>
        {(r.to_categorise || []).length ? (
          <ul className="vbr2-list">
            {(r.to_categorise || []).map((ln) => (
              <li key={ln.id} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text">{ln.date} · {ln.description} · <b>{signed(ln)}</b></span>
                <Select aria-label={`Category for ${ln.description}`} defaultValue="" onChange={(e) => categorise(ln, e.target.value)}>
                  <option value="">Choose a category…</option>
                  {options.filter((a) => !a.directions || a.directions.includes(ln.direction))
                    .map((a) => <option key={a.code} value={a.code}>{a.name}</option>)}
                </Select>
              </li>
            ))}
          </ul>
        ) : <p className="vbr2-text">Every line has a category.</p>}
      </Card>
      <BillPayments x={x} groups={r.bill_payments || []} options={options} nameOf={nameOf} />
      <InvoicePayments x={x} groups={r.invoice_payments || []} />
      <Card>
        <h3 className="vbr2-h">Filed by an old rule</h3>
        <p className="vbr2-sub">Vula filed these by a single word in the bank's description (like "App" in "FNB App
          Payment"), not by who was paid, and bank charges ended up under other accounts. Pick the right account once
          per payee — or, where every line of a payee is already on one account and that's right, keep it there in one
          tap. Vula remembers it for their next payment.</p>
        {(r.refile || []).length ? (
          <ul className="vbr2-list">
            {(r.refile || []).map((g) => (
              <li key={g.key} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text">
                  <b>{g.label}</b> · {g.count} line{g.count === 1 ? "" : "s"} · <b>{money(Math.abs(g.cents))}</b>
                  <br /><span className="vbr2-muted">{g.why_text}. Now under {g.accounts.map((a) => nameOf(a.code)).join(", ")}
                    {g.suggested ? ` — these are ${nameOf(g.suggested).toLowerCase()}` : ""}.</span>
                </span>
                {(g.suggested || g.keep) && (
                  <Button size="sm" onClick={() => refileSuggested(g)}
                    aria-label={`${g.keep ? "Keep" : "File"} ${g.label} as ${nameOf(g.keep ? g.current : g.suggested)}`}>
                    {g.keep ? "Keep as" : "File as"} {nameOf(g.keep ? g.current : g.suggested)}</Button>)}
                <Select aria-label={`Account for ${g.label}`} defaultValue="" onChange={(e) => refile(g, e.target.value)}>
                  <option value="">File them as…</option>
                  {options.filter((a) => !a.directions || a.directions.includes(g.direction))
                    .map((a) => <option key={a.code} value={a.code}>{a.name}</option>)}
                </Select>
              </li>
            ))}
          </ul>
        ) : <p className="vbr2-text">Nothing left that an old rule filed.</p>}
      </Card>
    </div>
  );
}

// ── Bank payments that pay a supplier's open bill ─────────────────────────────

function BillPayments({ x, groups, options, nameOf }) {
  const [acct, setAcct] = useState({});
  if (!groups.length) return null;
  const outs = options.filter((a) => !a.directions || a.directions.includes("out"));
  const names = (o) => o.bills.map((b) => b.number || "bill").join(" + ");
  async function pay(g, ln, o) {
    const code = acct[g.key];
    if (!code) { toast("Choose the account for these payments first.", "danger"); return; }
    const marked = o.bills.filter((b) => b.already_paid).map((b) => b.number).join(", ");
    if (!(await confirmDialog(`${ln.date} ${money(ln.amount_cents)} paid ${names(o)}? `
      + (o.bills.length > 1 ? "Each bill gets its share. " : "") + "Bills still owed are marked paid on that date"
      + (marked ? ` (${marked} is already marked paid and is only tied to this payment)` : "")
      + `, and the payment is filed as ${nameOf(code)}, so each cost is counted once.`,
    { confirmLabel: `It paid ${names(o)}` }))) return;
    if (await done(await x.payBill(ln.id, o.bills.map((b) => b.id), code), "Tied to the bill.", "That didn't save — try again.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">Payments that pay a supplier's bill</h3>
      <p className="vbr2-sub">Each payment names a supplier whose bills come to exactly that amount — one bill, or two or
        three of theirs paid together. Tie them so no bill is booked as a second cost. Vula also remembers the name you
        pay them under.</p>
      {groups.map((g) => (
        <div key={g.key} className="vbr2-group">
          <div className="vbr2-row vbr2-wrap">
            <span className="vbr2-text"><b>{g.supplier}</b> · {g.lines.length} payment{g.lines.length === 1 ? "" : "s"} · <b>{money(g.cents)}</b></span>
            <Select aria-label={`Account for payments to ${g.supplier}`} value={acct[g.key] || ""}
              onChange={(e) => setAcct((s) => ({ ...s, [g.key]: e.target.value }))}>
              <option value="">File the payments as…</option>
              {outs.map((a) => <option key={a.code} value={a.code}>{a.name}</option>)}
            </Select>
          </div>
          <ul className="vbr2-list">
            {g.lines.map((ln) => (
              <li key={ln.id} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text">{ln.date} · {ln.description} · <b>{money(ln.amount_cents)}</b></span>
                <span className="vbr2-actions">
                  {(ln.options || []).map((o) => (
                    <Button key={o.bills.map((b) => b.id).join("+")} variant="secondary" onClick={() => pay(g, ln, o)}>
                      {o.bills.length === 1
                        ? `Paid ${names(o)} (${o.bills[0].date}${o.bills[0].project ? `, ${o.bills[0].project}` : ""})`
                        : `Paid ${o.bills.map((b) => `${b.number} ${money(b.owed_cents)}${b.already_paid ? " (marked paid)" : ""}`).join(" + ")}`}
                    </Button>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Card>
  );
}

// ── A customer's payment for one or several of the business's invoices (22 Nov 2026) ──────

function InvoicePayments({ x, groups }) {
  if (!groups.length) return null;
  const names = (o) => o.invoices.map((i) => i.number || "invoice").join(" + ");
  async function pay(ln, o) {
    const drafts = o.invoices.filter((i) => i.draft).map((i) => i.number).join(", ");
    const marked = o.invoices.filter((i) => i.already_paid).map((i) => i.number).join(", ");
    if (!(await confirmDialog(`${ln.date} ${money(ln.amount_cents)} paid ${names(o)}? `
      + (o.invoices.length > 1 ? "Each invoice gets its share. " : "") + "Invoices still owed are marked paid on that date"
      + (drafts ? ` (${drafts} is still a draft — it was sent after all)` : "")
      + (marked ? ` (${marked} is already marked paid and is only tied to this payment)` : "")
      + ", and the payment is filed as sales, so each sale is counted once.",
    { confirmLabel: `It paid ${names(o)}` }))) return;
    if (await done(await x.payInvoices(ln.id, o.invoices.map((i) => i.id)), "Tied to the invoice.", "That didn't save — try again.")) x.reload();
  }
  return (
    <Card>
      <h3 className="vbr2-h">Payments from a customer for their invoices</h3>
      <p className="vbr2-sub">Each payment names a customer (or one of their invoice numbers) whose invoices come to exactly
        that amount — one invoice, or two or three of theirs paid together. Tie them so each invoice is paid once and the
        money isn't counted as a second sale.</p>
      {groups.map((g) => (
        <div key={g.key} className="vbr2-group">
          <p className="vbr2-text"><b>{g.customer}</b> · {g.lines.length} payment{g.lines.length === 1 ? "" : "s"} · <b>{money(g.cents)}</b></p>
          <ul className="vbr2-list">
            {g.lines.map((ln) => (
              <li key={ln.id} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text">{ln.date} · {ln.description} · <b>{money(ln.amount_cents)}</b></span>
                <span className="vbr2-actions">
                  {(ln.options || []).map((o) => (
                    <Button key={o.invoices.map((i) => i.id).join("+")} variant="secondary" onClick={() => pay(ln, o)}>
                      {o.invoices.length === 1
                        ? `Paid ${names(o)} (${o.invoices[0].date}${o.invoices[0].draft ? ", draft" : ""})`
                        : `Paid ${o.invoices.map((i) => `${i.number} ${money(i.owed_cents)}${i.draft ? " (draft)" : ""}${i.already_paid ? " (marked paid)" : ""}`).join(" + ")}`}
                    </Button>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Card>
  );
}

// ── In transit ───────────────────────────────────────────────────────────────

function Transit({ x, r }) {
  const docs = r.in_transit || [];
  async function link(doc, ln) {
    const target = doc.kind === "expense" ? { expense_id: doc.id } : { invoice_id: doc.id };
    if (await done(await x.link(ln.id, target), "Tied together — no longer in transit.", "That didn't save — try again.")) x.reload();
  }
  async function notPaid(doc) {
    if (!(await confirmDialog(`Mark ${doc.label || "this bill"} as not paid yet?`, { confirmLabel: "Not paid yet" }))) return;
    if (await done(await x.recordDraft(doc.id), "Marked as still owed.", "That didn't save — try again.")) x.reload();
  }
  if (!docs.length) return <Card><p className="vbr2-text">Every recorded payment has its bank line.</p></Card>;
  return (
    <div className="vbr2-stack">
      <p className="vbr2-sub">These are marked paid, but no bank line is tied to them. Often the bank line is in the books as a separate cost, so the same money is counted twice. Tie each to its bank line.</p>
      {docs.map((d) => (
        <Card key={`${d.kind}-${d.id}`}>
          <div className="vbr2-row vbr2-wrap">
            <span className="vbr2-text"><b>{KIND[d.kind] || d.kind} {d.label}</b> · {d.date} · {money(Math.abs(d.open_cents))}</span>
            {d.kind === "bill" && <Button variant="ghost" onClick={() => notPaid(d)}>It wasn't paid</Button>}
          </div>
          {(d.candidates || []).length ? (
            <ul className="vbr2-list">
              {d.candidates.map((ln) => (
                <li key={ln.id} className="vbr2-row vbr2-wrap">
                  <span className="vbr2-text">{ln.date} · {ln.description} · {signed(ln)}</span>
                  <Button variant="secondary" onClick={() => link(d, ln)}>This is the payment</Button>
                </li>
              ))}
            </ul>
          ) : <p className="vbr2-sub">No bank line with this amount — check whether it was really paid, or from which account.</p>}
        </Card>
      ))}
    </div>
  );
}

// ── Staff claims ─────────────────────────────────────────────────────────────

function Claims({ x, r }) {
  const rows = r.staff_claims || [];
  async function paidBack(c) {
    if (await done(await x.paidBack(c.id), "Marked as paid back.", "That didn't save — try again.")) x.reload();
  }
  const cols = [
    { key: "date", label: "Date", width: 110 },
    { key: "paid_by_name", label: "Who paid", render: (c) => c.paid_by_name || "—" },
    { key: "description", label: "What", render: (c) => c.description || c.supplier || "—" },
    { key: "amount_cents", label: "Amount", align: "right", render: (c) => money(c.amount_cents) },
    { key: "act", label: "", render: (c) => <Button size="sm" variant="secondary" onClick={() => paidBack(c)}>Paid back</Button> },
  ];
  return (
    <Card>
      <h3 className="vbr2-h">Claims paid with staff's own money</h3>
      <p className="vbr2-sub">Until they're marked paid back, the books show this money as owed to the person.</p>
      <DataTable columns={cols} rows={rows} caption="Staff claims not yet paid back" stack empty="No claims waiting." />
    </Card>
  );
}

// ── Paperwork ────────────────────────────────────────────────────────────────

function Paperwork({ x, r }) {
  async function record(d) {
    if (await done(await x.recordDraft(d.id), "Recorded.", "That didn't save — try again.")) x.reload();
  }
  async function cancel(d) {
    const why = await promptDialog(`Cancel ${d.invoice_number || "this draft"}? Say why:`, { initial: "A duplicate of another bill." });
    if (!why) return;
    if (await done(await x.cancelDraft(d.id, why), "Cancelled.", "That didn't save — try again.")) x.reload();
  }
  async function fixDate(o) {
    const date = await promptDialog("The right date (YYYY-MM-DD):", { initial: o.date });
    if (!date) return;
    const why = await promptDialog("Why? (kept for the accountant)", { initial: "The year was misread from the slip." });
    if (!why) return;
    if (await done(await x.correctDate(o.kind, o.id, date, why), "Date corrected.", "That didn't save — try again.")) x.reload();
  }
  const draftCols = [
    { key: "issue_date", label: "Date", width: 110 },
    { key: "invoice_number", label: "Number" },
    { key: "who", label: "From / to", render: (d) => (d.direction === "inbound" ? d.supplier : d.customer_name) || "—" },
    { key: "total_cents", label: "Total", align: "right", render: (d) => money(d.total_cents) },
    { key: "act", label: "", render: (d) => (
      <span className="vbr2-actions">
        <Button size="sm" variant="secondary" onClick={() => record(d)}>Record it</Button>
        <Button size="sm" variant="ghost" onClick={() => cancel(d)}>Cancel it</Button>
      </span>) },
  ];
  return (
    <div className="vbr2-stack">
      <Card>
        <h3 className="vbr2-h">Drafts</h3>
        <p className="vbr2-sub">A draft isn't in the books yet. Record the real ones; cancel duplicates and quotes filed as bills.</p>
        <DataTable columns={draftCols} rows={r.drafts || []} caption="Draft bills and invoices" stack empty="No drafts." />
      </Card>
      <Card>
        <h3 className="vbr2-h">Dates that look wrong</h3>
        {(r.odd_dates || []).length ? (
          <ul className="vbr2-list">
            {(r.odd_dates || []).map((o) => (
              <li key={o.id} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text">{o.date} · {o.what}</span>
                <Button size="sm" variant="secondary" onClick={() => fixDate(o)}>Fix the date</Button>
              </li>
            ))}
          </ul>
        ) : <p className="vbr2-text">No odd dates.</p>}
      </Card>
    </div>
  );
}

// ── Official statements ──────────────────────────────────────────────────────

const ACTION = {
  restore: "Put back in the books", add: "Add — it's on the statement but not in Vula",
  set_aside: "Set aside — not on the statement", review: "Check by hand — it's tied to a bill, invoice or worker",
  tag: "Tag with this account number",
};

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** Every waiting comparison checked again against the bank lines as they are now, and what needs no
 *  person applied in one go (25 Nov 2026). Shows what it would do first; anything tied to an invoice,
 *  bill, order or worker, and statements from before the books start, stay for a person. */
function ApplyAll({ x }) {
  const waiting = (x.checks || []).filter((c) => c.status === "proposed").length;
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  if (!waiting && !result) return null;
  async function run() {
    setBusy("Checking each statement again…");
    const pre = await x.applyAll(false);
    if (!pre.ok) { setBusy(""); toast(reason(pre, "Couldn't check the statements — try again."), "danger"); return; }
    const t = pre.data.totals || {};
    const changes = (t.restore || 0) + (t.add || 0) + (t.set_aside || 0) + (t.tagged || 0);
    const sum = [t.restore && plural(t.restore, "line put back", "lines put back"),
      t.add && plural(t.add, "line added", "lines added"), t.set_aside && plural(t.set_aside, "line set aside", "lines set aside"),
      t.tagged && plural(t.tagged, "line tagged with its account", "lines tagged with their account")].filter(Boolean).join(", ");
    setBusy("");
    const ask = `${plural(pre.data.statements.length, "statement", "statements")} checked again: ${sum || "nothing to change"}.`
      + (pre.data.left ? ` ${plural(pre.data.left, "statement stays", "statements stay")} for you to look at.` : "")
      + (t.review ? ` ${plural(t.review, "line is", "lines are")} tied to an invoice, bill or worker and won't be touched.` : "");
    if (!(await confirmDialog(ask, { confirmLabel: changes ? "Apply them" : "Close the comparisons" }))) return;
    setBusy("Applying…");
    const res = await x.applyAll(true);
    setBusy("");
    if (!res.ok) { toast(reason(res, "That didn't apply — try again."), "danger"); return; }
    setResult(res.data);
    toast(res.data.failed ? `${res.data.failed} didn't save — check again.` : `${plural(res.data.applied, "statement", "statements")} applied.`,
      res.data.failed ? "warn" : "ok");
    x.reload();
  }
  const left = (result?.statements || []).filter((s) => s.status === "left" || s.status === "failed");
  return (
    <Card>
      <h3 className="vbr2-h">Apply every waiting statement</h3>
      <p className="vbr2-sub">
        {waiting ? `${plural(waiting, "comparison is", "comparisons are")} waiting. ` : ""}
        Vula reads each statement again, compares it with the bank lines as they are now, and applies what needs no
        decision: lines put back, missing lines added, copies set aside, lines tagged with their account. You see the
        totals before anything changes.
      </p>
      <Button onClick={run} disabled={!!busy || !waiting}>{busy || "Check again and apply all"}</Button>
      {left.length > 0 && (
        <ul className="vbr2-list" aria-label="Statements left for you">
          {left.map((s, i) => (
            <li key={i} className="vbr2-row vbr2-wrap">
              <span className="vbr2-text"><b>{s.account || "—"} · {s.from} to {s.to}</b> — {s.why}</span>
            </li>))}
        </ul>)}
    </Card>
  );
}

function Statements({ x }) {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(null);
  const [ticked, setTicked] = useState(new Set());

  // Several statements at once (19 Nov 2026): each is read and compared on its own, one after another,
  // and each gets its own comparison to open and apply. Nothing changes until one is applied.
  async function upload() {
    if (!files.length) return;
    const out = [];
    for (const [i, f] of files.entries()) {
      setBusy(`Reading ${i + 1} of ${files.length}…`);
      const res = await x.upload(f);
      out.push({ name: f.name, ok: res.ok, id: res.data?.check_id,
        why: res.ok ? null : reason(res, "It couldn't be read exactly.") });
      setResults([...out]);
    }
    setBusy("");
    setFiles([]);
    const good = out.filter((r) => r.ok);
    toast(`${good.length} of ${out.length} compared — nothing has changed yet.`, good.length === out.length ? "ok" : "warn");
    x.reload();
    if (out.length === 1 && good.length === 1 && good[0].id) show(good[0].id);
  }
  async function show(id) {
    const res = await x.check(id);
    if (!res.ok) { toast(reason(res, "Couldn't open that comparison."), "danger"); return; }
    setOpen(res.data);
    setTicked(new Set((res.data.actions || []).filter((a) => a.kind !== "review").map((a) => a.id)));
  }
  async function apply() {
    const accept = [...ticked];
    if (!(await confirmDialog(`Apply ${accept.length} change${accept.length === 1 ? "" : "s"} from ${open.filename}?`,
      { confirmLabel: "Apply" }))) return;
    const res = await x.apply(open.id, accept);
    if (await done(res, res.data.not_saved && res.data.not_saved.length ? `${res.data.not_saved.length} didn't save — open it again.` : "Applied.",
      "That didn't apply — try again.")) { setOpen(null); x.reload(); }
  }
  const toggle = (id) => setTicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="vbr2-stack">
      <Card>
        <h3 className="vbr2-h">Upload official bank statements</h3>
        <p className="vbr2-sub">The PDFs from online banking › Statements (not a transaction history) — choose as many as you like. Vula reads each, checks it adds up to the cent and compares it line by line. Nothing changes until you apply.</p>
        <div className="vbr2-row vbr2-wrap">
          <Field label="Statement PDFs">
            <Input type="file" accept="application/pdf" multiple onChange={(e) => setFiles([...(e.target.files || [])])} />
          </Field>
          <Button onClick={upload} disabled={!files.length || !!busy}>
            {busy || (files.length > 1 ? `Compare ${files.length} statements` : "Compare it")}</Button>
        </div>
        {results.length > 0 && (
          <ul className="vbr2-list" aria-label="Statements just compared">
            {results.map((r, i) => (
              <li key={i} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text"><b>{r.name}</b>{r.why ? ` — ${r.why}` : ""}</span>
                {r.ok ? <Button size="sm" variant="secondary" onClick={() => show(r.id)} aria-label={`Open the comparison for ${r.name}`}>Open</Button>
                  : <StatusChip tone="danger">Not read</StatusChip>}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <ApplyAll x={x} />
      <Card>
        <h3 className="vbr2-h">Comparisons</h3>
        {(x.checks || []).length ? (
          <ul className="vbr2-list">
            {x.checks.map((c) => (
              <li key={c.id} className="vbr2-row vbr2-wrap">
                <span className="vbr2-text"><b>{c.filename}</b> · {c.account || "—"} · {c.period_from} to {c.period_to}</span>
                <StatusChip tone={c.status === "proposed" ? "warn" : "ok"}>
                  {c.status === "applied" ? "Applied" : c.status === "discarded" ? "Checked again" : "To review"}</StatusChip>
                <Button size="sm" variant="secondary" onClick={() => show(c.id)}>Open</Button>
              </li>
            ))}
          </ul>
        ) : <p className="vbr2-text">No statement compared yet.</p>}
      </Card>
      {open && (
        <Card>
          <h3 className="vbr2-h">{open.filename}</h3>
          <p className="vbr2-sub">{(open.summary || {}).already_right || 0} lines already right. Tick what to change.</p>
          <ul className="vbr2-list">
            {(open.actions || []).map((a) => (
              <li key={a.id} className="vbr2-row vbr2-wrap">
                <input type="checkbox" aria-label={`${ACTION[a.kind] || a.kind}: ${a.reason || ""}`} checked={ticked.has(a.id)}
                  disabled={open.status === "applied" || a.kind === "review"} onChange={() => toggle(a.id)} />
                <span className="vbr2-text"><b>{ACTION[a.kind] || a.kind}</b>{a.line ? ` · ${a.line.date} · ${a.line.description} · ${money(a.line.amount_cents)}` : ""}
                  {a.cents != null && !a.line ? ` · ${money(a.cents)}` : ""}{a.reason ? ` — ${a.reason}` : ""}</span>
              </li>
            ))}
          </ul>
          {open.status !== "applied" && <Button onClick={apply} disabled={!ticked.size}>Apply the ticked ones</Button>}
        </Card>
      )}
    </div>
  );
}
