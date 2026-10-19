/**
 * useShopDomain.js — the shop's own address: add it, poll while the DNS records and certificate
 * settle, remove it (2026-10-19, UX redesign Phase F). Moved out of VulaShopDomain unchanged —
 * same GET/POST/DELETE /admin/shop-domain (vula/commerce/shop_domain.py).
 */
import { useEffect, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog, toast } from '../../ui/index.jsx'

export async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied') } catch { toast('Copy failed — select and copy it instead', 'danger') }
}

export function useShopDomain(tenantId) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/shop-domain`
  const [info, setInfo] = useState(null)
  const [domain, setDomain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function load() {
    try {
      const r = await fetch(base)
      const data = r.ok ? await r.json() : { state: 'none' }
      setInfo(data)
      if (!domain && data.suggestion) setDomain(data.suggestion)
    } catch {
      setInfo({ state: 'none' })
    }
  }

  useEffect(() => { if (tenantId) load() }, [tenantId])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {                      // keep checking while it's being set up
    if (!info || !['waiting_dns', 'securing'].includes(info.state)) return
    const t = setTimeout(load, 20000)
    return () => clearTimeout(t)
  }, [info])   // eslint-disable-line react-hooks/exhaustive-deps

  async function connect(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const r = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain }) })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || 'Could not add that address')
      setInfo(data)
      toast('Address added — now add the DNS records below.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!(await confirmDialog('Stop using your own address? Links go back to Vula’s, hidden behind the WhatsApp buttons as before.'))) return
    const r = await fetch(base, { method: 'DELETE' })
    if (r.ok) { setInfo(await r.json()); toast('Removed.') }
  }

  return { info, domain, setDomain, busy, error, connect, remove }
}
