/**
 * VulaShopDomain.jsx — "Your shop's own address" in Settings (vula_mind/vula/commerce/shop_domain.py).
 *
 * Optional. The shop already works on WhatsApp with its links hidden behind buttons; an own
 * address (shop.yourbusiness.co.za) just shows the business's name at the top when the shop or
 * a receipt opens. Type it, copy the two DNS records to whoever looks after the domain, and Vula
 * switches over by itself once it works.
 */
import { useEffect, useState } from 'react'
import { Badge, Button, Field, Input, confirmDialog, toast } from './ui/index.jsx'
import { VULA_API } from '../lib/authFetch'

const STATE = {
  waiting_dns: ['warn', 'Waiting for the DNS records'],
  securing: ['warn', 'Securing the address (a few minutes)'],
  live: ['ok', 'Live'],
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('Copied') } catch { toast('Copy failed — select and copy it instead', 'danger') }
}

export default function VulaShopDomain({ tenantId }) {
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

  useEffect(() => { if (tenantId) load() }, [tenantId])
  useEffect(() => {                      // keep checking while it's being set up
    if (!info || !['waiting_dns', 'securing'].includes(info.state)) return
    const t = setTimeout(load, 20000)
    return () => clearTimeout(t)
  }, [info])

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

  if (!info) return <div style={{ fontSize: 13, color: 'var(--muted)' }}>Loading…</div>

  if (!info.domain) {
    return (
      <form onSubmit={connect} style={{ display: 'grid', gap: 10, fontSize: 13 }}>
        <div style={{ color: 'var(--muted)' }}>
          Optional — your shop already works on WhatsApp. With your own address, customers see your name
          (not Vula’s) at the top when your shop or a receipt opens.
        </div>
        <Field label="Your shop’s address" hint="Starts with shop. so your main website stays as it is — e.g. shop.yourbusiness.co.za">
          <Input required value={domain} placeholder="shop.yourbusiness.co.za" onChange={e => setDomain(e.target.value)} />
        </Field>
        {error && <div style={{ color: 'var(--danger)' }}>{error}</div>}
        <div><Button type="submit" size="sm" disabled={busy}>{busy ? 'Adding…' : 'Use this address'}</Button></div>
      </form>
    )
  }

  const [tone, label] = STATE[info.state] || ['muted', 'Setting up']
  const records = info.records || []
  return (
    <div style={{ display: 'grid', gap: 10, fontSize: 13 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>{info.domain}</strong>
        <Badge tone={tone}>{info.state === 'live' ? '✓ ' : ''}{label}</Badge>
        {info.state === 'live' && <a href={`https://${info.domain}/`} target="_blank" rel="noreferrer">Open</a>}
      </div>
      {info.state !== 'live' && records.length > 0 && (
        <>
          <div style={{ color: 'var(--muted)' }}>
            Add these two records where {info.domain.split('.').slice(1).join('.')} is managed (your web person, Shopify › Domains,
            GoDaddy, Afrihost, Cloudflare…). Vula switches over by itself once they work — usually within an hour.
          </div>
          <div style={{ display: 'grid', gap: 6 }}>
            {records.map(r => (
              <div key={r.type + r.name} style={{ display: 'grid', gridTemplateColumns: '64px 1fr auto', gap: 8, alignItems: 'center',
                                                  border: '1px solid var(--border)', borderRadius: 10, padding: '8px 10px' }}>
                <strong>{r.type}</strong>
                <div style={{ minWidth: 0 }}>
                  <div>Name: <code>{r.name}</code></div>
                  <div style={{ overflowWrap: 'anywhere' }}>Value: <code>{r.value}</code></div>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {r.ok === true && <Badge tone="ok">✓</Badge>}
                  <Button size="sm" variant="ghost" onClick={() => copy(r.value)}>Copy</Button>
                </div>
              </div>
            ))}
          </div>
          {info.instructions && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Button size="sm" onClick={() => copy(info.instructions)}>Copy instructions for my web person</Button>
              <Button size="sm" variant="ghost" onClick={() => window.open(`https://wa.me/?text=${encodeURIComponent(info.instructions)}`, '_blank')}>
                Send on WhatsApp
              </Button>
            </div>
          )}
        </>
      )}
      {info.state !== 'live' && records.length === 0 && (
        <div style={{ color: 'var(--muted)' }}>
          {info.automated ? 'Fetching the DNS records…' : 'Saved — Vula will send you the two DNS records to add. Until then everything keeps working as before.'}
        </div>
      )}
      {info.error && <div style={{ color: 'var(--danger)' }}>{info.error}</div>}
      <div><Button size="sm" variant="danger" onClick={remove}>Stop using this address</Button></div>
    </div>
  )
}
