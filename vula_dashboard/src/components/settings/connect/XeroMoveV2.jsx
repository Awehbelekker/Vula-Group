/**
 * XeroMoveV2.jsx — Settings › Move from Xero (5 Nov 2026, step 1). Connect the business's Xero
 * (read-only), pick the cut-over date, and see what moving would bring in: the opening balances
 * from Xero's trial balance, invoices still owed to the business and bills it still owes (with
 * whether they agree with Xero's own debtors / creditors), customers and suppliers matched to the
 * ones Vula already has, and history. Nothing is written to the books in this step.
 * Data: connect/useXeroMove.js → vula/api/xero.py.
 */
import { AlertTriangle, Check, LogOut, RefreshCw } from 'lucide-react'
import { Button, Card, ErrorCard, Field, Input, Skeleton, StatusChip } from '../../ui/index.jsx'
import { useXeroMove } from './useXeroMove'
import '../settings.css'

const money = c => (c == null ? '—' : `${c < 0 ? '−' : ''}R${(Math.abs(c) / 100).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)

export default function XeroMoveV2({ tenantId }) {
  const x = useXeroMove(tenantId)
  if (x.failed) return <ErrorCard what="Move from Xero" onRetry={x.reload} />
  if (!x.info) return <Card><Skeleton lines={3} /></Card>
  const conn = x.info.connection
  const connected = conn?.status === 'connected'
  return (
    <>
      <Card className="vst-card">
        <p className="vst-body">
          Bring your books across from Xero and run them in Vula. First Vula reads Xero (it can’t change anything there) and
          shows you what it would bring in at your cut-over date — the balance of every account, what customers still owe you
          and what you still owe suppliers. Nothing is added to your books until you’ve checked it.
        </p>
        {!x.info.one_tap && !connected && (
          <div className="vcn-warn-box" role="status"><AlertTriangle size={16} aria-hidden="true" /> Xero can’t be connected with one tap yet — Vula’s team has been told.</div>
        )}
        {connected ? (
          <div className="vst-row">
            <span className="vst-strong">{conn.org_name || 'Xero'}</span>
            <StatusChip tone="ok"><Check size={14} aria-hidden="true" />Connected (read-only)</StatusChip>
            <Button size="sm" variant="quiet" Icon={LogOut} onClick={x.disconnect}>Disconnect</Button>
          </div>
        ) : (
          <div className="vst-row">
            {conn?.status === 'needs_reconnect' && <StatusChip tone="warn">Needs connecting again</StatusChip>}
            <Button onClick={x.connect} disabled={!x.info.one_tap}>Connect Xero</Button>
          </div>
        )}
        {connected && (
          <form className="vst-card" onSubmit={x.preview}>
            <Field label="Cut-over date" hint="The last day Xero keeps the books — usually a month-end, or the end of your financial year. Vula takes over the next day.">
              <Input type="date" required value={x.cutover} onChange={e => x.setCutover(e.target.value)} />
            </Field>
            <div className="vst-row"><Button type="submit" Icon={RefreshCw} disabled={x.busy || x.run?.status === 'reading'}>
              {x.run?.status === 'reading' ? 'Reading Xero…' : 'Show me what would move'}</Button></div>
          </form>
        )}
        {x.error && <p className="vcn-error" role="alert">{x.error}</p>}
      </Card>
      {x.run && <Run run={x.run} />}
    </>
  )
}

function Agrees({ ok, mine, theirs, label }) {
  return (
    <span className="vst-row">
      <span>{money(mine)}</span>
      {ok ? <StatusChip tone="ok"><Check size={14} aria-hidden="true" />Agrees with Xero’s {label}</StatusChip>
        : <StatusChip tone="warn">Xero’s {label}: {money(theirs)}</StatusChip>}
    </span>
  )
}

function Run({ run }) {
  if (run.status === 'reading') return <Card className="vst-card"><p className="vst-body">Reading Xero… a busy business can take a few minutes. You can leave this page.</p><Skeleton lines={3} /></Card>
  if (run.status === 'failed') return <Card className="vst-card"><p className="vcn-error" role="alert">{run.error || 'Reading Xero didn’t finish.'}</p></Card>
  const p = run.preview || {}
  if (!p.cutover) return null
  const c = p.counts || {}
  return (
    <Card className="vst-card">
      <span className="vst-strong">What would move from {p.organisation || 'Xero'} at {p.cutover}</span>
      <p className="vst-sub">Nothing has been added to your books yet. Bringing it in is the next step, once this looks right.</p>
      {(p.warnings || []).map(w => <div key={w} className="vcn-warn-box" role="status"><AlertTriangle size={16} aria-hidden="true" /> {w}</div>)}
      <ul className="vcn-list">
        <li className="vcn-item"><div className="vcn-item-main"><span className="vst-strong">Opening balances</span>
          <span className="vst-sub">{p.trial_balance?.balanced ? 'Xero’s trial balance balances' : 'Xero’s trial balance doesn’t balance'} — debits {money(p.trial_balance?.debits_cents)}, credits {money(p.trial_balance?.credits_cents)}</span></div></li>
        <li className="vcn-item"><div className="vcn-item-main"><span className="vst-strong">Customers still owe you ({p.receivables?.count})</span>
          <Agrees ok={p.receivables?.agrees} mine={p.receivables?.total_cents} theirs={p.receivables?.xero_debtors_cents} label="debtors" /></div></li>
        <li className="vcn-item"><div className="vcn-item-main"><span className="vst-strong">You still owe suppliers ({p.payables?.count})</span>
          <Agrees ok={p.payables?.agrees} mine={p.payables?.total_cents} theirs={p.payables?.xero_creditors_cents} label="creditors" /></div></li>
        <li className="vcn-item"><div className="vcn-item-main"><span className="vst-strong">Customers and suppliers</span>
          <span className="vst-sub">Customers: {p.customers?.same} already in Vula, {p.customers?.new} new{p.customers?.probably ? `, ${p.customers.probably} to check` : ''}.
            Suppliers: {p.suppliers?.same} already in Vula, {p.suppliers?.new} new{p.suppliers?.probably ? `, ${p.suppliers.probably} to check` : ''}.</span></div></li>
        <li className="vcn-item"><div className="vcn-item-main"><span className="vst-strong">History (kept for questions, not counted again)</span>
          <span className="vst-sub">{p.history?.documents} paid invoices and bills, {p.history?.bank_transactions} bank transactions, {p.history?.manual_journals} journals · {c.items} items</span></div></li>
      </ul>
      <details><summary className="vst-strong">Opening balance per account</summary>
        <ul className="vcn-list">{(p.opening_balances || []).filter(r => r.balance_cents).map(r => (
          <li key={r.vula_code} className="vcn-item"><div className="vcn-item-main">
            <span className="vst-row"><span>{r.vula_name}</span>{r.new && <StatusChip tone="muted">new account</StatusChip>}<span>{money(r.balance_cents)}</span></span>
            <span className="vst-sub">From Xero: {(r.from || []).join(', ')}</span></div></li>))}</ul>
      </details>
      {(p.suppliers?.check?.length > 0 || p.customers?.check?.length > 0) && (
        <details><summary className="vst-strong">Names to check (looks like one you already have)</summary>
          <ul className="vcn-list">{[...(p.suppliers?.check || []), ...(p.customers?.check || [])].map(m => (
            <li key={m.name} className="vcn-item"><span className="vst-sub">Xero “{m.name}” → Vula “{m.vula_name}” ({m.by})</span></li>))}</ul>
        </details>
      )}
    </Card>
  )
}
