/**
 * VulaBudget.jsx — Expense tracking + budget overview for a tenant.
 *
 * - Add expenses manually (or via Smart Scanner)
 * - See revenue (from orders) vs expenses vs profit
 * - Filter by month, export to CSV
 */

import { confirmDialog } from './ui/index.jsx'
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../lib/authFetch'


const CATEGORIES = ['stock', 'delivery', 'packaging', 'marketing', 'equipment', 'staff', 'rent', 'utilities', 'other']

const CAT_COLORS = {
  stock: '#2DAAB5', delivery: '#8b5cf6', packaging: 'var(--warn)', marketing: 'var(--danger)',
  equipment: '#0ea5e9', staff: 'var(--ok)', rent: '#6366f1', utilities: '#ec4899', other: 'var(--muted)',
}

export default function VulaBudget({ tenantId, stats }) {
  const [expenses, setExpenses] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7))
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ category: 'stock', description: '', amount: '', supplier: '', date: new Date().toISOString().slice(0, 10), due_date: '' })
  const [due, setDue] = useState(null) // { overdue, upcoming, *_total_cents }
  const [recurring, setRecurring] = useState([])
  const [showAddRecurring, setShowAddRecurring] = useState(false)
  const [rForm, setRForm] = useState({ description: '', supplier: '', category: 'rent', amount: '', cadence: 'monthly', next_due: new Date().toISOString().slice(0, 10) })

  const load = useCallback(async () => {
    setLoading(true)
    // /admin/expenses now serves the expense-claims workflow (migration 060): it filters by
    // since/until, not month, and doesn't return a total_cents summary — so filter and total
    // here instead of trusting the server for those two things.
    const [y, m] = month.split('-').map(Number)
    const since = `${month}-01`
    const until = new Date(y, m, 0).toISOString().slice(0, 10)  // last day of the selected month
    const [exp, dueResp] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expenses?since=${since}&until=${until}`).then(r => r.json()),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expenses/due?days_ahead=30`).then(r => r.json()).catch(() => null),
    ])
    const rows = exp.expenses || []
    setExpenses(rows)
    setTotal(rows.reduce((sum, e) => sum + (e.amount_cents || 0), 0))
    setDue(dueResp)
    setLoading(false)
  }, [tenantId, month])

  useEffect(() => { load() }, [load])

  const loadRecurring = useCallback(async () => {
    const r = await fetch(`${VULA_API}/v1/recurring-bills/${tenantId}?status=active`).then(r => r.json()).catch(() => ({}))
    setRecurring(r.bills || [])
  }, [tenantId])

  useEffect(() => { loadRecurring() }, [loadRecurring])

  async function addRecurring(e) {
    e.preventDefault()
    const cents = Math.round(parseFloat(rForm.amount) * 100)
    if (isNaN(cents) || cents <= 0 || !rForm.description) return
    await fetch(`${VULA_API}/v1/recurring-bills/${tenantId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        description: rForm.description, supplier: rForm.supplier || null, category: rForm.category,
        amount_cents: cents, cadence: rForm.cadence, next_due: rForm.next_due,
      }),
    })
    setRForm({ description: '', supplier: '', category: 'rent', amount: '', cadence: 'monthly', next_due: new Date().toISOString().slice(0, 10) })
    setShowAddRecurring(false)
    loadRecurring()
  }

  async function cancelRecurring(id) {
    if (!(await confirmDialog('Stop this recurring bill? Past expenses it already created stay on the books.', { danger: true, confirmLabel: 'Yes' }))) return
    await fetch(`${VULA_API}/v1/recurring-bills/${tenantId}/${id}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'cancelled' }),
    })
    loadRecurring()
  }

  async function markPaid(id) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expenses/${id}/pay`, { method: 'PATCH' })
    load()
  }

  async function addExpense(e) {
    e.preventDefault()
    const cents = Math.round(parseFloat(form.amount) * 100)
    if (isNaN(cents) || cents <= 0) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expenses`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: form.date, category: form.category,
        description: form.description, amount_cents: cents, supplier: form.supplier,
        due_date: form.due_date || null,
      }),
    })
    setForm({ category: 'stock', description: '', amount: '', supplier: '', date: new Date().toISOString().slice(0, 10), due_date: '' })
    setShowAdd(false)
    load()
  }

  async function deleteExpense(id) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/expenses/${id}`, { method: 'DELETE' })
    load()
  }

  function exportCSV() {
    const rows = [['Date', 'Category', 'Description', 'Supplier', 'Amount (R)']]
    expenses.forEach(e => rows.push([e.date, e.category, e.description, e.supplier || '', (e.amount_cents / 100).toFixed(2)]))
    const csv = rows.map(r => r.map(c => `"${c}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `expenses-${month}.csv`; a.click()
  }

  const fmt = c => `R${(c / 100).toFixed(2)}`
  const revenue = stats?.today_revenue_cents != null ? stats.total_revenue_cents : 0
  const profit = revenue - total

  // Category breakdown
  const byCat = {}
  expenses.forEach(e => { byCat[e.category] = (byCat[e.category] || 0) + e.amount_cents })

  return (
    <div>
      {/* Budget summary */}
      <div style={s.summary}>
        <div style={s.sumCard}>
          <p style={{ ...s.sumValue, color: '#2DAAB5' }}>{fmt(revenue)}</p>
          <p style={s.sumLabel}>Total revenue</p>
        </div>
        <div style={s.sumCard}>
          <p style={{ ...s.sumValue, color: 'var(--danger)' }}>{fmt(total)}</p>
          <p style={s.sumLabel}>Expenses ({month})</p>
        </div>
        <div style={s.sumCard}>
          <p style={{ ...s.sumValue, color: profit >= 0 ? 'var(--ok)' : 'var(--danger)' }}>{fmt(profit)}</p>
          <p style={s.sumLabel}>Gross profit</p>
        </div>
      </div>

      {/* Payments due — overdue + upcoming supplier payments */}
      {due && (due.overdue?.length > 0 || due.upcoming?.length > 0) && (
        <div style={s.duePanel}>
          <div style={s.dueHead}>
            <span style={s.dueTitle}>💸 Payments due</span>
            <span style={s.dueTotal}>{fmt(due.grand_total_cents)}</span>
          </div>
          {[...(due.overdue || []).map(e => ({ ...e, _over: true })), ...(due.upcoming || [])].map(e => (
            <div key={e.id} style={s.dueRow}>
              <div style={{ flex: 1 }}>
                <span style={s.dueDesc}>{e.description || e.supplier || 'Expense'}</span>
                <span style={{ ...s.dueMeta, color: e._over ? 'var(--danger)' : 'var(--muted)' }}>
                  {e._over ? '⚠ overdue' : 'due'} {e.due_date}{e.supplier ? ` · ${e.supplier}` : ''}
                </span>
              </div>
              <span style={s.dueAmt}>{fmt(e.amount_cents)}</span>
              <button onClick={() => markPaid(e.id)} style={s.payBtn}>Mark paid</button>
            </div>
          ))}
        </div>
      )}

      {/* Recurring bills — rent, utilities, subscriptions, supplier accounts */}
      <div style={s.duePanel}>
        <div style={s.dueHead}>
          <span style={s.dueTitle}>🔁 Recurring bills</span>
          <button onClick={() => setShowAddRecurring(!showAddRecurring)} style={s.smallAddBtn}>+ Add</button>
        </div>
        <p style={{ ...s.dueMeta, color: 'var(--muted)', margin: '0 0 8px' }}>
          Rent, utilities, software — Vula creates the expense automatically ahead of each due date.
        </p>
        {showAddRecurring && (
          <form onSubmit={addRecurring} style={{ ...s.addForm, marginBottom: 10 }}>
            <input placeholder="Description (e.g. Rent)" value={rForm.description}
              onChange={e => setRForm({ ...rForm, description: e.target.value })} style={s.input} required />
            <div style={s.formRow}>
              <select value={rForm.category} onChange={e => setRForm({ ...rForm, category: e.target.value })} style={s.select}>
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <select value={rForm.cadence} onChange={e => setRForm({ ...rForm, cadence: e.target.value })} style={s.select}>
                <option value="monthly">Monthly</option>
                <option value="biweekly">Every 2 weeks</option>
                <option value="weekly">Weekly</option>
              </select>
            </div>
            <div style={s.formRow}>
              <input placeholder="Supplier (optional)" value={rForm.supplier}
                onChange={e => setRForm({ ...rForm, supplier: e.target.value })} style={s.input} />
              <input placeholder="Amount (R)" type="number" step="0.01" value={rForm.amount}
                onChange={e => setRForm({ ...rForm, amount: e.target.value })} style={s.input} required />
            </div>
            <div style={s.formRow}>
              <label style={{ ...s.dueMeta, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
                Next due
                <input type="date" value={rForm.next_due}
                  onChange={e => setRForm({ ...rForm, next_due: e.target.value })} style={s.input} required />
              </label>
            </div>
            <button type="submit" style={s.saveBtn}>Save recurring bill</button>
          </form>
        )}
        {recurring.length === 0 ? (
          <p style={s.muted}>No recurring bills set up yet.</p>
        ) : recurring.map(b => (
          <div key={b.id} style={s.dueRow}>
            <div style={{ flex: 1 }}>
              <span style={s.dueDesc}>{b.description}</span>
              <span style={{ ...s.dueMeta, color: 'var(--muted)' }}>
                {b.cadence} · next {b.next_due}{b.supplier ? ` · ${b.supplier}` : ''}
              </span>
            </div>
            <span style={s.dueAmt}>{fmt(b.amount_cents)}</span>
            <button onClick={() => cancelRecurring(b.id)} style={s.delBtn}>×</button>
          </div>
        ))}
      </div>

      {/* Category bar */}
      {Object.keys(byCat).length > 0 && (
        <div style={s.catBar}>
          {Object.entries(byCat).map(([cat, amt]) => (
            <div key={cat} style={{ flex: amt, background: CAT_COLORS[cat] || 'var(--muted)', height: 8 }} title={`${cat}: ${fmt(amt)}`} />
          ))}
        </div>
      )}

      {/* Controls */}
      <div style={s.controls}>
        <input type="month" value={month} onChange={e => setMonth(e.target.value)} style={s.monthInput} />
        <button onClick={() => setShowAdd(!showAdd)} style={s.addBtn}>+ Add expense</button>
        <button onClick={exportCSV} style={s.exportBtn}>↓ CSV</button>
      </div>

      {/* Add form */}
      {showAdd && (
        <form onSubmit={addExpense} style={s.addForm}>
          <div style={s.formRow}>
            <select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} style={s.select}>
              {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <input type="date" value={form.date} onChange={e => setForm({ ...form, date: e.target.value })} style={s.input} />
          </div>
          <input placeholder="Description" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} style={s.input} required />
          <div style={s.formRow}>
            <input placeholder="Supplier (optional)" value={form.supplier} onChange={e => setForm({ ...form, supplier: e.target.value })} style={s.input} />
            <input placeholder="Amount (R)" type="number" step="0.01" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} style={s.input} required />
          </div>
          <label style={{ ...s.dueMeta, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 6 }}>
            Due date (optional — leave blank if already paid)
            <input type="date" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })} style={s.input} />
          </label>
          <button type="submit" style={s.saveBtn}>Save expense</button>
        </form>
      )}

      {/* Expense list */}
      {loading ? <p style={s.muted}>Loading…</p> : expenses.length === 0 ? (
        <div style={{ textAlign: 'center', maxWidth: 420, margin: '24px auto', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 32 }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>💰</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 700, color: 'var(--ink)', marginBottom: 6 }}>Start tracking spend</div>
          <p style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55, margin: '0 0 16px' }}>
            Log your first expense to see budget-vs-actual by category. Fastest way: snap a receipt
            with the <b>Smart Scanner</b> and Vula fills it in for you.
          </p>
          <button onClick={() => setShowAdd(true)} style={{ ...s.saveBtn, width: 'auto', padding: '9px 18px' }}>+ Add your first expense</button>
        </div>
      ) : (
        <div style={s.list}>
          {expenses.map(e => (
            <div key={e.id} style={s.expRow}>
              <span style={{ ...s.catDot, background: CAT_COLORS[e.category] }} />
              <div style={{ flex: 1 }}>
                <span style={s.expDesc}>{e.description}</span>
                <span style={s.expMeta}>{e.date} · {e.category}{e.supplier ? ` · ${e.supplier}` : ''}</span>
              </div>
              <span style={s.expAmt}>{fmt(e.amount_cents)}</span>
              <button onClick={() => deleteExpense(e.id)} style={s.delBtn}>×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const s = {
  summary:    { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 12 },
  sumCard:    { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '14px 12px', textAlign: 'center' },
  sumValue:   { fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 700, margin: '0 0 2px' },
  sumLabel:   { fontSize: 11, color: 'var(--muted)', margin: 0 },
  catBar:     { display: 'flex', borderRadius: 4, overflow: 'hidden', marginBottom: 16, gap: 1 },
  duePanel:   { background: 'var(--surface)', border: '1px solid #F0D6A8', borderRadius: 10, padding: 14, marginBottom: 16 },
  dueHead:    { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  dueTitle:   { fontSize: 13, fontWeight: 700, color: 'var(--ink)' },
  dueTotal:   { fontSize: 15, fontWeight: 700, color: '#E8B86E' },
  dueRow:     { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderTop: '1px solid #F5F0E6' },
  dueDesc:    { display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--ink)' },
  dueMeta:    { display: 'block', fontSize: 11 },
  dueAmt:     { fontSize: 14, fontWeight: 700, color: 'var(--ink)' },
  payBtn:     { padding: '5px 10px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer'},
  smallAddBtn:{ padding: '5px 10px', background: 'transparent', color: 'var(--accent, var(--accent))', border: '1px solid var(--accent, var(--accent))', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer'},
  controls:   { display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' },
  monthInput: { padding: '7px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 },
  addBtn:     { padding: '7px 14px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, cursor: 'pointer', fontWeight: 600 },
  exportBtn:  { padding: '7px 12px', background: 'transparent', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, cursor: 'pointer', marginLeft: 'auto' },
  addForm:    { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: 14, marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 8 },
  formRow:    { display: 'flex', gap: 8 },
  select:     { flex: 1, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 },
  input:      { flex: 1, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13, boxSizing: 'border-box' },
  saveBtn:    { padding: '9px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer'},
  list:       { display: 'flex', flexDirection: 'column', gap: 6 },
  expRow:     { display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' },
  catDot:     { width: 10, height: 10, borderRadius: '50%', flexShrink: 0 },
  expDesc:    { display: 'block', fontSize: 13, fontWeight: 500, color: 'var(--ink)' },
  expMeta:    { display: 'block', fontSize: 11, color: 'var(--muted)' },
  expAmt:     { fontSize: 14, fontWeight: 700, color: 'var(--danger)' },
  delBtn:     { background: 'transparent', border: 'none', color: 'var(--faint)', fontSize: 18, cursor: 'pointer', lineHeight: 1 },
  muted:      { color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '24px 0' },
}
