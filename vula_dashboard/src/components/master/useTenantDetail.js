/**
 * useTenantDetail.js — Master › a tenant's drill-in: overview (+ manage), link a WhatsApp number on
 * Vula's account, push the WhatsApp catalogue, usage, audit, open escalations, conversations and
 * errors (2026-10-19, UX redesign Phase F). One hook per tab, moved out of
 * VulaMasterTenantDetail unchanged — same /v1/master/… and tenant-scoped calls.
 */
import { useEffect, useState } from 'react'
import { authFetch, VULA_API } from '../../lib/authFetch'

export function useTenantOverview({ tenantId, onError }) {
  const [tenant, setTenant] = useState(null)
  const [registry, setRegistry] = useState({ business_types: [], modules: [] })

  const load = async () => {
    try {
      const [t, r] = await Promise.all([
        authFetch('/v1/master/tenants'),
        authFetch('/v1/tenants/registry'),
      ])
      setTenant((t.tenants || []).find((x) => x.tenant_id === tenantId) || null)
      setRegistry(r)
    } catch (e) { onError(e.message) }
  }
  useEffect(() => { load() }, [tenantId])  // eslint-disable-line

  const save = async (patch) => {
    try {
      await authFetch(`/v1/master/tenants/${tenantId}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      load()
    } catch (e) { onError(e.message) }
  }

  return { tenant, registry, save }
}

export function useLinkWhatsAppNumber({ tenantId }) {
  const [pnid, setPnid] = useState('')
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [msg, setMsg] = useState('')
  const link = async () => {
    setBusy(true); setMsg(''); setRes(null)
    try {
      const d = await authFetch(`/v1/master/tenants/${tenantId}/whatsapp-number`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number_id: pnid.trim(), pin: pin.trim() || undefined }),
      })
      if (d.detail) setMsg(d.detail)
      else { setRes(d); setPin('') }
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  return { pnid, setPnid, pin, setPin, busy, res, msg, link }
}

export function useWhatsAppCatalogue({ tenantId }) {
  const [cid, setCid] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [msg, setMsg] = useState('')
  const push = async () => {
    setBusy(true); setMsg(''); setRes(null)
    try {
      const d = await authFetch(`/v1/master/tenants/${tenantId}/meta-catalog`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ catalog_id: cid.trim() || undefined }),
      })
      if (d.detail) setMsg(d.detail)
      else if (d.status !== 'success') setMsg(d.error || d.message || 'The catalogue was not updated.')
      else setRes(d)
    } catch (e) { setMsg(e.message) }
    setBusy(false)
  }
  return { cid, setCid, busy, res, msg, push }
}

export function useTenantUsage({ tenantId, onError }) {
  const [u, setU] = useState(null)
  useEffect(() => { authFetch('/v1/master/usage?days=14').then(setU).catch((e) => onError(e.message)) }, [tenantId])  // eslint-disable-line
  return { u }
}

export function useTenantAudit({ tenantId, onError }) {
  const [events, setEvents] = useState(null)
  useEffect(() => {
    authFetch(`/v1/master/audit?tenant_id=${tenantId}&limit=50`).then((d) => setEvents(d.events || [])).catch((e) => onError(e.message))
  }, [tenantId])  // eslint-disable-line
  return { events }
}

export function useTenantEscalations({ tenantId, onError }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations?status=open&limit=20`)
      .then((r) => r.json()).then((d) => setRows(d.escalations || [])).catch((e) => onError(e.message))
  }, [tenantId])  // eslint-disable-line
  return { rows }
}

export function useTenantConversations({ tenantId, onError }) {
  const [threads, setThreads] = useState(null)
  const [openPhone, setOpenPhone] = useState(null)
  const [messages, setMessages] = useState(null)

  useEffect(() => {
    setOpenPhone(null); setMessages(null)
    authFetch(`/v1/master/tenants/${tenantId}/conversations`)
      .then((d) => setThreads(d.threads || [])).catch((e) => onError(e.message))
  }, [tenantId])  // eslint-disable-line

  const openThread = (phone) => {
    setOpenPhone(phone); setMessages(null)
    authFetch(`/v1/master/tenants/${tenantId}/conversations/${encodeURIComponent(phone)}`)
      .then((d) => setMessages(d.messages || [])).catch((e) => onError(e.message))
  }

  return { threads, openPhone, setOpenPhone, messages, openThread }
}

export function useTenantErrors({ tenantId, onError }) {
  const [d, setD] = useState(null)
  useEffect(() => {
    authFetch(`/v1/master/tenants/${tenantId}/errors`).then(setD).catch((e) => onError(e.message))
  }, [tenantId])  // eslint-disable-line
  return { d }
}
