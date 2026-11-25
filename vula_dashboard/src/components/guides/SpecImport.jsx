/**
 * SpecImport.jsx — Sell › Product guides › Read spec sheets from a catalogue (25 Nov 2026,
 * vula/commerce/spec_import.py). The business sends its maker's catalogue PDF; Vula reads it in
 * the background and proposes a spec sheet per product, keeping only what is printed in the
 * catalogue (the rest is listed as set aside, with why). Each one is matched to one of the
 * business's products by name, or the person picks one, or adds it as a new draft product (no
 * price). Nothing changes until "Keep the ticked ones"; each is read back on the server.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, Select, StatusChip, toast } from '../ui/index.jsx'
import { VULA_API } from '../../lib/authFetch'

const POLL_MS = 3000

export default function SpecImport({ tenantId, products, cats, onApplied }) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/product-guides/spec-import`
  const [job, setJob] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [picks, setPicks] = useState({})      // key -> {on, target: product id | 'new', category}
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  async function poll(id) {
    try {
      const r = await fetch(`${base}/${id}`)
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'The catalogue read went missing.')
      setJob(d)
      if (d.status === 'reading') timer.current = setTimeout(() => poll(id), POLL_MS)
      else if (d.status === 'ready') {
        const next = {}
        for (const it of d.result?.items || []) {
          if (!it.ok || (d.applied || []).includes(it.key)) continue
          next[it.key] = { on: !!it.product_id, target: it.product_id || '', category: cats[0]?.key || 'general' }
        }
        setPicks(next)
      }
    } catch (x) {
      setErr(x.message)
    }
  }

  async function send(file) {
    if (!file) return
    setErr('')
    setJob(null)
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch(base, { method: 'POST', body: fd })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : "That catalogue couldn't be read.")
      setJob({ ...d, pages_read: 0, result: {} })
      timer.current = setTimeout(() => poll(d.id), 1500)
    } catch (x) {
      setErr(x.message)
    }
    setBusy(false)
  }

  async function keep() {
    const list = Object.entries(picks).filter(([, p]) => p.on && p.target)
      .map(([key, p]) => (p.target === 'new' ? { key, create: { category: p.category } } : { key, product_id: p.target }))
    if (!list.length) return
    setBusy(true)
    try {
      const r = await fetch(`${base}/${job.id}/apply`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ picks: list }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : "That didn't save. Try again.")
      const made = d.done.filter((x) => x.created).length
      toast(`${d.done.length} spec sheet${d.done.length === 1 ? '' : 's'} saved${made ? `, ${made} as new draft products (set their price and status in Products)` : ''}.`
        + (d.refused.length ? ` ${d.refused.length} not saved.` : ''), d.refused.length ? 'warn' : 'ok')
      onApplied?.()
      await poll(job.id)
    } catch (x) {
      toast(x.message, 'danger')
    }
    setBusy(false)
  }

  const items = useMemo(() => (job?.result?.items || []), [job])
  const ready = job?.status === 'ready'
  const ticked = Object.values(picks).filter((p) => p.on && p.target).length
  const set = (key, patch) => setPicks((x) => ({ ...x, [key]: { ...x[key], ...patch } }))

  return (
    <Card className="vgd-imp">
      <h2 className="vgd-h">Read spec sheets from a catalogue</h2>
      <p className="vgd-sub">Send your maker's catalogue as a PDF. Vula keeps only what is printed in it and never reads prices. You choose what to keep.</p>
      <label className="vgd-file">
        <input type="file" accept="application/pdf,.pdf" disabled={busy || job?.status === 'reading'}
          onChange={(e) => { send(e.target.files?.[0]); e.target.value = '' }} />
        <span>{job?.status === 'reading' ? 'Reading…' : 'Choose a catalogue PDF'}</span>
      </label>
      {err && <p className="vgd-err" role="alert">{err}</p>}

      {job && job.status === 'reading' && (
        <p className="vgd-sub" role="status">Reading {job.file_name}: page {job.pages_read || 0} of {job.pages}. You can leave this screen; it carries on.</p>
      )}
      {job?.status === 'failed' && <p className="vgd-err" role="alert">{job.error}</p>}

      {ready && (
        <>
          <p className="vgd-sub" role="status">
            {items.filter((i) => i.ok).length} products found in {job.file_name} ({job.pages} pages).
            {(job.result?.notes || []).map((n) => ` ${n}`).join('')}
          </p>
          <ul className="vgd-found">
            {items.map((it) => {
              const p = picks[it.key]
              const done = (job.applied || []).includes(it.key)
              return (
                <li key={it.key} className={it.ok ? '' : 'vgd-dim'}>
                  <div className="vgd-row vgd-top">
                    {it.ok && !done && (
                      <input type="checkbox" aria-label={`Keep ${it.name}`} checked={!!p?.on} onChange={(e) => set(it.key, { on: e.target.checked })} />
                    )}
                    <strong className="vgd-h">{it.name}</strong>
                    <span className="vgd-sub">p. {it.pages}</span>
                    {done && <StatusChip tone="ok">Saved</StatusChip>}
                    {!it.ok && <StatusChip>Nothing printed to keep</StatusChip>}
                  </div>
                  {it.ok && (
                    <>
                      <p className="vgd-sub">
                        {[it.grade, ...(it.rows || []).map((r) => `${r[0]}: ${r[1]}`)].filter(Boolean).join(' · ')}
                        {it.benefits?.length ? ` · ${it.benefits.join(', ')}` : ''}
                      </p>
                      {(it.flags || []).map((f) => <p key={f} className="vgd-warn">{f}</p>)}
                      {!done && (
                        <div className="vgd-row">
                          <Select value={p?.target || ''} aria-label={`Which product is ${it.name}`} style={{ maxWidth: 320 }}
                            onChange={(e) => set(it.key, { target: e.target.value, on: !!e.target.value })}>
                            <option value="">Which of your products is it?</option>
                            {it.candidates?.length > 0 && (
                              <optgroup label="Closest names">{it.candidates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</optgroup>
                            )}
                            <optgroup label="All your products">{products.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</optgroup>
                            <option value="new">Add it as a new product (draft, no price)</option>
                          </Select>
                          {p?.target === 'new' && (
                            <Select value={p.category} aria-label="Category for the new product" style={{ maxWidth: 220 }}
                              onChange={(e) => set(it.key, { category: e.target.value })}>
                              {(cats.length ? cats : [{ key: 'general', label: 'General' }]).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                            </Select>
                          )}
                          {it.how && it.how !== 'none' && p?.target === it.product_id && <span className="vgd-sub">matched by {it.how}</span>}
                        </div>
                      )}
                    </>
                  )}
                  {it.aside?.length > 0 && (
                    <details className="vgd-aside"><summary>{it.aside.length} set aside</summary>
                      <ul>{it.aside.map((a, i) => <li key={i}>{a}</li>)}</ul>
                    </details>
                  )}
                </li>
              )
            })}
          </ul>
          <div className="vgd-row">
            <Button onClick={keep} disabled={busy || !ticked}>{busy ? 'Saving…' : `Keep the ticked ones (${ticked})`}</Button>
            <span className="vgd-sub">A product's current spec sheet is replaced by the one you keep.</span>
          </div>
        </>
      )}
    </Card>
  )
}
