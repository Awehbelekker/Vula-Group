/**
 * useDiscounts.js — discount codes (migration 091): list, add/edit, delete, switch on/off
 * (2026-10-19, UX redesign Phase F). Shared by VulaMerchantAdmin's DiscountCodesTab and
 * discounts/DiscountsV2.jsx; moved out unchanged — same /admin/discount-codes calls and bodies
 * (a fixed amount typed in rand is sent as integer cents).
 */
import { useCallback, useEffect, useState } from 'react'
import { VULA_API } from '../../lib/authFetch'
import { confirmDialog } from '../ui/index.jsx'

export const BLANK_DISCOUNT = {
  code: '', type: 'percent', value: '', min_order_cents: '', starts_at: '', ends_at: '',
  usage_limit: '', active: true, first_order_only: false, per_customer_limit: '',
}

export function useDiscounts(tenantId) {
  const [codes, setCodes] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(null) // null | {} (new) | code row
  const [form, setForm] = useState(BLANK_DISCOUNT)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes`)
    const d = await r.json()
    setCodes(d.discount_codes || [])
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  function startEdit(c) {
    setForm({
      code: c.code || '', type: c.type || 'percent',
      value: c.value != null ? (c.type === 'fixed' ? (c.value / 100).toFixed(2) : String(c.value)) : '',
      min_order_cents: c.min_order_cents != null ? (c.min_order_cents / 100).toFixed(2) : '',
      starts_at: (c.starts_at || '').slice(0, 10), ends_at: (c.ends_at || '').slice(0, 10),
      usage_limit: c.usage_limit ?? '', active: c.active !== false,
      first_order_only: c.first_order_only || false, per_customer_limit: c.per_customer_limit ?? '',
    })
    setEditing(c)
    setError(null)
  }
  function startNew() { setForm(BLANK_DISCOUNT); setEditing({}); setError(null) }
  function cancel() { setEditing(null); setForm(BLANK_DISCOUNT); setError(null) }

  async function save(e) {
    e.preventDefault()
    if (!form.code.trim()) return
    setSaving(true)
    setError(null)
    const payload = {
      code: form.code.trim(),
      type: form.type,
      value: form.type === 'fixed'
        ? Math.round(parseFloat(form.value || 0) * 100)
        : (parseInt(form.value, 10) || 0),
      min_order_cents: form.min_order_cents !== '' ? Math.round(parseFloat(form.min_order_cents) * 100) : null,
      starts_at: form.starts_at ? `${form.starts_at}T00:00:00+02:00` : null,
      ends_at: form.ends_at ? `${form.ends_at}T23:59:59+02:00` : null,
      usage_limit: form.usage_limit !== '' ? parseInt(form.usage_limit, 10) : null,
      active: form.active,
      first_order_only: form.first_order_only,
      per_customer_limit: form.per_customer_limit !== '' ? parseInt(form.per_customer_limit, 10) : null,
    }
    const isEdit = editing && editing.id
    const r = await fetch(
      `${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes${isEdit ? `/${editing.id}` : ''}`,
      { method: isEdit ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }
    )
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Could not save that code.')
      setSaving(false)
      return
    }
    cancel()
    setSaving(false)
    await load()
  }

  async function remove(c) {
    if (!(await confirmDialog(`Delete code "${c.code}"?`, { danger: true, confirmLabel: 'Yes' }))) return
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes/${c.id}`, { method: 'DELETE' })
    await load()
    setSaving(false)
  }

  async function toggleActive(c) {
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/discount-codes/${c.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: !c.active }),
    })
    await load()
    setSaving(false)
  }

  return { codes, loading, saving, editing, form, setForm, error, startEdit, startNew, cancel, save, remove, toggleActive }
}

export function valueLabel(c) {
  if (c.type === 'percent') return `${c.value}% off`
  if (c.type === 'fixed') return `R${(c.value / 100).toFixed(2)} off`
  return 'Free shipping'
}
