/**
 * VulaShopDomain.jsx — "Your shop's own address" in Settings (vula_mind/vula/commerce/shop_domain.py).
 *
 * Optional. The shop already works on WhatsApp with its links hidden behind buttons; an own
 * address (shop.yourbusiness.co.za) just shows the business's name at the top when the shop or
 * a receipt opens. Type it, copy the two DNS records to whoever looks after the domain, and Vula
 * switches over by itself once it works.
 * Data and actions: settings/connect/useShopDomain.js (shared with the redesign's ShopDomainV2).
 */
import { Badge, Button, Field, Input } from './ui/index.jsx'
import { copy, useShopDomain } from './settings/connect/useShopDomain'

const STATE = {
  waiting_dns: ['warn', 'Waiting for the DNS records'],
  securing: ['warn', 'Securing the address (a few minutes)'],
  live: ['ok', 'Live'],
}

export default function VulaShopDomain({ tenantId }) {
  const { info, domain, setDomain, busy, error, connect, remove } = useShopDomain(tenantId)

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
