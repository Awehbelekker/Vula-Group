/**
 * useMasterOps.js — Master › Health (WhatsApp lines, scheduler, Qdrant backups, routing, VRL,
 * webhook failures, pending migrations), the weekly digest, Usage & billing, and Users (logins:
 * create, reset password, remove) (2026-10-19, UX redesign Phase F). Moved out of
 * VulaMasterPanel unchanged — same /v1/master/… calls.
 */
import { useEffect, useState } from 'react'
import { authFetch } from '../../lib/authFetch'
import { confirmDialog } from '../ui/index.jsx'

export function useMasterHealth({ onError }) {
  const [h, setH] = useState(null)
  const [migMsg, setMigMsg] = useState('')
  const [qb, setQb] = useState(null)
  const [qbBusy, setQbBusy] = useState(false)
  useEffect(() => { authFetch('/v1/master/health').then(setH).catch(e => onError(e.message)) }, [])
  useEffect(() => { authFetch('/v1/master/qdrant-backup').then(r => setQb(r.statuses || [])).catch(e => onError(e.message)) }, [])
  const runQdrantBackup = async () => {
    setQbBusy(true)
    try {
      const r = await authFetch('/v1/master/qdrant-backup/run', { method: 'POST' })
      if (r.error) onError(r.error)
      else setQb(r.statuses || [])
    } catch (e) { onError(e.message) } finally { setQbBusy(false) }
  }
  const copyPendingSql = async () => {
    try {
      const r = await authFetch('/v1/master/migrations/pending-sql')
      if (!r.sql) return setMigMsg('Nothing pending.')
      await navigator.clipboard.writeText(r.sql)
      setMigMsg(`Copied SQL for ${r.migrations.join(', ')} — paste it into the Supabase SQL editor and run once, then reload this page.`)
    } catch (e) { onError(`Couldn't copy: ${e.message}`) }
  }
  return { h, migMsg, qb, qbBusy, runQdrantBackup, copyPendingSql }
}

/** Crashes in the dashboard on people's devices (GET /v1/master/app-errors, vula/app_errors.py):
 * scrubbed, counted per build and business; "Fixed" hides one until it happens again. */
export function useAppErrors({ onError }) {
  const [errors, setErrors] = useState(null)
  const load = () => authFetch('/v1/master/app-errors?days=14').then(d => setErrors(d.errors || [])).catch(e => onError(e.message))
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const resolve = async (id) => {
    try { await authFetch(`/v1/master/app-errors/${id}/resolve`, { method: 'POST' }); await load() } catch (e) { onError(e.message) }
  }
  return { errors, resolve }
}

/** Vula's WhatsApp login-code template on each WhatsApp account (Meta's review status), and
 * submitting it where it's missing — only that template (GET/POST /v1/master/wa-login-template). */
export function useLoginTemplate({ onError }) {
  const [t, setT] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const load = () => authFetch('/v1/master/wa-login-template').then(setT).catch(e => onError(e.message))
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const missing = (t?.accounts || []).filter(a => a.status === 'missing')
  const submit = async () => {
    const n = missing.length
    if (!n || !(await confirmDialog(`Send Vula's login-code template to Meta for review on ${n} WhatsApp account${n === 1 ? '' : 's'}? Only this template is sent.`,
      { title: 'Submit login template', confirmLabel: 'Submit' }))) return
    setBusy(true); setMsg('')
    try {
      const r = await authFetch('/v1/master/wa-login-template', { method: 'POST' })
      const done = (r.submitted || []).map(a => a.account)
      const bad = (r.failed || []).map(a => `${a.account}: ${a.error}`)
      setMsg([done.length ? `Sent for review on ${done.join(', ')}. Meta usually decides within a few hours.` : '',
        bad.length ? `Not sent: ${bad.join('; ')}` : ''].filter(Boolean).join(' ') || 'Nothing needed sending.')
      await load()
    } catch (e) { onError(e.message) } finally { setBusy(false) }
  }
  return { t, busy, msg, missing, submit }
}

export function useWeeklyDigest({ onError }) {
  const [text, setText] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const preview = async () => {
    setBusy(true)
    try { setText((await authFetch('/v1/master/digest')).text) } catch (e) { onError(e.message) }
    setBusy(false)
  }
  const send = async () => {
    setBusy(true)
    try {
      const r = await authFetch('/v1/master/digest/send', { method: 'POST' })
      setMsg(r.sent ? 'Sent.' : `Not sent: ${r.reason}`)
    } catch (e) { onError(e.message) }
    setBusy(false)
  }
  return { text, msg, busy, preview, send }
}

export function useMasterUsage({ onError }) {
  const [u, setU] = useState(null)
  useEffect(() => { authFetch('/v1/master/usage?days=14').then(setU).catch(e => onError(e.message)) }, [])
  return { u }
}

export function useMasterUsers({ onError }) {
  const [users, setUsers] = useState(null)
  const [tenants, setTenants] = useState([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ tenant_id: '', email: '', name: '', role: 'staff' })
  const [busy, setBusy] = useState(null)
  const [notice, setNotice] = useState('')

  const load = () => {
    authFetch('/v1/master/users').then(d => setUsers(d.users || [])).catch(e => onError(e.message))
  }
  useEffect(() => {
    load()
    authFetch('/v1/master/tenants').then(d => {
      const list = d.tenants || []
      setTenants(list)
      if (list.length) setForm(f => ({ ...f, tenant_id: f.tenant_id || list[0].tenant_id }))
    }).catch(() => {})
  }, [])  // eslint-disable-line

  const create = async () => {
    if (!form.tenant_id || !form.email.trim()) return onError('tenant + email required')
    setBusy('create')
    try {
      const r = await authFetch(`/v1/master/tenants/${form.tenant_id}/users`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email.trim(), name: form.name.trim() || undefined, role: form.role }),
      })
      if (r.error) onError(r.error)
      else {
        setNotice(`Created ${r.email} — temp password: ${r.temp_password}`)
        setForm(f => ({ ...f, email: '', name: '' })); setCreating(false); load()
      }
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  const reset = async (r) => {
    setBusy(r.user_id)
    try {
      const d = await authFetch(`/v1/master/tenants/${r.tenant_id}/users/${r.user_id}/reset`, { method: 'POST' })
      if (d.error) onError(d.error)
      else setNotice(`New temp password for ${r.email || r.user_id}: ${d.temp_password}`)
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  const remove = async (r) => {
    // a removed login is locked out at once and can't be undone, so ask first (2026-10-19)
    const who = r.email || r.user_id
    if (!(await confirmDialog(`Remove ${who}'s login for ${r.tenant_id}? They're signed out and can't get back in until a new login is made.`,
      { danger: true, confirmLabel: 'Remove' }))) return
    setBusy(r.user_id)
    try {
      await authFetch(`/v1/master/tenants/${r.tenant_id}/users/${r.user_id}`, { method: 'DELETE' })
      load()
    } catch (e) { onError(e.message) } finally { setBusy(null) }
  }

  return { users, tenants, creating, setCreating, form, setForm, busy, notice, setNotice, create, reset, remove }
}
