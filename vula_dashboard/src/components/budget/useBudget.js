/**
 * useBudget.js — Money › Budget, shared by VulaBudget (current look) and budget/BudgetV2 (the
 * redesign, 2026-10-20). Moved verbatim: the month's expenses (filtered and totalled here), payments
 * due (overdue + next 30 days) with Mark paid, recurring bills (rent, utilities, software) that
 * create the expense ahead of each due date, adding / deleting an expense, and the CSV export.
 * Amounts typed in rands become integer cents here.
 */
import { useState, useEffect, useCallback } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { lastDayOfMonth, todaySA } from '../../lib/dates'
import { confirmDialog } from '../ui/index.jsx'

export const CATEGORIES = ['stock', 'delivery', 'packaging', 'marketing', 'equipment', 'staff', 'rent', 'utilities', 'other']

export function useBudget(tenantId) {
  const [expenses, setExpenses] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  // SA month, not UTC: just after midnight SAST on the 1st the UTC date is still last month (2026-10-20)
  const [month, setMonth] = useState(todaySA().slice(0, 7))
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ category: 'stock', description: '', amount: '', supplier: '', date: todaySA(), due_date: '' })
  const [due, setDue] = useState(null) // { overdue, upcoming, *_total_cents }
  const [recurring, setRecurring] = useState([])
  const [showAddRecurring, setShowAddRecurring] = useState(false)
  const [rForm, setRForm] = useState({ description: '', supplier: '', category: 'rent', amount: '', cadence: 'monthly', next_due: todaySA() })

  const load = useCallback(async () => {
    setLoading(true)
    // /admin/expenses now serves the expense-claims workflow (migration 060): it filters by
    // since/until, not month, and doesn't return a total_cents summary — so filter and total
    // here instead of trusting the server for those two things.
    const [y, m] = month.split('-').map(Number)
    const since = `${month}-01`
    const until = lastDayOfMonth(y, m)
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
    setRForm({ description: '', supplier: '', category: 'rent', amount: '', cadence: 'monthly', next_due: todaySA() })
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
    setForm({ category: 'stock', description: '', amount: '', supplier: '', date: todaySA(), due_date: '' })
    setShowAdd(false)
    load()
  }

  // confirm: none in the current look; the redesign asks first (BudgetV2)
  async function deleteExpense(id, confirm = async () => true) {
    if (!(await confirm('Delete this expense? It comes off the books for this month.'))) return
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

  return { expenses, total, loading, month, setMonth, showAdd, setShowAdd, form, setForm, due, recurring, showAddRecurring, setShowAddRecurring, rForm, setRForm, addRecurring, cancelRecurring, markPaid, addExpense, deleteExpense, exportCSV }
}
