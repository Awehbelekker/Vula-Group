/**
 * StoreConnectV2.jsx — the redesigned Shopify and WooCommerce connections in Settings
 * (2026-10-19, UX redesign Phase F). Same flow as VulaShopifyConnect / VulaWooConnect
 * (connect/useStoreConnect.js): the connected store with live-updates state, the last sync or the
 * running step, Sync now / Reconnect / Disconnect, and the numbered setup steps with the
 * labelled key fields. Keys are sent once and never shown again.
 */
import { KeyRound, Link2, RefreshCw, Unplug } from 'lucide-react'
import { SA_TZ } from '../../../lib/dates'
import { Button, Card, Field, Input, Skeleton, StatusChip } from '../../ui/index.jsx'
import { STORES, useStoreConnect } from './useStoreConnect'
import '../settings.css'

const STEPS = {
  shopify: [
    'In Shopify, open Settings › Apps and sales channels › Develop apps (or dev.shopify.com › Apps) and create an app called “Vula”.',
    'Give it these Admin API access scopes: read_products, read_inventory, read_orders. Release the version.',
    'Install the app on your store.',
    'Open the app’s Settings and copy the Client ID and the Client secret into the boxes below.',
  ],
  woo: [
    'In WordPress, open WooCommerce › Settings › Advanced › REST API and click “Add key”.',
    'Description: Vula. User: a shop manager or administrator. Permissions: Read/Write (Read also works, but then updates come every 6 hours instead of live).',
    'Click “Generate API key” and copy the Consumer key and Consumer secret into the boxes below.',
  ],
}

const when = (iso) => {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: SA_TZ }) } catch { return iso }
}

export default function StoreConnectV2({ store, tenantId, adminEmail }) {
  const x = useStoreConnect({ tenantId, adminEmail, store })
  const S = STORES[store]
  const info = x.info
  if (!info) return <Card><Skeleton lines={3} /></Card>
  const sync = info.sync || {}
  const last = info.last_sync || {}
  const name = store === 'shopify' ? (info.shop_name || info.shop_domain) : (info.store_name || info.store_url)
  const address = store === 'shopify' ? info.shop_domain : info.store_url
  const set = (patch) => x.setForm({ ...x.form, ...patch })

  if (info.connected && !x.showForm) {
    const readLimited = store === 'shopify' ? !(info.scopes || []).includes('read_inventory') : info.permissions === 'read'
    return (
      <Card className="vst-card">
        <div className="vst-row">
          <span className="vst-label">Status</span>
          <StatusChip tone="ok">Connected</StatusChip>
          <StatusChip tone={info.live_updates ? 'ok' : 'warn'}>{info.live_updates ? 'Live updates on' : 'Updates every 6 hours'}</StatusChip>
        </div>
        <dl className="vcn-rows">
          <div className="vcn-row"><dt>Store</dt><dd>{name}</dd></div>
          {address && address !== name && <div className="vcn-row"><dt>Address</dt><dd>{address}</dd></div>}
          <div className="vcn-row"><dt>{sync.running ? 'Syncing' : 'Last sync'}</dt>
            <dd>{sync.running ? `${sync.step}…` : <>{when(info.last_sync_at)}{last.products_found != null &&
              ` — ${last.products_found} products (${last.products_created || 0} new, ${last.products_updated || 0} updated), ${last.stock_changes || 0} stock changes`}</>}</dd></div>
        </dl>
        {(info.last_error || sync.error) && <p className="vcn-error" role="alert">{sync.error || info.last_error}</p>}
        {readLimited && <p className="vcn-warn-box">{store === 'shopify'
          ? 'The app has no read_inventory access, so stock levels aren’t read. Add it in Shopify and connect again.'
          : 'The key is Read only, so changes reach Vula every 6 hours. Make a Read/Write key and connect again for live updates.'}</p>}
        <div className="vst-row">
          <Button Icon={RefreshCw} onClick={x.syncNow} disabled={sync.running}>Sync now</Button>
          <Button variant="secondary" Icon={KeyRound} onClick={() => x.setShowForm(true)}>Reconnect</Button>
          <Button variant="quiet" Icon={Unplug} className="vcn-danger-btn" onClick={x.disconnect}>Disconnect</Button>
        </div>
      </Card>
    )
  }

  if (!x.showForm) {
    return (
      <Card className="vst-card">
        <div className="vst-row"><span className="vst-label">Status</span><StatusChip>Not connected</StatusChip></div>
        <p className="vst-body">Selling on {S.label}? Connect it and Vula keeps your products, photos, prices and stock in step — so WhatsApp answers are always current.</p>
        <div className="vst-row"><Button Icon={Link2} onClick={() => x.setShowForm(true)}>Connect {S.label}</Button></div>
      </Card>
    )
  }

  return (
    <Card className="vst-card">
      <form className="vst-card" onSubmit={x.connect}>
        <ol className="vcn-steps">{STEPS[store].map(s => <li key={s}>{s}</li>)}</ol>
        {store === 'shopify' ? (
          <>
            <Field label="Store address" hint="Your myshopify.com address, e.g. seedleme.myshopify.com">
              <Input required value={x.form.shop_domain} placeholder="yourstore.myshopify.com" onChange={e => set({ shop_domain: e.target.value })} />
            </Field>
            {x.useToken ? (
              <Field label="Admin API access token" hint="Only for an older custom app (starts with shpat_). Updates every 6 hours instead of live.">
                <Input required type="password" autoComplete="off" value={x.form.access_token} onChange={e => set({ access_token: e.target.value })} />
              </Field>
            ) : (
              <div className="vst-grid">
                <Field label="Client ID"><Input required autoComplete="off" value={x.form.client_id} onChange={e => set({ client_id: e.target.value })} /></Field>
                <Field label="Client secret"><Input required type="password" autoComplete="off" value={x.form.client_secret} onChange={e => set({ client_secret: e.target.value })} /></Field>
              </div>
            )}
            <div><Button type="button" size="sm" variant="quiet" onClick={() => x.setUseToken(!x.useToken)}>
              {x.useToken ? 'Use a Client ID and secret instead' : 'I have an Admin API access token (older custom app)'}
            </Button></div>
          </>
        ) : (
          <>
            <Field label="Shop address" hint="Your WooCommerce shop’s main address, e.g. https://www.yourshop.co.za">
              <Input required value={x.form.store_url} placeholder="https://www.yourshop.co.za" onChange={e => set({ store_url: e.target.value })} />
            </Field>
            <div className="vst-grid">
              <Field label="Consumer key" hint="Starts with ck_"><Input required autoComplete="off" value={x.form.consumer_key} onChange={e => set({ consumer_key: e.target.value })} /></Field>
              <Field label="Consumer secret" hint="Starts with cs_"><Input required type="password" autoComplete="off" value={x.form.consumer_secret} onChange={e => set({ consumer_secret: e.target.value })} /></Field>
            </div>
          </>
        )}
        {x.error && <p className="vcn-error" role="alert">{x.error}</p>}
        <div className="vst-row">
          <Button type="submit" Icon={Link2} disabled={x.busy}>{x.busy ? 'Connecting…' : `Connect ${S.label}`}</Button>
          <Button type="button" variant="secondary" onClick={() => { x.setShowForm(false); x.setError(null) }}>Cancel</Button>
        </div>
      </form>
    </Card>
  )
}
