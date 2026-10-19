/**
 * VulaWooConnect.jsx — "Connect WooCommerce" in Settings (vula_mind/vula/integrations/woocommerce.py).
 *
 * The owner makes a REST API key in WooCommerce and pastes the store address, consumer key and
 * secret here. Vula reads the whole catalogue (drafts, variations, every photo, stock); with a
 * Read/Write key WooCommerce tells Vula whenever a product, stock level or order changes. Keys
 * are sent once and never shown again.
 * Data and actions: settings/connect/useStoreConnect.js (shared with the redesign's StoreConnectV2).
 */
import { Badge, Button, Field, Input } from './ui/index.jsx'
import { useStoreConnect } from './settings/connect/useStoreConnect'

const STEPS = [
  'In WordPress, open WooCommerce › Settings › Advanced › REST API and click “Add key”.',
  'Description: Vula. User: a shop manager or administrator. Permissions: Read/Write (Read also works, but then updates come every 6 hours instead of live).',
  'Click “Generate API key” and copy the Consumer key and Consumer secret into the boxes below.',
]

function when(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

export default function VulaWooConnect({ tenantId, adminEmail }) {
  const { info, showForm, setShowForm, form, setForm, busy, error, setError, connect, syncNow, disconnect } =
    useStoreConnect({ tenantId, adminEmail, store: 'woo' })

  if (!info) return <div style={{ fontSize: 13, color: 'var(--muted)' }}>Loading…</div>

  const sync = info.sync || {}
  const last = info.last_sync || {}

  if (info.connected && !showForm) {
    return (
      <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Badge tone="ok">Connected</Badge>
          <strong>{info.store_name || info.store_url}</strong>
          <span style={{ color: 'var(--muted)' }}>{info.store_url}</span>
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
        {info.permissions === 'read' && (
          <div style={{ color: 'var(--warn)' }}>The key is Read only, so changes reach Vula every 6 hours. Make a Read/Write key and connect again for live updates.</div>
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
          Selling on WooCommerce? Connect it and Vula keeps your products, photos, prices and stock in step — so WhatsApp answers are always current.
        </div>
        <div><Button size="sm" onClick={() => setShowForm(true)}>Connect WooCommerce</Button></div>
      </div>
    )
  }

  return (
    <form onSubmit={connect} style={{ display: 'grid', gap: 10, fontSize: 13 }}>
      <ol style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
        {STEPS.map(s => <li key={s}>{s}</li>)}
      </ol>
      <Field label="Shop address" hint="Your WooCommerce shop’s main address, e.g. https://www.yourshop.co.za">
        <Input required value={form.store_url} placeholder="https://www.yourshop.co.za"
               onChange={e => setForm({ ...form, store_url: e.target.value })} />
      </Field>
      <Field label="Consumer key" hint="Starts with ck_">
        <Input required autoComplete="off" value={form.consumer_key}
               onChange={e => setForm({ ...form, consumer_key: e.target.value })} />
      </Field>
      <Field label="Consumer secret" hint="Starts with cs_">
        <Input required type="password" autoComplete="off" value={form.consumer_secret}
               onChange={e => setForm({ ...form, consumer_secret: e.target.value })} />
      </Field>
      {error && <div style={{ color: 'var(--danger)' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <Button type="submit" size="sm" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => { setShowForm(false); setError(null) }}>Cancel</Button>
      </div>
    </form>
  )
}
