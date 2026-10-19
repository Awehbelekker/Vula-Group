/**
 * useMasterTools.js — Master › Onboard (go-live checklist + unprovisioned signups), Knowledge
 * (promotion queue), Audit, Go-live test, Capability benchmark, Conversations (daily check,
 * owner-advisor preview, retitle documents) and Models (bake-off + 👎 feedback cases)
 * (2026-10-19, UX redesign Phase F). Moved out of VulaMasterPanel unchanged — same /v1/master/…
 * and /v1/admin/signups calls.
 */
import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../lib/authFetch'
import { todaySA } from '../../lib/dates'
import { confirmDialog } from '../ui/index.jsx'

export function useMasterOnboard({ onError, selectTenantId, onConsumeSelect }) {
  const [tenants, setTenants] = useState([])
  const [selected, setSelected] = useState('')
  const [setup, setSetup] = useState(null)
  const [signups, setSignups] = useState([])

  useEffect(() => {
    authFetch('/v1/master/tenants').then(d => {
      const list = d.tenants || []
      setTenants(list)
      if (list.length && !selected) setSelected(list[0].tenant_id)
    }).catch(e => onError(e.message))
    authFetch('/v1/admin/signups?limit=50').then(d => setSignups(d.signups || [])).catch(() => {})
  }, [])  // eslint-disable-line

  useEffect(() => {
    if (!selectTenantId) return
    setSelected(selectTenantId)
    onConsumeSelect && onConsumeSelect()
  }, [selectTenantId])  // eslint-disable-line

  useEffect(() => {
    if (!selected) return
    setSetup(null)
    authFetch(`/v1/master/tenants/${selected}/setup`).then(setSetup).catch(e => onError(e.message))
  }, [selected])  // eslint-disable-line

  const knownIds = new Set(tenants.map(t => t.tenant_id))
  const unprovisioned = signups.filter(s => !s.tenant_id || !knownIds.has(s.tenant_id))

  return { tenants, selected, setSelected, setup, unprovisioned }
}

export function useKnowledgeQueue({ onError }) {
  const [rows, setRows] = useState(null)
  const [busy, setBusy] = useState(null)
  const [targets, setTargets] = useState({})

  const load = () => {
    authFetch('/v1/master/learned-answers?limit=100')
      .then(d => setRows(d.candidates || []))
      .catch(e => onError(e.message))
  }
  useEffect(() => { load() }, [])  // eslint-disable-line

  const promote = async (row) => {
    const target = targets[row.id] || 'business'
    setBusy(row.id)
    try {
      const r = await authFetch(`/v1/master/learned-answers/${row.id}/promote`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
      })
      if (r.error) onError(r.error)
      else load()
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  return { rows, busy, targets, setTargets, promote }
}

export function useMasterAudit({ onError }) {
  const [events, setEvents] = useState(null)
  useEffect(() => { authFetch('/v1/master/audit?limit=100').then(d => setEvents(d.events || [])).catch(e => onError(e.message)) }, [])
  return { events }
}

export function useGoLiveTest({ onError }) {
  const [tenant, setTenant] = useState('')
  const [res, setRes] = useState(null)
  const load = useCallback((tid) => authFetch(`/v1/master/golive-test?tenant_id=${encodeURIComponent(tid)}`)
    .then(setRes).catch(e => onError(e.message)), [])
  useEffect(() => {
    if (!tenant || res?.status !== 'running') return
    const t = setInterval(() => load(tenant), 10000)
    return () => clearInterval(t)
  }, [tenant, res?.status])
  const run = async () => {
    try {
      const r = await authFetch('/v1/master/golive-test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tenant_id: tenant }),
      })
      if (r.detail) onError(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail))
      else load(tenant)
    } catch (e) { onError(e.message) }
  }
  return { tenant, setTenant, res, setRes, load, run }
}

export function useBenchmark({ onError }) {
  const [data, setData] = useState(null)
  const [only, setOnly] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(null)
  const load = () => authFetch('/v1/master/benchmark').then(setData).catch(e => onError(e.message))
  useEffect(() => { load() }, [])
  const running = (data?.runs || []).some(r => r.status === 'running')
  useEffect(() => {
    if (!running) return
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [running])
  const run = async () => {
    setBusy(true)
    try {
      const r = await authFetch('/v1/master/benchmark', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ only: only || null }),
      })
      if (r.detail) onError(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail))
      else load()
    } catch (e) { onError(e.message) } finally { setBusy(false) }
  }
  return { data, only, setOnly, busy, open, setOpen, running, run }
}

export function useConversationCheck({ onError }) {
  const yesterday = todaySA(-1)
  const [day, setDay] = useState(yesterday)
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(null)
  const [sending, setSending] = useState(false)
  const load = useCallback(() => {
    setData(null)
    authFetch(`/v1/master/conversations/check?day=${day}`).then(setData).catch(e => onError(e.message))
  }, [day])
  useEffect(() => { load() }, [load])
  const send = async () => {
    setSending(true)
    try {
      const r = await authFetch('/v1/master/conversations/check/send', { method: 'POST' })
      if (!r.sent) onError(r.reason || 'Not sent')
    } catch (e) { onError(e.message) } finally { setSending(false) }
  }
  return { day, setDay, data, open, setOpen, sending, send }
}

export function useAdvisorPreview({ onError }) {
  const [tenant, setTenant] = useState('')
  const [res, setRes] = useState(null)
  const load = () => authFetch(`/v1/master/advisor/preview?tenant_id=${encodeURIComponent(tenant)}`)
    .then(setRes).catch(e => onError(e.message))
  return { tenant, setTenant, res, load }
}

export function useRetitle({ onError }) {
  const [tenant, setTenant] = useState('')
  const [state, setState] = useState(null)
  const poll = useCallback((tid) => authFetch(`/v1/master/documents/retitle?tenant_id=${encodeURIComponent(tid)}`)
    .then(setState).catch(e => onError(e.message)), [])
  useEffect(() => {
    if (!tenant || state?.status !== 'running') return
    const t = setInterval(() => poll(tenant), 5000)
    return () => clearInterval(t)
  }, [tenant, state?.status])
  const start = async (apply) => {
    if (apply && !(await confirmDialog(`Rename ${state?.items?.length || 0} documents for ${tenant}?`))) return
    try {
      const r = await authFetch('/v1/master/documents/retitle', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenant, apply }),
      })
      if (r.detail) onError(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail))
      else poll(tenant)
    } catch (e) { onError(e.message) }
  }
  return { tenant, setTenant, state, setState, start }
}

export function useModelBakeoff({ onError }) {
  const [info, setInfo] = useState(null)
  const [picked, setPicked] = useState([])
  const [custom, setCustom] = useState('')
  const [skill, setSkill] = useState('')
  const [reports, setReports] = useState(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(null)
  const [fbCases, setFbCases] = useState(null)
  const [copied, setCopied] = useState('')
  const copy = (text, id) => {
    try { navigator.clipboard.writeText(text); setCopied(id); setTimeout(() => setCopied(''), 1500) }
    catch { onError('Copy failed — select the text instead.') }
  }

  const loadReports = () => authFetch('/v1/master/evals/reports?limit=30')
    .then(d => setReports(d.reports || [])).catch(e => onError(e.message))
  useEffect(() => {
    authFetch('/v1/master/evals/candidates').then(d => { setInfo(d); setPicked(d.candidates || []) }).catch(e => onError(e.message))
    loadReports()
    authFetch('/v1/master/evals/feedback-cases?limit=50').then(d => setFbCases(d.cases || [])).catch(() => setFbCases([]))
  }, [])
  const running = (reports || []).some(r => r.status === 'running')
  useEffect(() => {
    if (!running) return
    const t = setInterval(loadReports, 10000)
    return () => clearInterval(t)
  }, [running])

  const run = async () => {
    const models = [...picked, ...custom.split(',').map(x => x.trim()).filter(Boolean)]
    setBusy(true)
    try {
      const r = await authFetch('/v1/master/evals/tools', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models, skill: skill || null }),
      })
      if (r.detail) onError(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail))
      else { setCustom(''); loadReports() }
    } catch (e) { onError(e.message) } finally { setBusy(false) }
  }

  return { info, picked, setPicked, custom, setCustom, skill, setSkill, reports, busy, open, setOpen, fbCases, copied, copy, running, run }
}
