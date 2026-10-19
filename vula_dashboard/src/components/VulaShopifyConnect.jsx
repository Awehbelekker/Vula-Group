/**
 * VulaShopifyConnect.jsx — "Connect Shopify" in Settings (vula_mind/vula/integrations/shopify.py).
 *
 * The owner creates an app for their own store in Shopify's Dev Dashboard and pastes its
 * Client ID and secret here. Vula then reads the whole catalogue (drafts, every photo, stock)
 * and Shopify tells Vula whenever a product, stock level or order changes. Secrets are sent
 * once and never shown again.
 * Data and actions: settings/connect/useStoreConnect.js (shared with the redesign's StoreConnectV2).
 */
import { Badge, Button, Field, Input } from './ui/index.jsx'
import { useStoreConnect } from './settings/connect/useStoreConnect'

const STEPS = [
  'In Shopify, open Settings › Apps and sales channels › Develop apps (or dev.shopify.com › Apps) and create an app called “Vula”.',
  'Give it these Admin API access scopes: read_products, read_inventory, read_orders. Release the version.',
  'Install the app on your store.',
  'Open the app’s Settings and copy the Client ID and the Client secret into the boxes below.',
]

function when(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

export default function VulaShopifyConnect({ tenantId, adminEmail }) {
  const { info, showForm, setShowForm, useToken, setUseToken, form, setForm, busy, error, setError, connect, syncNow, disconnect } =
    useStoreConnect({ tenantId, adminEmail, store: 'shopify' })

  if (!info) return <div style={{ fontSize: 13, color: 'var(--muted)' }}>Loading…</div>

  const sync = info.sync || {}
  const last = info.last_sync || {}

  if (info.connected && !showForm) {
    return (
      <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Badge tone="ok">Connected</Badge>
          <strong>{info.shop_name || info.shop_domain}</strong>
          <span style={{ color: 'var(--muted)' }}>{info.shop_domain}</span>
          <Badge tone={info.live_updates ? 'ok' : 'warn'}>
            {info.live_updates ? 'Live updates on' : 'Updates every 6 hours'}
          </Badge>
        </div>
        {sync.running ? (
          <div style={{ color: 'var(--muted)' }}>Syncing: {sync.step}…</div>
        ) : (
          <div style={{ color: 'var(--muted)' }}>
            Last sync {when(info.last_sync_at)}
            {last.products_found != null && ` — ${last.products_found} products (${last.products_created || 0} new, ${last.products_updated || 0} updated), ${last.stock_changes || 0} stock changes`}.
          </div>
        )}
        {(info.last_error || sync.error) && (
          <div style={{ color: 'var(--danger)' }}>{sync.error || info.last_error}</div>
        )}
        {!(info.scopes || []).includes('read_inventory') && (
          <div style={{ color: 'var(--warn)' }}>The app has no read_inventory access, so stock levels aren’t read. Add it in Shopify and connect again.</div>
        )}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button size="sm" onClick={syncNow} disabled={sync.running}>Sync now</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowForm(true)}>Reconnect</Button>
          <Button size="sm" variant="danger" onClick={disconnect}>Disconnect</Button>
        </div>
      </div>
    )
  }

  if (!showForm) {
    return (
      <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
        <div style={{ color: 'var(--muted)' }}>
          Selling on Shopify? Connect it and Vula keeps your products, photos, prices and stock in step — so WhatsApp answers are always current.
        </div>
        <div><Button size="sm" onClick={() => setShowForm(true)}>Connect Shopify</Button></div>
      </div>
    )
  }

  return (
    <form onSubmit={connect} style={{ display: 'grid', gap: 10, fontSize: 13 }}>
      <ol style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
        {STEPS.map(s => <li key={s}>{s}</li>)}
      </ol>
      <Field label="Store address" hint="Your myshopify.com address, e.g. seedleme.myshopify.com">
        <Input required value={form.shop_domain} placeholder="yourstore.myshopify.com"
               onChange={e => setForm({ ...form, shop_domain: e.target.value })} />
      </Field>
      {useToken ? (
        <Field label="Admin API access token" hint="Only for an older custom app (starts with shpat_). Updates every 6 hours instead of live.">
          <Input required type="password" autoComplete="off" value={form.access_token}
                 onChange={e => setForm({ ...form, access_token: e.target.value })} />
        </Field>
      ) : (
        <>
          <Field label="Client ID">
            <Input required autoComplete="off" value={form.client_id}
                   onChange={e => setForm({ ...form, client_id: e.target.value })} />
          </Field>
          <Field label="Client secret">
            <Input required type="password" autoComplete="off" value={form.client_secret}
                   onChange={e => setForm({ ...form, client_secret: e.target.value })} />
          </Field>
        </>
      )}
      <button type="button" onClick={() => setUseToken(!useToken)}
              style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent)', cursor: 'pointer', textAlign: 'left', fontSize: 12.5 }}>
        {useToken ? 'Use a Client ID and secret instead' : 'I have an Admin API access token (older custom app)'}
      </button>
      {error && <div style={{ color: 'var(--danger)' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <Button type="submit" size="sm" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => { setShowForm(false); setError(null) }}>Cancel</Button>
      </div>
    </form>
  )
}
