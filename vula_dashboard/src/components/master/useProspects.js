/**
 * useProspects.js — Master › Prospects (13 Nov 2026): businesses scanned before they're clients
 * (vula/prospects.py). Scans run in the background; while any is scanning the list refreshes.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { authFetch } from '../../lib/authFetch'

const POLL_MS = 5000

export function useProspects({ onError } = {}) {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState(null)
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)

  const load = useCallback(async () => {
    try {
      const d = await authFetch('/v1/master/prospects')
      setList(d.prospects || [])
    } catch (e) { onError && onError(e.message) }
    finally { setLoading(false) }
  }, [onError])

  const loadOne = useCallback(async (id) => {
    if (!id) { setOpen(null); return }
    try { setOpen((await authFetch(`/v1/master/prospects/${id}`)).prospect || null) }
    catch (e) { onError && onError(e.message) }
  }, [onError])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadOne(openId) }, [openId, loadOne])

  // while a scan runs, check again every few seconds
  const scanning = list.some((p) => p.status === 'scanning') || open?.status === 'scanning'
  useEffect(() => {
    if (!scanning) return undefined
    timer.current = setTimeout(() => { load(); if (openId) loadOne(openId) }, POLL_MS)
    return () => clearTimeout(timer.current)
  }, [scanning, list, open, load, loadOne, openId])

  const scan = useCallback(async ({ url, name, socials }) => {
    setBusy(true)
    try {
      const d = await authFetch('/v1/master/prospects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, name, socials }),
      })
      await load()
      if (d.prospect?.id) setOpenId(d.prospect.id)
      return d.prospect
    } catch (e) { onError && onError(e.message); return null }
    finally { setBusy(false) }
  }, [load, onError])

  const rescan = useCallback(async (id) => {
    try { await authFetch(`/v1/master/prospects/${id}/rescan`, { method: 'POST' }); await load(); await loadOne(id) }
    catch (e) { onError && onError(e.message) }
  }, [load, loadOne, onError])

  const linkTenant = useCallback(async (id, tenantId) => {
    try {
      await authFetch(`/v1/master/prospects/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tenant_id: tenantId }),
      })
      await load()
    } catch (e) { onError && onError(e.message) }
  }, [load, onError])

  const remove = useCallback(async (id) => {
    try { await authFetch(`/v1/master/prospects/${id}`, { method: 'DELETE' }); setOpenId(null); await load() }
    catch (e) { onError && onError(e.message) }
  }, [load, onError])

  return { list, loading, open, openId, setOpenId, busy, scan, rescan, remove, linkTenant, load }
}

/** A tenant id from a business name: lower-case words joined by dashes. */
export function slugFor(name) {
  return String(name || '').toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}
