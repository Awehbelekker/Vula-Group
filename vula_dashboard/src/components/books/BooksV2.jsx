/**
 * BooksV2.jsx — the redesigned Accounts (2026-10-19, UX redesign Phase F). Same data and actions
 * as the current screen (books/useBooks.js): a date range, income / expenses / net profit, the
 * P&L by category, the VAT summary for SARS, and the ledger export for the accountant.
 */
import { CalendarDays, Download } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, Field, Input, PageHeader, StatCard } from "../ui/index.jsx";
import { useBooks } from "./useBooks";
import "./books.css";

const money = (c) => formatMoney(c || 0, { cents: "always" });

export default function BooksV2({ tenantId }) {
  const b = useBooks(tenantId);
  const { pnl, vat } = b;
  return (
    <div className="vbk2">
      <PageHeader sub="Your books, built from the reconciled bank statement. Export the ledger for your accountant to file."
        actions={<Button Icon={Download} onClick={b.exportLedger}>Export for accountant (CSV)</Button>} />
      <Card className="vbk2-range">
        <Field label="From"><Input type="date" value={b.since} onChange={(e) => b.setSince(e.target.value)} /></Field>
        <Field label="To"><Input type="date" value={b.until} onChange={(e) => b.setUntil(e.target.value)} /></Field>
        <p className="vbk2-sub"><CalendarDays size={14} aria-hidden="true" /> Leave both empty for everything so far.</p>
      </Card>

      {pnl && (
        <>
          <div className="vbk2-stats">
            <StatCard variant="tile" label="Income" value={money(pnl.total_income_cents)} color="var(--ok-text)" />
            <StatCard variant="tile" label="Expenses" value={money(pnl.total_expense_cents)} />
            <StatCard variant="tile" label="Net profit" value={money(pnl.net_profit_cents)}
              color={pnl.net_profit_cents >= 0 ? "var(--ok-text)" : "var(--danger-text)"} />
          </div>
          <Card pad={0} className="vbk2-card">
            <h3 className="vbk2-h">Profit and loss</h3>
            <Lines title="Income" rows={pnl.income} />
            <Lines title="Expenses" rows={pnl.expenses} />
            {(!pnl.income?.length && !pnl.expenses?.length) && <p className="vbk2-empty">Nothing categorised yet — reconcile a statement in Bank & matching first.</p>}
          </Card>
        </>
      )}

      <Card pad={0} className="vbk2-card">
        <h3 className="vbk2-h">VAT (SARS)</h3>
        {!vat ? <p className="vbk2-empty">—</p>
          : vat.vat_registered === false
            ? <p className="vbk2-empty">Not VAT-registered — no VAT return needed. Turn on VAT registration in Settings if this changes.</p>
            : (
              <dl className="vbk2-lines">
                <div className="vbk2-line"><dt>Output VAT (sales)</dt><dd className="vula-money">{money(vat.output_vat_cents)}</dd></div>
                <div className="vbk2-line"><dt>Input VAT (purchases)</dt><dd className="vula-money">{money(vat.input_vat_cents)}</dd></div>
                <div className="vbk2-line vbk2-line--total">
                  <dt>{vat.net_vat_cents >= 0 ? "VAT payable to SARS" : "VAT refund due"}</dt>
                  <dd className="vula-money">{money(Math.abs(vat.net_vat_cents))}</dd>
                </div>
              </dl>
            )}
        {vat?.note && <p className="vbk2-sub vbk2-pad">{vat.note}</p>}
      </Card>
    </div>
  );
}

function Lines({ title, rows }) {
  if (!rows?.length) return null;
  return (
    <div className="vbk2-group">
      <h4 className="vbk2-over">{title}</h4>
      <dl className="vbk2-lines">
        {rows.map((r, i) => (
          <div key={i} className="vbk2-line"><dt>{r.account}</dt><dd className="vula-money">{money(r.amount_cents)}</dd></div>
        ))}
      </dl>
    </div>
  );
}
