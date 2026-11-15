/**
 * ProspectsV2.jsx — Master › Prospects (13 Nov 2026): scan a business's website before it's a
 * client, read what Vula found and what's missing, copy the pitch, then make it a tenant (the
 * new-tenant form, filled in; that imports the website for real). Nothing a scan reads is saved
 * to any tenant. Social links are kept as links: nothing reads them yet.
 */
import { useState } from 'react'
import { ChevronDown, ChevronUp, Copy, RefreshCw, Search, Trash2, UserPlus } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, EmptyState, Field, Input, Skeleton, StatusChip, Textarea, confirmDialog, toast } from '../ui/index.jsx'
import { saDate } from './SignupsV2'
import { slugFor, useProspects } from './useProspects'
import './hq.css'

const TONE = { done: 'ok', scanning: 'warn', failed: 'danger' }
const LABEL = { done: 'Ready', scanning: 'Scanning…', failed: 'Failed' }
const rands = (c) => formatMoney(c || 0, { cents: 'never' })

export default function ProspectsV2({ onError, onMakeTenant }) {
  const x = useProspects({ onError })
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [socials, setSocials] = useState('')

  async function submit(e) {
    e.preventDefault()
    if (!url.trim()) return
    const p = await x.scan({ url: url.trim(), name: name.trim(), socials: socials.split(/[\s,]+/).filter(Boolean) })
    if (p) { setUrl(''); setName(''); setSocials('') }
  }

  return (
    <section className="vhq" aria-label="Prospects">
      <p className="vhq-sub">Scan a business's website before it's a client: what Vula found, what's missing and the pitch. Nothing is saved to a tenant.</p>
      <Card>
        <form onSubmit={submit} style={{ display: 'grid', gap: 10 }} aria-label="Scan a prospect">
          <Field label="Website"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="e.g. kelpgearshop.com" required /></Field>
          <Field label="Business name (optional)"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="From the website if left blank" /></Field>
          <Field label="Social pages (optional)" hint="Links only, kept with the report: Vula doesn't read social pages yet.">
            <Input value={socials} onChange={(e) => setSocials(e.target.value)} placeholder="instagram.com/…, facebook.com/…" />
          </Field>
          <div><Button type="submit" Icon={Search} disabled={x.busy || !url.trim()}>{x.busy ? 'Starting…' : 'Scan'}</Button></div>
        </form>
      </Card>

      {x.loading ? <Card><Skeleton lines={3} /></Card>
        : x.list.length === 0
          ? <EmptyState Icon={Search} title="No prospects yet">Scan a website above. A scan takes a minute or two.</EmptyState>
          : (
            <Card pad={4}>
              <ul className="vhq-list" aria-label="Prospects scanned">
                {x.list.map((p) => {
                  const isOpen = x.openId === p.id
                  return (
                    <li key={p.id} className="vhq-item">
                      <button type="button" className="vhq-toggle" aria-expanded={isOpen} onClick={() => x.setOpenId(isOpen ? null : p.id)}>
                        <span className="vhq-grow"><b>{p.name}</b><span className="vhq-muted">{p.url.replace(/^https?:\/\//, '')}</span></span>
                        {p.tenant_id && <StatusChip outline>Tenant: {p.tenant_id}</StatusChip>}
                        {p.status === 'done' && <StatusChip outline>{p.counts?.products ? `${p.counts.products} products` : `${p.counts?.pages || 0} pages`}</StatusChip>}
                        <StatusChip tone={TONE[p.status] || 'muted'}>{LABEL[p.status] || p.status}</StatusChip>
                        <span className="vhq-muted">{saDate(p.created_at)}</span>
                        {isOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
                      </button>
                      {isOpen && <Report p={x.open && x.open.id === p.id ? x.open : p} x={x} onMakeTenant={onMakeTenant} />}
                    </li>
                  )
                })}
              </ul>
            </Card>
          )}
    </section>
  )
}

function Report({ p, x, onMakeTenant }) {
  const r = p.report
  if (p.status === 'scanning') return <div className="vhq-detail"><p className="vhq-sub">Reading the website… this takes a minute or two.</p></div>
  if (p.status === 'failed' || !r) {
    return (
      <div className="vhq-detail">
        <p className="vhq-error" role="alert">{p.error || "The scan didn't finish."}</p>
        <Actions p={p} x={x} onMakeTenant={onMakeTenant} />
      </div>
    )
  }
  async function copy() {
    try { await navigator.clipboard.writeText(r.pitch); toast('Pitch copied') } catch { toast("Couldn't copy: select the text instead", 'warn') }
  }
  return (
    <div className="vhq-detail" style={{ display: 'grid', gap: 14 }}>
      <List title="What Vula found" items={r.found} empty="Nothing could be read." />
      <List title="What we'd fix" items={r.gaps} empty="Nothing missing that a scan can see." />
      <List title="With Vula" items={r.offer} />
      {r.products?.length > 0 && (
        <div>
          <h4 className="vhq-h">Products ({r.counts?.products})</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {r.products.slice(0, 12).map((it, i) => (
              <li key={i}>{it.name} — {it.price_max_cents > it.price_min_cents ? `${rands(it.price_min_cents)}–${rands(it.price_max_cents)}` : rands(it.price_min_cents)}
                {it.options > 1 ? `, ${it.options} options` : ''}{`, ${it.photos} photo${it.photos === 1 ? '' : 's'}`}</li>
            ))}
          </ul>
        </div>
      )}
      {r.socials?.length > 0 && (
        <div>
          <h4 className="vhq-h">Social pages <span className="vhq-muted">— links only, not read</span></h4>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{r.socials.map((s) => <li key={s}><a href={s} target="_blank" rel="noreferrer">{s.replace(/^https?:\/\//, '')}</a></li>)}</ul>
        </div>
      )}
      <Field label="The pitch">
        <Textarea readOnly value={r.pitch} rows={12} aria-label="The pitch" />
      </Field>
      <Actions p={p} x={x} onMakeTenant={onMakeTenant} onCopy={copy} />
    </div>
  )
}

function List({ title, items, empty }) {
  return (
    <div>
      <h4 className="vhq-h">{title}</h4>
      {items?.length ? <ul style={{ margin: 0, paddingLeft: 18 }}>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
        : <p className="vhq-muted" style={{ margin: 0 }}>{empty}</p>}
    </div>
  )
}

function Actions({ p, x, onMakeTenant, onCopy }) {
  const r = p.report || {}
  return (
    <div className="vhq-row" style={{ flexWrap: 'wrap', gap: 8 }}>
      {onCopy && <Button variant="secondary" Icon={Copy} onClick={onCopy}>Copy pitch</Button>}
      {!p.tenant_id && onMakeTenant && (
        <Button Icon={UserPlus} onClick={() => onMakeTenant({
          prospect_id: p.id, tenant_id: slugFor(r.name || p.name), display_name: r.name || p.name,
          business_type: r.kind === 'shop' ? 'retail' : 'services', website: p.url,
        })}>Make them a tenant</Button>
      )}
      <Button variant="secondary" Icon={RefreshCw} onClick={() => x.rescan(p.id)}>Scan again</Button>
      <Button variant="secondary" Icon={Trash2} onClick={async () => {
        if (await confirmDialog(`Delete the scan of ${p.name}?`, { danger: true, confirmLabel: 'Delete' })) x.remove(p.id)
      }}>Delete</Button>
    </div>
  )
}
