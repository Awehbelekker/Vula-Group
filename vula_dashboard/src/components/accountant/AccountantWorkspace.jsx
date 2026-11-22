/**
 * AccountantWorkspace.jsx — what an accountant's login sees (19 Nov 2026, Phase 3 of Xero-quality
 * books). Its own shell, not the business's menu: nothing here can change the books, and the server
 * refuses anything but the reads listed in vula_mind/vula/api/server.py (_ACCOUNTANT_READS) — so a
 * write button that slipped in still couldn't do anything. Every view, open and download is on the
 * business's record (vula_mind/vula/accountant_activity.py).
 *
 * Overview (the books check, statements on file), Reports (P&L, VAT, trial balance, debtors,
 * creditors; the general ledger as CSV), Bank (each account's lines), Invoices (sales and supplier,
 * with PDFs) and Documents (the year's money documents in folders, as a zip). An accountant with
 * several clients switches business at the top.
 */
import { useState } from "react";
import { BookOpenCheck, Download, FileText, Landmark, LogOut, ReceiptText } from "lucide-react";
import { useAuthStore } from "../../store/auth";
import { formatMoney } from "../../lib/money";
import {
  Button, Card, DataTable, EmptyState, ErrorCard, Field, Input, PillTabs, Select, Skeleton, StatusChip,
} from "../ui/index.jsx";
import FinanceFolders from "../documents/FinanceFolders";
import { useBankLines, useClients, useInvoiceList, useOverview, useReports } from "./useAccountant";
import "./accountant.css";

const money = (c) => formatMoney(c || 0, { cents: "always" });
const TABS = [
  { id: "overview", label: "Overview" }, { id: "reports", label: "Reports" }, { id: "bank", label: "Bank" },
  { id: "invoices", label: "Invoices" }, { id: "documents", label: "Documents" },
];
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
const monthName = (ym) => { const [y, m] = String(ym).split("-"); return `${MONTHS[Number(m) - 1] || m} ${y}`; };
const day = (v) => (v ? String(v).slice(0, 10) : "—");

export default function AccountantWorkspace({ tenantId }) {
  const { user, logout, switchTenant } = useAuthStore();
  const { clients, name } = useClients(tenantId);
  const [tab, setTab] = useState("overview");
  return (
    <div className="vac">
      <header className="vac-top">
        <div className="vac-who">
          <p className="vac-over">Accountant's view</p>
          <h1 className="vac-name">{name || tenantId}</h1>
        </div>
        <div className="vac-top-actions">
          {clients.length > 1 && (
            <label className="vac-switch"><span className="vac-sr">Business</span>
              <Select aria-label="Business" value={tenantId} onChange={(e) => switchTenant(e.target.value)}>
                {clients.map((c) => <option key={c.tenant_id} value={c.tenant_id}>{c.name}</option>)}
              </Select>
            </label>)}
          <Button variant="secondary" Icon={LogOut} onClick={logout}>Sign out</Button>
        </div>
      </header>
      <main className="vac-main">
        <p className="vac-note" role="note">View only — you can look at and download the books; nothing here changes them.
          {" "}The business can see what you open and download{user?.email ? ` (signed in as ${user.email})` : ""}.</p>
        <PillTabs label="Accountant's view" tabs={TABS} active={tab} onChange={setTab} />
        {tab === "overview" && <Overview tenantId={tenantId} onTab={setTab} />}
        {tab === "reports" && <Reports tenantId={tenantId} />}
        {tab === "bank" && <Bank tenantId={tenantId} />}
        {tab === "invoices" && <Invoices tenantId={tenantId} />}
        {tab === "documents" && <FinanceFolders tenantId={tenantId} accountant />}
      </main>
    </div>
  );
}

function Off() {
  return (
    <EmptyState Icon={BookOpenCheck} title="This business hasn't given you access right now">
      Ask the business to check your access under Team › Accountant.
    </EmptyState>
  );
}

function Overview({ tenantId, onTab }) {
  const { audit, cover, state } = useOverview(tenantId);
  if (state === "loading") return <Card><Skeleton lines={4} /></Card>;
  if (state === "off") return <Off />;
  const signed = audit?.signed_off_by, agreed = audit?.agreed_by;
  return (
    <div className="vac-stack">
      <Card className="vac-card">
        <h2 className="vac-h"><BookOpenCheck size={18} aria-hidden="true" />The books check</h2>
        {!audit
          ? <p className="vac-sub">The business hasn't run Vula's books check yet.</p>
          : (
            <ul className="vac-list">
              <li><span>Last run</span><span>{day(audit.created_at)}</span></li>
              <li><span>Agreed by the business</span>{agreed ? <StatusChip tone="ok">{day(audit.agreed_at)}</StatusChip> : <StatusChip tone="warn">Not yet</StatusChip>}</li>
              <li><span>Signed off by Vula</span>{signed ? <StatusChip tone="ok">Yes</StatusChip> : <StatusChip tone="warn">Not yet</StatusChip>}</li>
              {audit.agreed_note && <li className="vac-wide"><span>The business's note</span><span>{audit.agreed_note}</span></li>}
            </ul>)}
      </Card>
      <Card className="vac-card">
        <h2 className="vac-h"><Landmark size={18} aria-hidden="true" />Bank statements on file</h2>
        {!(cover?.accounts || []).length
          ? <p className="vac-sub">No bank accounts yet.</p>
          : (
            <ul className="vac-list">
              {cover.accounts.map((a) => (
                <li key={a.id}><span className="vac-strong">{a.account}</span>
                  {a.missing.length ? <span className="vac-missing">Missing {a.missing.map(monthName).join(", ")}</span>
                    : <StatusChip tone="ok">Every month on file</StatusChip>}</li>))}
            </ul>)}
        {cover?.lines_on_no_account > 0 && <p className="vac-sub">{cover.lines_on_no_account} bank lines don't say which account they're from yet.</p>}
        <div className="vac-row"><Button variant="secondary" onClick={() => onTab("documents")}>See the documents</Button></div>
      </Card>
    </div>
  );
}

function Reports({ tenantId }) {
  const r = useReports(tenantId);
  const journal = r.pnl?.source === "journal";
  return (
    <div className="vac-stack">
      <Card className="vac-range">
        <Field label="From"><Input type="date" value={r.since} onChange={(e) => r.setSince(e.target.value)} /></Field>
        <Field label="To"><Input type="date" value={r.until} onChange={(e) => r.setUntil(e.target.value)} /></Field>
        {journal && <PillTabs label="View" tabs={[{ id: "accrual", label: "Accrual" }, { id: "cash", label: "Cash" }]}
          active={r.basis} onChange={r.setBasis} />}
        <Button Icon={Download} onClick={r.ledgerCsv}>General ledger (CSV)</Button>
        <Button Icon={Download} variant="secondary" onClick={r.statementsPdf}>Financial statements (PDF)</Button>
      </Card>
      {r.state === "loading" && <Card><Skeleton lines={6} /></Card>}
      {r.state === "off" && <Off />}
      {r.state === "failed" && <ErrorCard what="the reports" />}
      {r.state === "ready" && (
        <>
          <Card pad={0} className="vac-card">
            <h2 className="vac-h vac-pad">Profit and loss {journal ? `(${r.basis} view)` : "(from the bank statements)"}</h2>
            <Lines title="Income" rows={r.pnl.income} />
            <Lines title="Expenses" rows={r.pnl.expenses} />
            <dl className="vac-lines"><div className="vac-line vac-total"><dt>Net profit</dt><dd className="vula-money">{money(r.pnl.net_profit_cents)}</dd></div></dl>
          </Card>
          <Card pad={0} className="vac-card">
            <h2 className="vac-h vac-pad">VAT</h2>
            {r.vat?.vat_registered === false
              ? <p className="vac-sub vac-pad">Not VAT registered.</p>
              : (
                <dl className="vac-lines">
                  <div className="vac-line"><dt>Output VAT</dt><dd className="vula-money">{money(r.vat?.output_vat_cents)}</dd></div>
                  <div className="vac-line"><dt>Input VAT</dt><dd className="vula-money">{money(r.vat?.input_vat_cents)}</dd></div>
                  <div className="vac-line vac-total"><dt>Net</dt><dd className="vula-money">{money(r.vat?.net_vat_cents)}</dd></div>
                </dl>)}
            {r.vat?.note && <p className="vac-sub vac-pad">{r.vat.note}</p>}
          </Card>
          {r.tb && !r.tb.error && (
            <Card pad={0} className="vac-card">
              <h2 className="vac-h vac-pad">Trial balance</h2>
              <DataTable caption="Trial balance" rowKey="code" rows={r.tb.accounts || []} columns={[
                { key: "name", label: "Account" },
                { key: "debit_cents", label: "Debit", align: "right", render: (a) => (a.debit_cents ? money(a.debit_cents) : "—") },
                { key: "credit_cents", label: "Credit", align: "right", render: (a) => (a.credit_cents ? money(a.credit_cents) : "—") },
              ]} />
              <p className="vac-sub vac-pad">Totals {money(r.tb.total_debit_cents)} / {money(r.tb.total_credit_cents)}
                {r.tb.total_debit_cents !== r.tb.total_credit_cents && <> · <StatusChip tone="danger">Doesn't balance</StatusChip></>}</p>
            </Card>)}
          <div className="vac-two">
            <Aging title="Owed to the business" data={r.debtors} total={r.debtors ? r.debtors.outstanding_cents + r.debtors.overdue_cents : 0} />
            <Aging title="Owed to suppliers" data={r.creditors} total={r.creditors?.total_cents} />
          </div>
        </>)}
    </div>
  );
}

function Lines({ title, rows }) {
  if (!rows?.length) return null;
  return (
    <div className="vac-group">
      <h3 className="vac-over vac-pad">{title}</h3>
      <dl className="vac-lines">
        {rows.map((x, i) => <div key={i} className="vac-line"><dt>{x.account}</dt><dd className="vula-money">{money(x.amount_cents)}</dd></div>)}
      </dl>
    </div>
  );
}

function Aging({ title, data, total }) {
  if (!data) return null;
  const a = data.aging || {};
  return (
    <Card pad={0} className="vac-card">
      <h2 className="vac-h vac-pad">{title}</h2>
      <dl className="vac-lines">
        {[["Not yet due", a.current], ["1–30 days overdue", a.days_1_30], ["31–60 days overdue", a.days_31_60],
          ["61–90 days overdue", a.days_61_90], ["90+ days overdue", a.days_90_plus]].map(([k, v]) => (
          <div key={k} className="vac-line"><dt>{k}</dt><dd className="vula-money">{money(v)}</dd></div>))}
        <div className="vac-line vac-total"><dt>Total</dt><dd className="vula-money">{money(total)}</dd></div>
      </dl>
    </Card>
  );
}

function Bank({ tenantId }) {
  const b = useBankLines(tenantId);
  return (
    <div className="vac-stack">
      <Card className="vac-range">
        <Field label="Account">
          <Select value={b.account} onChange={(e) => b.setAccount(e.target.value)}>
            <option value="">Every account</option>
            {b.accounts.map((a) => <option key={a.id} value={a.id}>{[a.name || a.bank, a.account].filter(Boolean).join(" ")}</option>)}
          </Select>
        </Field>
        <Button Icon={Download} onClick={b.csv}>Download bank lines (CSV)</Button>
      </Card>
      {b.state === "off" && <Off />}
      {b.state === "failed" && <ErrorCard what="the bank lines" />}
      {b.state !== "off" && b.state !== "failed" && (
        <Card pad={0} className="vac-card">
          <DataTable caption="Bank lines" loading={b.state === "loading"} rows={b.lines} empty="No bank lines." columns={[
            { key: "txn_date", label: "Date", render: (l) => day(l.txn_date), sortable: true },
            { key: "description", label: "Description" },
            { key: "account_code", label: "Category", render: (l) => (l.match_status === "ignored" ? "Set aside" : l.account_code || "—") },
            { key: "amount_cents", label: "Amount", align: "right", sortable: true,
              render: (l) => <span className="vula-money">{l.direction === "out" ? "−" : ""}{money(l.amount_cents)}</span> },
          ]} />
          {b.more && <div className="vac-row vac-pad"><Button variant="secondary" onClick={b.loadMore}>Show more</Button></div>}
        </Card>)}
    </div>
  );
}

function Invoices({ tenantId }) {
  const x = useInvoiceList(tenantId);
  return (
    <div className="vac-stack">
      <PillTabs label="Invoices" tabs={[{ id: "outbound", label: "Sales invoices" }, { id: "inbound", label: "Supplier invoices" }]}
        active={x.direction} onChange={x.setDirection} />
      {x.state === "off" && <Off />}
      {x.state === "failed" && <ErrorCard what="the invoices" />}
      {x.state !== "off" && x.state !== "failed" && (
        <Card pad={0} className="vac-card">
          <DataTable caption={x.direction === "outbound" ? "Sales invoices" : "Supplier invoices"} loading={x.state === "loading"}
            rows={x.rows} empty="No invoices." columns={[
              { key: "issue_date", label: "Date", render: (i) => day(i.issue_date || i.created_at), sortable: true },
              { key: "invoice_number", label: "Number", render: (i) => i.invoice_number || "—" },
              { key: "customer_name", label: x.direction === "outbound" ? "Customer" : "Supplier",
                render: (i) => i.customer_name || i.supplier_name || i.supplier || "—" },
              { key: "status", label: "Status" },
              { key: "total_cents", label: "Total", align: "right", sortable: true, render: (i) => <span className="vula-money">{money(i.total_cents)}</span> },
              ...(x.direction === "outbound" ? [{ key: "pdf", label: "PDF", render: (i) => (
                <Button size="sm" variant="secondary" Icon={FileText} onClick={() => x.pdf(i)} aria-label={`Download ${i.invoice_number || "invoice"} as PDF`}>PDF</Button>) }] : []),
            ]} />
        </Card>)}
      {x.direction === "inbound" && (
        <p className="vac-sub"><ReceiptText size={14} aria-hidden="true" /> The supplier invoices themselves are in Documents › 03 Supplier invoices.</p>)}
    </div>
  );
}

