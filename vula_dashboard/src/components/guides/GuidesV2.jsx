/**
 * GuidesV2.jsx — Sell › Product guides (25 Nov 2026). A guide is a link the business sends a
 * client (an architect, a specifier, a customer): its own products with their spec sheets, in the
 * business's own name, logo and colour, with "Check stock on WhatsApp" and "Ask for a sample"
 * going to the business's number, where Vula answers from real stock. Spec sheets are edited on
 * each product (Products › a product › Spec sheet). The page is never indexed; turning a guide off
 * or making a new link stops the old link working.
 */
import { useMemo, useState } from 'react'
import { BookOpen } from 'lucide-react'
import { Button, Card, EmptyState, ErrorCard, Field, Input, Modal, PageHeader, Skeleton, StatusChip, Textarea, confirmDialog, toast } from '../ui/index.jsx'
import { useGuides } from './useGuides'
import SpecImport from './SpecImport'
import './guides.css'

const when = (iso) => {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' }) : ''
}

const EMPTY = { title: '', client_name: '', intro: '', categories: [], product_ids: [], show_prices: false, audience: 'client' }

function GuideForm({ g, cats, products, onSave, onClose }) {
  const [f, setF] = useState(g ? { ...EMPTY, ...g } : EMPTY)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))
  const flip = (k, v) => set(k, f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v])
  const shown = useMemo(() => {
    const w = q.trim().toLowerCase()
    return products.filter((p) => !w || `${p.name} ${p.category || ''}`.toLowerCase().includes(w)).slice(0, 200)
  }, [q, products])
  const inGuide = useMemo(() => {
    if (!f.categories.length && !f.product_ids.length) return products.filter((p) => (p.status || 'active') === 'active').length
    const ids = new Set(f.product_ids)
    return products.filter((p) => ids.has(p.id) || (f.categories.includes(p.category) && (p.status || 'active') === 'active')).length
  }, [f.categories, f.product_ids, products])

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      await onSave({ title: f.title, client_name: f.client_name, intro: f.intro, categories: f.categories,
        product_ids: f.product_ids, show_prices: f.show_prices, audience: f.audience || 'client' })
      onClose()
    } catch (x) {
      setErr(x.message)
    }
    setBusy(false)
  }

  return (
    <Modal open title={g ? 'Edit guide' : 'New product guide'} onClose={onClose} width={620}>
      <form className="vgd-form" onSubmit={submit}>
        <Field label="Title"><Input value={f.title} onChange={(e) => set('title', e.target.value)} placeholder="e.g. Flooring options for Riverside Offices" required /></Field>
        <Field label="Who it's for" hint="Shown as “Prepared for …”. Optional.">
          <Input value={f.client_name || ''} onChange={(e) => set('client_name', e.target.value)} placeholder="e.g. Riverside Architects" />
        </Field>
        <Field label="A line to start with" hint="Optional. No web addresses.">
          <Textarea value={f.intro || ''} onChange={(e) => set('intro', e.target.value)} rows={2} placeholder="e.g. The ranges we discussed for the ground floor and the stairs." />
        </Field>
        <fieldset className="vgd-set"><legend>Who it's for</legend>
          <label className="vgd-check"><input type="radio" name="vgd-aud" checked={(f.audience || 'client') === 'client'} onChange={() => set('audience', 'client')} /> A client: no competing products</label>
          <label className="vgd-check"><input type="radio" name="vgd-aud" checked={f.audience === 'team'} onChange={() => set('audience', 'team')} /> My team (reps): shows the products each one competes with</label>
        </fieldset>
        <label className="vgd-check"><input type="checkbox" checked={f.show_prices} onChange={(e) => set('show_prices', e.target.checked)} /> Show prices</label>

        {cats.length > 0 && (
          <fieldset className="vgd-set"><legend>Whole categories</legend>
            <div className="vgd-chips">
              {cats.map((c) => (
                <button type="button" key={c.key} className={'vgd-chip' + (f.categories.includes(c.key) ? ' on' : '')}
                  aria-pressed={f.categories.includes(c.key)} onClick={() => flip('categories', c.key)}>{c.label}</button>
              ))}
            </div>
          </fieldset>
        )}

        <fieldset className="vgd-set"><legend>And these products</legend>
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your products" aria-label="Search your products" />
          <ul className="vgd-picks">
            {shown.map((p) => (
              <li key={p.id}><label>
                <input type="checkbox" checked={f.product_ids.includes(p.id)} onChange={() => flip('product_ids', p.id)} />
                <span>{p.name}</span>
                {p.specs ? <StatusChip tone="ok">Spec sheet</StatusChip> : <span className="vgd-sub">no spec sheet</span>}
                {p.status === 'unlisted' && <span className="vgd-sub">unlisted</span>}
              </label></li>
            ))}
          </ul>
        </fieldset>
        <p className="vgd-sub">{!f.categories.length && !f.product_ids.length ? `Nothing chosen: the guide shows every listed product (${inGuide}).` : `${inGuide} product${inGuide === 1 ? '' : 's'} in this guide.`}</p>
        {err && <p className="vgd-err" role="alert">{err}</p>}
        <div className="vgd-row">
          <Button type="submit" disabled={busy}>{busy ? 'Saving…' : g ? 'Save changes' : 'Make the guide'}</Button>
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Modal>
  )
}

function shareText(g) {
  if (g.audience === 'team') return `Our field guide${g.title ? ` — ${g.title}` : ''} (for the team, not for clients): ${g.url}`
  return `${g.client_name ? `Hi ${g.client_name}, here` : 'Here'} is our product guide${g.title ? ` — ${g.title}` : ''}: ${g.url}`
}

export default function GuidesV2({ tenantId }) {
  const s = useGuides(tenantId)
  const [editing, setEditing] = useState(null)     // null | 'new' | guide
  const withSpecs = s.products.filter((p) => p.specs).length

  async function copy(g) {
    try { await navigator.clipboard.writeText(g.url); toast('Link copied') } catch { toast(g.url) }
  }
  async function act(fn, ok) {
    try { await fn(); if (ok) toast(ok) } catch (x) { toast(x.message, 'danger') }
  }

  return (
    <div className="vgd">
      <PageHeader sub="Send a client your range with the specs, in your own name and colours. They can check stock or ask for a sample on WhatsApp, and Vula answers from your stock."
        actions={<Button onClick={() => setEditing('new')}>New guide</Button>} />

      {s.loading ? <Card><Skeleton lines={4} /></Card> : s.failed ? <ErrorCard what="your product guides" onRetry={s.load} /> : (
        <>
          <Card className="vgd-tip">
            <p><strong>{withSpecs} of {s.products.length}</strong> products have a spec sheet (grade, benefits, specs, a link to the maker).
              Add one on each product (Products › a product › Spec sheet), or read them from your maker's catalogue below.</p>
          </Card>
          <SpecImport tenantId={tenantId} products={s.products} cats={s.cats} onApplied={s.load} />
          {s.guides.length === 0 ? (
            <EmptyState Icon={BookOpen} title="No guides yet" action={<Button onClick={() => setEditing('new')}>Make your first guide</Button>}>
              A guide is one link with your products and their specs: for an architect, a specifier or a customer.
            </EmptyState>
          ) : (
            <ul className="vgd-list">
              {s.guides.map((g) => (
                <li key={g.id}><Card className="vgd-item">
                  <div className="vgd-row vgd-top">
                    <h2 className="vgd-h">{g.title}</h2>
                    {g.audience === 'team' && <StatusChip tone="warn">Team only</StatusChip>}
                    {g.active ? <StatusChip tone="ok">Live</StatusChip> : <StatusChip>Off</StatusChip>}
                  </div>
                  <p className="vgd-sub">
                    {g.client_name ? `For ${g.client_name} · ` : ''}
                    {g.views ? `opened ${g.views} time${g.views === 1 ? '' : 's'}, last ${when(g.last_viewed_at)}` : 'not opened yet'}
                    {g.show_prices ? ' · with prices' : ''}
                  </p>
                  <div className="vgd-row vgd-acts">
                    <Button size="sm" onClick={() => copy(g)} disabled={!g.active}>Copy link</Button>
                    <Button size="sm" variant="secondary" disabled={!g.active}
                      onClick={() => window.open(`https://wa.me/?text=${encodeURIComponent(shareText(g))}`, '_blank', 'noopener')}>{g.audience === 'team' ? 'Send to a rep on WhatsApp' : 'Send on WhatsApp'}</Button>
                    <Button size="sm" variant="ghost" disabled={!g.active} onClick={() => window.open(g.url, '_blank', 'noopener')}>Open</Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(g)}>Edit</Button>
                    <Button size="sm" variant="ghost" onClick={() => act(() => s.save(g.id, { active: !g.active }), g.active ? 'Turned off. The link no longer opens.' : 'Live again.')}>{g.active ? 'Turn off' : 'Turn on'}</Button>
                    <Button size="sm" variant="ghost" onClick={async () => {
                      if (await confirmDialog('Make a new link? The old one stops working. Send the new one to whoever should still have it.'))
                        act(() => s.renew(g.id), 'New link made.')
                    }}>New link</Button>
                    <Button size="sm" variant="ghost" onClick={async () => {
                      if (await confirmDialog('Delete this guide? Its link stops working. Your products are not changed.'))
                        act(() => s.remove(g.id), 'Deleted.')
                    }}>Delete</Button>
                  </div>
                </Card></li>
              ))}
            </ul>
          )}
        </>
      )}
      {editing && (
        <GuideForm g={editing === 'new' ? null : editing} cats={s.cats} products={s.products}
          onSave={(body) => s.save(editing === 'new' ? null : editing.id, body)} onClose={() => setEditing(null)} />
      )}
    </div>
  )
}
