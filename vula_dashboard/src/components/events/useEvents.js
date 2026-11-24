/**
 * useEvents.js — Sell › Events (24 Nov 2026). Backend: vula_mind/vula/api/events.py.
 * Prices typed in are read as written (lib/money randToCents) and sent as integer cents; every
 * number shown (sold, left, takings) is the server's.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { randToCents } from '../../lib/money'

const base = (t) => `/v1/commerce/${t}/admin/events`

export function useEvents(tenantId) {
  const [events, setEvents] = useState(null)
  const [error, setError] = useState('')
  const load = useCallback(() => {
    setError('')
    return api(base(tenantId)).then(d => setEvents(d.events || [])).catch(e => { setError(e.message); setEvents(null) })
  }, [tenantId])
  useEffect(() => { load() }, [load])

  const save = (id, body) => api(id ? `${base(tenantId)}/${id}` : base(tenantId), { method: id ? 'PUT' : 'POST', body })
    .then(d => { load(); return d.event })
  const setStatus = (id, status) => api(`${base(tenantId)}/${id}/status`, { method: 'POST', body: { status } })
    .then(d => { load(); return d.event })
  return { events, error, load, save, setStatus }
}

export function useDoor(tenantId, eventId) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const url = `${base(tenantId)}/${eventId}`
  const load = useCallback(() => {
    setError('')
    return api(`${url}/attendees`).then(setData).catch(e => setError(e.message))
  }, [url])
  useEffect(() => { load() }, [load])
  const checkIn = (code) => api(`${url}/check-in`, { method: 'POST', body: { code } }).then(r => { load(); return r })
  const undo = (ticketId) => api(`${url}/tickets/${ticketId}/undo`, { method: 'POST', body: {} }).then(load)
  return { data, error, load, checkIn, undo }
}

/** A date-time input's value ("2026-12-05T18:00", the person's own clock) → ISO with its zone. */
export const localToIso = (v) => { const d = new Date(v); return v && !Number.isNaN(d.getTime()) ? d.toISOString() : '' }
export const isoToLocal = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
export const whenText = (iso) => iso ? new Date(iso).toLocaleString('en-ZA', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''

/** The editor's rows → what the server takes, or a message saying which row is wrong. */
export function typesBody(rows) {
  const out = []
  for (const r of rows) {
    const name = (r.name || '').trim()
    if (!name && !r.price && !r.capacity) continue
    const cents = randToCents(r.price)
    const capacity = Number(r.capacity)
    const most = Number(r.max_per_order || 10)
    if (!name) return { error: 'Give each ticket type a name.' }
    if (cents === null || cents < 100) return { error: `Enter a price for ${name}, like 150 or 150.00.` }
    if (!Number.isInteger(capacity) || capacity < 0) return { error: `Enter how many ${name} tickets there are.` }
    if (!Number.isInteger(most) || most < 1) return { error: `Enter the most ${name} tickets one person can buy.` }
    out.push({ ...(r.id ? { id: r.id } : {}), name, price_cents: cents, capacity, max_per_order: most })
  }
  return { types: out }
}
