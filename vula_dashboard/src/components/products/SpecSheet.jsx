/**
 * SpecSheet.jsx — Products › a product › Spec sheet (25 Nov 2026, migration 310). What product
 * guides show of the product (Sell › Product guides): a grade line, benefits, specs as
 * "Label: value" lines, a link to the maker's page. Saved on its own button
 * (PATCH /admin/products/{id} {specs}); the server cleans it and says what it can't keep. Loaded
 * only when a product is opened (lazy), so the app's first load stays the same size.
 */
import { useState } from 'react'
import { Button, Field, Input, Textarea } from '../ui/index.jsx'
import { VULA_API } from '../../lib/authFetch'
import '../guides/guides.css'

const lines = (rows) => (rows || []).map((r) => `${r[0]}: ${r[1]}`).join('\n')

export default function SpecSheet({ tenantId, product, onSaved }) {
  const s = product.specs || {}
  const [grade, setGrade] = useState(s.grade || '')
  const [benefits, setBenefits] = useState((s.benefits || []).join('\n'))
  const [rows, setRows] = useState(lines(s.rows))
  const [link, setLink] = useState(s.link || '')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  async function save() {
    setBusy(true)
    setMsg(null)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/products/${product.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ specs: { grade, benefits, rows, link: link.trim() } }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : "That didn't save. Try again.")
      const saved = d.specs || {}
      setGrade(saved.grade || ''); setBenefits((saved.benefits || []).join('\n')); setRows(lines(saved.rows)); setLink(saved.link || '')
      onSaved?.(d)
      setMsg({ ok: true, text: d.specs ? 'Saved. Product guides show it now.' : 'Cleared. This product has no spec sheet.' })
    } catch (x) {
      setMsg({ ok: false, text: x.message })
    }
    setBusy(false)
  }

  return (
    <div className="vgd-spec">
      <div className="vgd-spec-grid">
        <Field label="Grade or use" hint="A short line, e.g. Grade 33 · Commercial">
          <Input value={grade} onChange={(e) => setGrade(e.target.value)} maxLength={80} />
        </Field>
        <Field label="Maker's page" hint="Optional, https://…">
          <Input type="url" inputMode="url" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://" />
        </Field>
      </div>
      <Field label="Benefits" hint="One per line, up to 8.">
        <Textarea value={benefits} onChange={(e) => setBenefits(e.target.value)} rows={3} placeholder={'Stain resistant\nSuitable for heavy traffic'} />
      </Field>
      <Field label="Specs" hint="One per line as Label: value. A Colours line becomes the colour picker when the product has no colour options.">
        <Textarea value={rows} onChange={(e) => setRows(e.target.value)} rows={5} placeholder={'Width: 2 m\nTotal thickness: 2 mm\nWear layer: 0.7 mm\nColours: Oak, Ash, Slate'} />
      </Field>
      <div className="vgd-row">
        <Button type="button" size="sm" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save spec sheet'}</Button>
        {msg && <p className={msg.ok ? 'vgd-sub' : 'vgd-err'} role="status">{msg.text}</p>}
      </div>
    </div>
  )
}
