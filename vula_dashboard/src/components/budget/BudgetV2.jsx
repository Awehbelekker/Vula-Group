/**
 * BudgetV2.jsx — the redesigned Money › Budget (2026-10-20, UX redesign Phase F). Same calls as
 * VulaBudget (budget/useBudget.js): the month's expenses and spend by category, payments due with
 * Mark paid, recurring bills, adding an expense and the CSV export. Amounts are integer cents
 * through formatMoney; typed rands become cents in the hook. Deleting an expense or stopping a
 * recurring bill asks first. The current look's "Total revenue / Gross profit" tiles are left out:
 * this screen is never given the revenue they were computed from, so they always read R 0.
 */
import { CalendarClock, ScanLine, Check, Download, Plus, Repeat, Trash2, Wallet, X } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, DataTable, EmptyState, Field, IconButton, Input, Select, Sheet, Skeleton, StatCard, StatusChip, confirmDialog } from '../ui/index.jsx'
import { CATEGORIES, useBudget } from './useBudget'
import './budget.css'

const money = (c) => formatMoney(c, { cents: 'always' })
const cap = (s) => String(s || '').replace(/^./, (c) => c.toUpperCase())
const monthName = (ym) => {
  const [y, m] = String(ym).split('-').map(Number)
  return y && m ? new Date(Date.UTC(y, m - 1, 15)).toLocaleDateString('en-ZA', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : ym
}

export default function BudgetV2({ tenantId }) {
  const x = useBudget(tenantId)
  const byCat = {}
  x.expenses.forEach(e => { byCat[e.category] = (byCat[e.category] || 0) + e.amount_cents })
  const cats = Object.entries(byCat).sort((a, b) => b[1] - a[1])
  const top = Math.max(1, ...cats.map(([, v]) => v))
  const dueRows = x.due ? [...(x.due.overdue || []).map(e => ({ ...e, _over: true })), ...(x.due.upcoming || [])] : []

  return (
    <section className="vbg" aria-label="Budget">
      <div className="vbg-row vbg-bottom">
        <Field label="Month" style={{ width: 180 }}>
          <Input type="month" value={x.month} onChange={e => x.setMonth(e.target.value)} />
        </Field>
        <Button Icon={Plus} onClick={() => x.setShowAdd(true)}>Add expense</Button>
        <Button variant="secondary" Icon={Download} onClick={x.exportCSV} disabled={!x.expenses.length}>CSV</Button>
      </div>

      <div className="vbg-tiles">
        <StatCard variant="tile" label={`Spent in ${monthName(x.month)}`} value={money(x.total)} sub={`${x.expenses.length} expense${x.expenses.length === 1 ? '' : 's'}`} />
        {x.due && <StatCard variant="tile" label="Payments due" value={money(x.due.grand_total_cents || 0)}
          sub={dueRows.length ? `${(x.due.overdue || []).length} overdue · ${(x.due.upcoming || []).length} in 30 days` : 'Nothing due'} />}
        <StatCard variant="tile" label="Recurring bills" value={x.recurring.length} sub="Created ahead of each due date" />
      </div>

      {dueRows.length > 0 && (
        <Card className="vbg-card vbg-due" role="group" aria-labelledby="vbg-due">
          <h2 className="vbg-h" id="vbg-due"><CalendarClock size={18} aria-hidden="true" />Payments due</h2>
          <ul className="vbg-list">
            {dueRows.map(e => (
              <li key={e.id} className="vbg-item">
                <span className="vbg-grow"><b>{e.description || e.supplier || 'Expense'}</b>
                  <span className="vbg-muted">due {e.due_date}{e.supplier ? ` · ${e.supplier}` : ''}</span></span>
                {e._over && <StatusChip tone="danger">Overdue</StatusChip>}
                <span className="vula-money">{money(e.amount_cents)}</span>
                <Button size="sm" Icon={Check} aria-label={`Mark ${e.description || e.supplier || 'expense'} paid`} onClick={() => x.markPaid(e.id)}>Mark paid</Button>
              </li>))}
          </ul>
        </Card>)}

      {cats.length > 0 && (
        <Card className="vbg-card" role="group" aria-labelledby="vbg-cats">
          <h2 className="vbg-h" id="vbg-cats"><Wallet size={18} aria-hidden="true" />Spend by category</h2>
          <dl className="vbg-cats">
            {cats.map(([cat, amt]) => (
              <div key={cat}>
                <dt>{cap(cat)}</dt>
                <dd><span className="vbg-meter" aria-hidden="true"><span style={{ width: `${Math.round(100 * amt / top)}%` }} /></span>
                  <span className="vula-money">{money(amt)}</span></dd>
              </div>))}
          </dl>
        </Card>)}

      <Recurring x={x} />

      <section className="vbg" aria-labelledby="vbg-exp">
        <h2 className="vbg-h" id="vbg-exp">Expenses in {monthName(x.month)}</h2>
        {x.loading && !x.expenses.length ? <Card><Skeleton lines={4} /></Card>
          : x.expenses.length === 0
            ? <EmptyState Icon={Wallet} title="Start tracking spend"
                action={<Button Icon={Plus} onClick={() => x.setShowAdd(true)}>Add your first expense</Button>}>
                Log an expense to see spend by category. Fastest way: snap a receipt with the Smart Scanner and Vula fills it in for you.
              </EmptyState>
            : <DataTable caption="Expenses" rows={x.expenses}
                columns={[
                  { key: 'date', label: 'Date' },
                  { key: 'description', label: 'Expense', render: e => <span><b>{e.description}</b>{e.supplier ? <span className="vbg-muted"> · {e.supplier}</span> : null}</span> },
                  { key: 'category', label: 'Category', render: e => cap(e.category) },
                  { key: 'amount_cents', label: 'Amount', align: 'right', render: e => <span className="vula-money">{money(e.amount_cents)}</span> },
                  { key: 'del', label: '', align: 'right', render: e => <Del x={x} e={e} /> },
                ]}
                stack={{
                  title: e => e.description,
                  detail: e => `${e.date} · ${cap(e.category)}${e.supplier ? ` · ${e.supplier}` : ''}`,
                  trailing: e => <span className="vbg-trail"><span className="vula-money">{money(e.amount_cents)}</span><Del x={x} e={e} /></span>,
                }} />}
      </section>

      {x.showAdd && <AddExpense x={x} />}
    </section>
  )
}

const Del = ({ x, e }) => (
  <IconButton quiet Icon={Trash2} className="vbg-danger" label={`Delete ${e.description}`}
    onClick={() => x.deleteExpense(e.id, m => confirmDialog(m, { danger: true, confirmLabel: 'Delete' }))} />
)

function Recurring({ x }) {
  const f = x.rForm
  const set = (k) => (e) => x.setRForm({ ...f, [k]: e.target.value })
  return (
    <Card className="vbg-card" role="group" aria-labelledby="vbg-rec">
      <div className="vbg-row">
        <h2 className="vbg-h vbg-grow" id="vbg-rec"><Repeat size={18} aria-hidden="true" />Recurring bills</h2>
        <Button size="sm" variant="secondary" Icon={x.showAddRecurring ? X : Plus} aria-expanded={x.showAddRecurring}
          onClick={() => x.setShowAddRecurring(!x.showAddRecurring)}>{x.showAddRecurring ? 'Close' : 'Add'}</Button>
      </div>
      <p className="vbg-muted">Rent, utilities, software: Vula creates the expense automatically ahead of each due date.</p>
      {x.showAddRecurring && (
        <form className="vbg-grid" onSubmit={x.addRecurring}>
          <Field label="Description"><Input value={f.description} onChange={set('description')} placeholder="e.g. Rent" required /></Field>
          <Field label="Category"><Select value={f.category} onChange={set('category')}>{CATEGORIES.map(c => <option key={c} value={c}>{cap(c)}</option>)}</Select></Field>
          <Field label="How often">
            <Select value={f.cadence} onChange={set('cadence')}>
              <option value="monthly">Monthly</option><option value="biweekly">Every 2 weeks</option><option value="weekly">Weekly</option>
            </Select>
          </Field>
          <Field label="Supplier" hint="Optional"><Input value={f.supplier} onChange={set('supplier')} /></Field>
          <Field label="Amount (R)"><Input type="number" step="0.01" min="0" inputMode="decimal" value={f.amount} onChange={set('amount')} required /></Field>
          <Field label="Next due"><Input type="date" value={f.next_due} onChange={set('next_due')} required /></Field>
          <div className="vbg-end"><Button type="submit" Icon={Check}>Save recurring bill</Button></div>
        </form>)}
      {x.recurring.length === 0
        ? <p className="vbg-muted">No recurring bills set up yet.</p>
        : <ul className="vbg-list">
            {x.recurring.map(b => (
              <li key={b.id} className="vbg-item">
                <span className="vbg-grow"><b>{b.description}</b><span className="vbg-muted">{b.cadence} · next {b.next_due}{b.supplier ? ` · ${b.supplier}` : ''}</span></span>
                <span className="vula-money">{money(b.amount_cents)}</span>
                <IconButton quiet Icon={X} className="vbg-danger" label={`Stop the recurring bill ${b.description}`} onClick={() => x.cancelRecurring(b.id)} />
              </li>))}
          </ul>}
    </Card>
  )
}

function AddExpense({ x }) {
  const f = x.form
  const set = (k) => (e) => x.setForm({ ...f, [k]: e.target.value })
  return (
    <Sheet open title="Add an expense" onClose={() => x.setShowAdd(false)}
      footer={<div className="vbg-row">
        <Button type="submit" form="vbg-add" Icon={Check}>Save expense</Button>
        <Button variant="secondary" onClick={() => x.setShowAdd(false)}>Cancel</Button>
      </div>}>
      <form id="vbg-add" className="vbg-form" onSubmit={x.addExpense}>
        <Field label="Description"><Input value={f.description} onChange={set('description')} required /></Field>
        <div className="vbg-grid">
          <Field label="Category"><Select value={f.category} onChange={set('category')}>{CATEGORIES.map(c => <option key={c} value={c}>{cap(c)}</option>)}</Select></Field>
          <Field label="Date"><Input type="date" value={f.date} onChange={set('date')} /></Field>
          <Field label="Supplier" hint="Optional"><Input value={f.supplier} onChange={set('supplier')} /></Field>
          <Field label="Amount (R)"><Input type="number" step="0.01" min="0" inputMode="decimal" value={f.amount} onChange={set('amount')} required /></Field>
        </div>
        <Field label="Due date" hint="Optional. Leave blank if it's already paid."><Input type="date" value={f.due_date} onChange={set('due_date')} /></Field>
        <p className="vbg-muted"><ScanLine size={14} aria-hidden="true" /> Have the slip? Money › Scanner reads it and fills this in for you.</p>
      </form>
    </Sheet>
  )
}
