/**
 * CatalogueSync.jsx — "Send to WhatsApp catalogue" on Sell › Products (23 Nov 2026). Shown only
 * when the business has a WhatsApp catalogue connected (GET /admin/meta-catalog); sends every
 * active product again (POST /admin/meta-catalog/sync, owner and managers only). Connecting a
 * catalogue needs set-up on Meta's side, so that stays with Vula support.
 */
import { useEffect, useState } from 'react'
import { Send } from 'lucide-react'
import { VULA_API } from '../../lib/authFetch'
import { Button, toast } from '../ui/index.jsx'

export default function CatalogueSync({ tenantId }) {
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/meta-catalog`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((d) => { if (live) setConnected(!!d.connected) })
      .catch(() => {})
    return () => { live = false }
  }, [tenantId])

  if (!connected) return null

  async function send() {
    setBusy(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/meta-catalog/sync`, { method: 'POST' })
      const d = await r.json().catch(() => ({}))
      if (r.status === 403) toast('Only an owner or manager can send products to the WhatsApp catalogue.', 'danger')
      else if (!r.ok) toast(d.detail || "That didn't go through. Try again.", 'danger')
      else toast(d.message || 'Sent.', d.ok ? 'ok' : 'danger')
    } catch {
      toast("That didn't go through. Check your connection and try again.", 'danger')
    }
    setBusy(false)
  }

  return (
    <Button size="sm" variant="secondary" Icon={Send} onClick={send} disabled={busy}>
      {busy ? 'Sending…' : 'Send to WhatsApp catalogue'}
    </Button>
  )
}
