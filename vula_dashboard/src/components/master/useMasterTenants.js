/**
 * useMasterTenants.js — Master › Tenants: every tenant with health, plan and billing, search and
 * sort, create, suspend / activate, the manage form, mark paid, +14 day trial, cancel and
 * reactivate (2026-10-19, UX redesign Phase F). Moved out of VulaMasterPanel's TenantsPanel and
 * ManageTenantRow unchanged — same /v1/master/tenants… and /v1/tenants calls.
 */
import { useEffect, useState } from 'react'
import { authFetch } from '../../lib/authFetch'
import { confirmDialog } from '../ui/index.jsx'

// Health from /v1/master/tenants (master._health): red = WhatsApp down, amber = unanswered
// questions / no number / quiet 30 days, green = fine, off = suspended.
export const HEALTH_ORDER = { red: 0, amber: 1, green: 2, off: 3 }
export const HEALTH_COLOUR = { red: 'var(--danger)', amber: 'var(--warn)', green: 'var(--ok)', off: 'var(--muted)' }

export function ago(iso) {
  if (!iso) return 'never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (Number.isNaN(days)) return '—'
  return days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 60 ? `${days} days ago` : `${Math.round(days / 30)} months ago`
}

export function matchesTenant(t, q) {
  const s = q.trim().toLowerCase()
  if (!s) return true
  return [t.tenant_id, t.display_name, t.business_type, t.signup_email, t.plan]
    .some(v => (v || '').toString().toLowerCase().includes(s))
}

export function sortTenants(list, by) {
  const name = (t) => (t.display_name || t.tenant_id || '').toLowerCase()
  const act = (t) => (t.last_activity ? new Date(t.last_activity).getTime() : 0)
  return [...list].sort((a, b) =>
    by === 'name' ? name(a).localeCompare(name(b))
      : by === 'activity' ? act(b) - act(a) || name(a).localeCompare(name(b))
        : (HEALTH_ORDER[a.health] ?? 2) - (HEALTH_ORDER[b.health] ?? 2) || act(b) - act(a))
}

export function useMasterTenants({ onError, prefill, onConsumePrefill, onCreated }) {
  const [rows, setRows] = useState([])
  const [registry, setRegistry] = useState({ business_types: [], modules: [] })
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ tenant_id: '', display_name: '', business_type: 'retail' })
  const [busy, setBusy] = useState(false)
  const [managing, setManaging] = useState(null)   // tenant_id being module/plan-edited
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('health')          // health | activity | name

  const load = async () => {
    try {
      const [t, r] = await Promise.all([
        authFetch('/v1/master/tenants'),
        authFetch('/v1/tenants/registry'),
      ])
      setRows(t.tenants || []); setRegistry(r)
    } catch (e) { onError(e.message) }
  }
  useEffect(() => { load() }, [])

  useEffect(() => {
    if (!prefill) return
    setForm({ tenant_id: prefill.tenant_id || '', display_name: prefill.display_name || '', business_type: prefill.business_type || 'retail' })
    setCreating(true)
    onConsumePrefill && onConsumePrefill()
  }, [prefill])  // eslint-disable-line

  const create = async () => {
    if (!form.tenant_id.trim()) return onError('tenant_id required (slug, e.g. my-shop)')
    setBusy(true)
    try {
      const r = await authFetch('/v1/tenants', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (r.error) onError(r.error)
      else {
        const newTenantId = form.tenant_id
        setCreating(false); setForm({ tenant_id: '', display_name: '', business_type: 'retail' }); load()
        onCreated && onCreated(newTenantId)
      }
    } catch (e) { onError(e.message) } finally { setBusy(false) }
  }

  const toggleActive = async (t) => {
    const name = t.display_name || t.tenant_id
    if (t.active !== false && !(await confirmDialog(
      `Suspend ${name}? Their WhatsApp bot, checkout and dashboard logins stop working until you activate them again.`,
      { danger: true, confirmLabel: 'Suspend' }))) return
    try {
      await authFetch(`/v1/master/tenants/${t.tenant_id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: t.active === false }),
      })
      load()
    } catch (e) { onError(e.message) }
  }

  const saveManage = async (tenant_id, patch) => {
    try {
      await authFetch(`/v1/master/tenants/${tenant_id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      load()
    } catch (e) { onError(e.message) }
  }

  const markPaid = async (t) => {
    try { await authFetch(`/v1/master/tenants/${t.tenant_id}/mark-paid`, { method: 'POST' }); load() }
    catch (e) { onError(e.message) }
  }
  const extendTrial = async (t) => {
    try {
      await authFetch(`/v1/master/tenants/${t.tenant_id}/extend-trial`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: 14 }),
      })
      load()
    } catch (e) { onError(e.message) }
  }
  const cancelSub = async (t) => {
    if (!(await confirmDialog(`Cancel ${t.display_name || t.tenant_id}'s subscription? This also suspends them — bot, checkout, and dashboard logins stop working immediately.`, { danger: true, confirmLabel: 'Yes' }))) return
    try { await authFetch(`/v1/master/tenants/${t.tenant_id}/cancel`, { method: 'POST' }); load() }
    catch (e) { onError(e.message) }
  }
  const reactivateSub = async (t) => {
    try { await authFetch(`/v1/master/tenants/${t.tenant_id}/reactivate`, { method: 'POST' }); load() }
    catch (e) { onError(e.message) }
  }

  return { rows, registry, creating, setCreating, form, setForm, busy, managing, setManaging, q, setQ, sort, setSort,
    load, create, toggleActive, saveManage, markPaid, extendTrial, cancelSub, reactivateSub }
}

/** The manage form's state for one tenant (plan, store URL, gateway, spend cap, modules, sharing,
 * the redesign switch); `patch()` is the PATCH body Save sends. */
export function useManageTenant(tenant, registry) {
  const [modules, setModules] = useState(tenant.modules || [])
  const [plan, setPlan] = useState(tenant.plan || 'starter')
  const [storeUrl, setStoreUrl] = useState(tenant.store_url || '')
  const [gateway, setGateway] = useState(tenant.default_payment_provider || '')
  const [shareNetwork, setShareNetwork] = useState(!!tenant.share_knowledge_with_network)
  const [spendCap, setSpendCap] = useState(tenant.spend_cap_usd != null ? String(tenant.spend_cap_usd) : '')
  // The redesigned dashboard (migrations 215 / 217): on for every business by default; off here
  // puts this one business back on the old design.
  const [uiV2, setUiV2] = useState(tenant.ui_v2 !== false)
  const allModules = registry.modules || []

  const toggle = (id) => setModules(m => m.includes(id) ? m.filter(x => x !== id) : [...m, id])

  const patch = () => ({
    modules, plan, store_url: storeUrl, default_payment_provider: gateway,
    share_knowledge_with_network: shareNetwork,
    spend_cap_usd: spendCap === '' ? null : Number(spendCap),
    ui_v2: uiV2,
  })
  return { modules, plan, setPlan, storeUrl, setStoreUrl, gateway, setGateway, shareNetwork, setShareNetwork,
    spendCap, setSpendCap, uiV2, setUiV2, allModules, toggle, patch }
}
