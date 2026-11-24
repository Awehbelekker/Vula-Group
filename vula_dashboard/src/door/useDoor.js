/**
 * useDoor.js — the door page's data (24 Nov 2026). Backend: vula_mind/vula/api/events.py, /v1/door.
 *
 * The page is opened from an event's door link; the secret after the # is the only way in and is
 * never sent anywhere but Vula's API. The list of tickets (names, types and the SHA-256 of each
 * code — never a code) is kept on this phone, so a scan can be judged with no signal: it is
 * marked here, queued, and sent when the signal is back. The server is still the judge — a ticket
 * another door let in first comes back as "already used" and is shown as such.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'
const REFRESH_MS = 15000
const lsGet = (k) => { try { return JSON.parse(localStorage.getItem(k) || 'null') } catch { return null } }
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* private mode: nothing kept */ } }

export const doorKey = () => (location.hash.slice(1).match(/^D[A-Za-z0-9_-]{20,60}$/) || [''])[0]

/** What a scanner gives → the ticket code, or '' when it isn't one of ours. */
export function cleanCode(raw) {
  const s = String(raw || '').trim().split('/').pop()
  return /^VT-[A-Za-z0-9_-]{16,40}$/.test(s) ? s : ''
}

export async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('')
}

async function call(path, body) {
  const r = await fetch(`${API}/v1/door/${path}`, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
  const d = await r.json().catch(() => ({}))
  if (!r.ok) { const e = new Error(typeof d.detail === 'string' ? d.detail : 'That didn’t work.'); e.status = r.status; throw e }
  return d
}
/** A failure that means "no signal", not "the server said no". */
const offlineError = (e) => !e.status

export function useDoor(key) {
  const store = `vula.door.${key}`
  const saved = useRef(lsGet(store) || {})
  const [data, setData] = useState(saved.current.data || null)
  const [queue, setQueue] = useState(saved.current.queue || [])
  const [device, setDeviceState] = useState(saved.current.device || '')
  const [online, setOnline] = useState(true)
  const [dead, setDead] = useState('')
  const queueRef = useRef(queue)
  queueRef.current = queue

  const persist = useCallback((patch) => {
    saved.current = { ...saved.current, ...patch }
    lsSet(store, saved.current)
  }, [store])
  const keepQueue = useCallback((q) => { setQueue(q); queueRef.current = q; persist({ queue: q }) }, [persist])
  const setDevice = (v) => { setDeviceState(v); persist({ device: v }) }

  /** The server's list, with this phone's unsent check-ins laid over it. */
  const overlay = useCallback((d, q) => {
    const mine = new Map(q.map(x => [x.ticket_id || x.h, x]))
    return { ...d, tickets: d.tickets.map(t => {
      const p = mine.get(t.id) || mine.get(t.h)
      return p && t.status !== 'checked_in' ? { ...t, status: 'checked_in', at: p.at, by: 'This phone', waiting: true } : t
    }) }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const d = await call(key)
      setOnline(true); persist({ data: d }); setData(overlay(d, queueRef.current))
    } catch (e) {
      if (offlineError(e)) setOnline(false)
      else if (e.status === 404) setDead(e.message)
    }
  }, [key, overlay, persist])

  const flush = useCallback(async () => {
    const q = queueRef.current
    if (!q.length) return []
    try {
      const out = await call(`${key}/sync`, { device, items: q.map(x => ({ ref: x.ref, code: x.code, ticket_id: x.ticket_id, at: x.at })) })
      const sent = new Set(q.map(x => x.ref))
      keepQueue(queueRef.current.filter(x => !sent.has(x.ref)))
      setOnline(true)
      return (out.results || []).filter(r => r.result !== 'ok')      // what another door got to first
    } catch (e) {
      if (offlineError(e)) setOnline(false)
      return []
    }
  }, [key, device, keepQueue])

  useEffect(() => {
    if (!key) return undefined
    const tick = async () => { await flush(); await refresh() }
    tick()
    const t = setInterval(tick, REFRESH_MS)
    window.addEventListener('online', tick)
    const off = () => setOnline(false)
    window.addEventListener('offline', off)
    return () => { clearInterval(t); window.removeEventListener('online', tick); window.removeEventListener('offline', off) }
  }, [key, flush, refresh])

  /** Let one ticket in: { code } from a scan, or { ticket } picked from the list. */
  const checkIn = useCallback(async ({ code, ticket }) => {
    const at = new Date().toISOString()
    const item = ticket ? { ticket_id: ticket.id } : { code }
    try {
      const res = await call(`${key}/check-in`, { ...item, device })
      setOnline(true); refresh()
      return res
    } catch (e) {
      if (!offlineError(e)) return { result: 'unknown', message: e.message }
      setOnline(false)
    }
    // No signal: judge it from the list on this phone, and send it later.
    const h = ticket ? ticket.h : await sha256(code)
    const cur = (data?.tickets || []).find(t => (ticket ? t.id === ticket.id : t.h === h))
    if (!cur) return { result: 'unknown', offline: true, message: 'Not on this phone’s list. It can’t be checked without signal.' }
    const who = { holder_name: cur.name, type: cur.type, checked_in_at: cur.at, checked_in_by: cur.by }
    if (cur.status === 'checked_in') return { result: 'already', offline: true, message: 'This ticket has already been used.', ticket: who }
    const q = [...queueRef.current, { ref: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, h, at, ...item }]
    keepQueue(q)
    setData(d => overlay({ ...d, tickets: d.tickets.map(t => ({ ...t })) }, q))
    return { result: 'ok', offline: true, message: 'Welcome in.', ticket: who }
  }, [key, device, data, refresh, keepQueue, overlay])

  return { data, queue, device, setDevice, online, dead, checkIn, refresh }
}
