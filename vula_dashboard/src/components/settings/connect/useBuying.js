/**
 * useBuying.js — Settings › Buying (5 Nov 2026): whether Vula may buy for the business, its limits,
 * who may ask and who approves, and the shops it may buy from. GET/PUT /admin/buying and
 * POST /admin/buying/shops (vula/api/buying_api.py). Nothing here buys anything: purchases are
 * asked for on WhatsApp and approved there.
 */
import { useEffect, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog, toast } from '../../ui/index.jsx'

const rands = c => (c ? String(Math.round(c) / 100) : '')

export function useBuying(tenantId) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/buying`
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  const [form, setForm] = useState(null)
  const [suppliers, setSuppliers] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  function take(d) {
    setData(d)
    const s = d.settings || {}
    setForm({
      enabled: !!s.enabled, per_purchase_limit: rands(s.per_purchase_limit_cents),
      monthly_limit: rands(s.monthly_limit_cents), who_can_ask: s.who_can_ask || 'managers',
      approver_phones: (s.approver_phones || []).join(', '), default_project: s.default_project || '',
    })
  }

  async function load() {
    try {
      const r = await fetch(base)
      if (!r.ok) throw new Error()
      take(await r.json()); setFailed(false)
    } catch { setFailed(true) }
  }

  useEffect(() => {
    if (!tenantId) return
    load()
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/suppliers`).then(r => (r.ok ? r.json() : {}))
      .then(d => setSuppliers(d.suppliers || [])).catch(() => {})
  }, [tenantId])   // eslint-disable-line react-hooks/exhaustive-deps

  async function send(url, method, body, done) {
    setBusy(true); setError(null)
    try {
      const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'That couldn’t be saved')
      take(d)
      if (done) toast(done)
      return true
    } catch (err) {
      setError(err.message)
      return false
    } finally {
      setBusy(false)
    }
  }

  function save(e) {
    e?.preventDefault()
    const phones = form.approver_phones.split(/[,;\n]/).map(p => p.trim()).filter(Boolean)
    return send(base, 'PUT', {
      enabled: form.enabled,
      per_purchase_limit: form.per_purchase_limit === '' ? 0 : Number(form.per_purchase_limit),
      monthly_limit: form.monthly_limit === '' ? 0 : Number(form.monthly_limit),
      who_can_ask: form.who_can_ask, approver_phones: phones, default_project: form.default_project,
    }, 'Saved')
  }

  const addShop = shop => send(`${base}/shops`, 'POST', shop, `${shop.name} added`)

  async function removeShop(s) {
    if (!(await confirmDialog(`Take ${s.name} off the list? Vula won’t search or buy there any more.`))) return
    send(`${base}/shops/${s.id}/remove`, 'POST', null, 'Removed')
  }

  const set = patch => setForm(f => ({ ...f, ...patch }))
  return { data, failed, form, set, save, addShop, removeShop, suppliers, busy, error, reload: load }
}
