/**
 * VulaCosting.jsx — Sell › Costing: what it actually costs to make each product, and the margin.
 * (Vula Manufacturing Core, phases 2–3; backend vula_mind/vula/api/manufacturing.py.)
 *
 * The business sets itself up — materials and what they cost, a recipe per product, each batch
 * it makes — and Vula works out the cost per unit: materials at their average cost + labour +
 * overhead, over the good units (rejects make each good one dearer). The same steps work on
 * WhatsApp ("made 12 basins, 1 reject, 6 hours"). Every figure comes from the server.
 */
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, EmptyState, Field, Input, PageHeader, SectionTabs, Select, Skeleton, Table, confirmDialog, toast } from './ui/index.jsx'
import { VULA_API } from '../lib/authFetch'

const R = c => (c == null ? '—' : `R${(c / 100).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
const UNITS = ['kg', 'g', 'l', 'ml', 'each', 'm', 'm2']
const RULES = [
  ['none', 'No overhead'], ['per_labour_hour', 'Per labour hour'], ['per_run', 'Per batch'], ['per_unit', 'Per unit made'],
]

function perUnit(m) {
  if (m.unit_cost == null) return null
  const c = Number(m.unit_cost)
  if (m.base_unit === 'g') return `R${(c * 1000).toFixed(2)} / kg`
  if (m.base_unit === 'ml') return `R${(c * 1000).toFixed(2)} / l`
  return `R${c.toFixed(2)} ${m.base_unit === 'each' ? 'each' : '/ ' + m.base_unit}`
}
function showQty(q, base) {
  const n = Number(q)
  if (base === 'g' && Math.abs(n) >= 1000) return `${+(n / 1000).toFixed(3)} kg`
  if (base === 'ml' && Math.abs(n) >= 1000) return `${+(n / 1000).toFixed(3)} l`
  return `${+n.toFixed(3)}${base === 'each' ? '' : ' ' + base}`
}

export default function VulaCosting({ tenantId }) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/mfg`
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('sheets')
  const load = useCallback(() => fetch(`${base}/overview`).then(r => r.ok ? r.json() : Promise.reject())
    .then(setData).catch(() => setData({ ready: false, error: true })), [base])
  useEffect(() => { load() }, [load])

  async function call(path, method, body) {
    const r = await fetch(`${base}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d.detail || 'That didn\'t work.')
    return d
  }

  if (!data) return <Card><Skeleton height={120} /></Card>
  if (!data.ready) return (
    <EmptyState icon="🏭" title="Costing isn't set up on this server yet">
      {data.error ? 'Could not load costing.' : 'The costing tables are being added — check back shortly.'}
    </EmptyState>)

  const tabs = [
    { id: 'sheets', label: `Cost sheets (${data.recipes.length})` },
    { id: 'materials', label: `Materials (${data.materials.length})` },
    { id: 'batches', label: 'Batches' },
    { id: 'settings', label: 'Labour & overhead' },
  ]
  return (
    <div>
      <PageHeader title="Costing" sub="What each product really costs to make — materials, labour and overhead over the good units — and your margin." />
      <SectionTabs tabs={tabs} active={tab} onChange={setTab} />
      {tab === 'sheets' && <Sheets data={data} call={call} reload={load} />}
      {tab === 'materials' && <Materials data={data} call={call} reload={load} base={base} />}
      {tab === 'batches' && <Batches data={data} call={call} reload={load} />}
      {tab === 'settings' && <Settings data={data} call={call} reload={load} />}
      <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 14 }}>
        Tip: all of this works on WhatsApp too — send <b>set up costing</b> and Vula asks one question at a time, using your invoices and bank statements. Or just say "got 2 bags concrete mix for R190", "made 12 round basins, 1 reject, 6 hours", "what does a round basin cost us?".
      </p>
    </div>
  )
}

// ── Cost sheets + recipe editor ─────────────────────────────────────────────────────────
function Sheets({ data, call, reload }) {
  const [editing, setEditing] = useState(null)   // product id
  if (editing) return <RecipeEditor data={data} call={call} productId={editing} onDone={() => { setEditing(null); reload() }} />
  const withRecipe = new Set(data.recipes.map(s => String(s.product_id)))
  const without = data.products.filter(p => !withRecipe.has(String(p.id)))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {data.recipes.length === 0 && (
        <EmptyState icon="🧾" title="No recipes yet">
          Add what goes into one batch of a product, and Vula works out what each one costs.
        </EmptyState>)}
      {data.recipes.map(s => <SheetCard key={s.product_id} s={s} onEdit={() => setEditing(s.product_id)} />)}
      {without.length > 0 && (
        <Card>
          <Field label="Add a recipe for">
            <Select defaultValue="" onChange={e => e.target.value && setEditing(e.target.value)}>
              <option value="">Choose a product…</option>
              {without.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          </Field>
        </Card>)}
    </div>
  )
}

function SheetCard({ s, onEdit }) {
  const e = s.expected, m = s.margin
  const tone = m.margin_pct == null ? 'muted' : m.margin_pct < 20 ? 'danger' : m.margin_pct < 35 ? 'warn' : 'ok'
  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 16 }}>{s.product}</b>
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>recipe v{s.recipe_version} · one batch makes {+e.good}</span>
        <span style={{ marginLeft: 'auto', fontSize: 20, fontWeight: 700 }}>{R(e.unit_cost_cents)} <span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 400 }}>each</span></span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, margin: '10px 0' }}>
        <Stat label="Materials" v={R(e.materials_cents)} /><Stat label="Labour" v={R(e.labour_cents)} />
        <Stat label="Overhead" v={R(e.overhead_cents)} />
        <Stat label={`Margin${s.vat_registered ? ' (excl. VAT)' : ''}`} v={m.margin_cents == null ? 'No price' : `${R(m.margin_cents)} · ${m.margin_pct}%`} tone={tone} />
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text)' }}>
        {e.lines.map(l => (
          <div key={l.material_id} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
            <span style={{ flex: 1 }}>{l.name}</span><span style={{ color: 'var(--muted)' }}>{showQty(l.qty_base, l.base_unit)}</span>
            <span style={{ width: 90, textAlign: 'right' }}>{l.cost_cents == null ? <Badge tone="warn">no price</Badge> : R(l.cost_cents)}</span>
          </div>))}
      </div>
      {e.missing_costs.length > 0 && <p style={{ fontSize: 12, color: 'var(--warn)', margin: '6px 0 0' }}>Not costed yet — no price for {e.missing_costs.join(', ')}. Book a delivery for each to complete it.</p>}
      {s.runs.length > 0 && <p style={{ fontSize: 12, color: 'var(--muted)', margin: '8px 0 0' }}>Last batches: {s.runs.filter(r => r.unit_cost_cents != null).slice(0, 5).map(r => R(r.unit_cost_cents)).join(' · ')}</p>}
      <div style={{ marginTop: 10 }}><Button variant="soft" size="sm" onClick={onEdit}>Edit recipe</Button></div>
    </Card>
  )
}

function Stat({ label, v, tone }) {
  return (
    <div style={{ background: 'var(--surface-alt)', borderRadius: 10, padding: '8px 10px' }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.04em' }}>{label}</div>
      <div style={{ fontWeight: 700, color: tone ? `var(--${tone})` : 'var(--text)' }}>{v}</div>
    </div>)
}

function RecipeEditor({ data, call, productId, onDone }) {
  const product = data.products.find(p => String(p.id) === String(productId))
  const [makes, setMakes] = useState('1')
  const [hours, setHours] = useState('0')
  const [lines, setLines] = useState([{ material: '', qty: '', unit: 'kg', waste_pct: '' }])
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    // Start from the recipe on file, so editing changes it rather than retyping it.
    let live = true
    call(`/recipes/${encodeURIComponent(productId)}`, 'GET').then(d => {
      const bom = d.recipe
      if (!live || !bom) return
      setMakes(String(+bom.batch_yield)); setHours(String(+bom.labour_hours || 0))
      const ls = (bom.lines || []).filter(l => l.material)
        .map(l => ({ material: l.material, qty: String(+l.qty), unit: l.unit || 'kg', waste_pct: +l.waste_pct ? String(+l.waste_pct) : '' }))
      if (ls.length) setLines(ls)
    }).catch(() => {})
    return () => { live = false }
  }, [productId])  // eslint-disable-line
  const set = (i, k, v) => setLines(ls => ls.map((l, j) => j === i ? { ...l, [k]: v } : l))
  async function save() {
    const ingredients = lines.filter(l => l.material.trim() && l.qty !== '')
      .map(l => ({ material: l.material.trim(), qty: Number(l.qty), unit: l.unit, waste_pct: l.waste_pct === '' ? 0 : Number(l.waste_pct) }))
    if (!ingredients.length) { toast('Add at least one material', 'danger'); return }
    setBusy(true)
    try {
      await call('/recipes', 'POST', { product_id: productId, makes: Number(makes) || 1, labour_hours: Number(hours) || 0, ingredients })
      toast('Recipe saved'); onDone()
    } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  return (
    <Card>
      <b style={{ fontSize: 16 }}>Recipe for {product?.name}</b>
      <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '4px 0 12px' }}>What goes into one batch. Saving makes a new version — batches already made keep the recipe they used. A material that isn't listed yet is added without a price.</p>
      <datalist id="vula-materials">{data.materials.map(m => <option key={m.id} value={m.name} />)}</datalist>
      {lines.map((l, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: 'minmax(140px,2fr) minmax(70px,1fr) minmax(70px,1fr) minmax(70px,1fr) auto', gap: 6, marginBottom: 6 }}>
          <Input list="vula-materials" placeholder="Material" value={l.material} onChange={e => set(i, 'material', e.target.value)} />
          <Input type="number" step="any" min="0" placeholder="Qty" value={l.qty} onChange={e => set(i, 'qty', e.target.value)} />
          <Select value={l.unit} onChange={e => set(i, 'unit', e.target.value)}>{UNITS.map(u => <option key={u}>{u}</option>)}<option value="bag">bag</option></Select>
          <Input type="number" step="any" min="0" placeholder="Waste %" value={l.waste_pct} onChange={e => set(i, 'waste_pct', e.target.value)} />
          <Button variant="ghost" size="sm" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))}>✕</Button>
        </div>))}
      <Button variant="ghost" size="sm" onClick={() => setLines(ls => [...ls, { material: '', qty: '', unit: 'kg', waste_pct: '' }])}>+ Add material</Button>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, marginTop: 12 }}>
        <Field label="One batch makes (good units)"><Input type="number" step="any" min="0" value={makes} onChange={e => setMakes(e.target.value)} /></Field>
        <Field label="Labour hours per batch"><Input type="number" step="any" min="0" value={hours} onChange={e => setHours(e.target.value)} /></Field>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <Button disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save recipe'}</Button>
        <Button variant="soft" onClick={onDone}>Cancel</Button>
      </div>
    </Card>
  )
}

// ── Materials ───────────────────────────────────────────────────────────────────────────
function Materials({ data, call, reload }) {
  const [add, setAdd] = useState({ name: '', unit: 'kg', pack_size: '', pack_unit: 'kg' })
  const [rec, setRec] = useState({ material: '', qty: '', unit: 'kg', total: '' })
  const [pricing, setPricing] = useState(null)
  const [busy, setBusy] = useState(false)
  async function addMaterial() {
    if (!add.name.trim()) return
    setBusy(true)
    try {
      await call('/materials', 'POST', { name: add.name.trim(), unit: add.unit,
        pack_size: add.pack_size === '' ? null : Number(add.pack_size), pack_unit: add.pack_size === '' ? null : add.pack_unit })
      toast(`${add.name.trim()} added`); setAdd({ name: '', unit: 'kg', pack_size: '', pack_unit: 'kg' }); reload()
    } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  async function receive() {
    if (!rec.material || rec.qty === '' || rec.total === '') { toast('Material, amount and cost, please', 'danger'); return }
    setBusy(true)
    try {
      const d = await call('/materials/receive', 'POST', { material: rec.material, qty: Number(rec.qty), unit: rec.unit,
        total_cents: Math.round(Number(String(rec.total).replace(/[R,\s]/g, '')) * 100) })
      toast(d.message || 'Booked in'); setRec({ material: '', qty: '', unit: 'kg', total: '' }); reload()
    } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Table rowKey="id" rows={data.materials} empty="No materials yet — add the first below."
        columns={[
          { key: 'name', label: 'Material' },
          { key: 'stock', label: 'On hand', align: 'right', render: m => <span style={{ color: Number(m.stock_qty) < 0 ? 'var(--danger)' : undefined }}>{showQty(m.stock_qty, m.base_unit)}</span> },
          { key: 'cost', label: 'Average cost', align: 'right', render: m => perUnit(m) || <Badge tone="warn">no price yet</Badge> },
          { key: 'pack', label: 'Pack', render: m => m.pack_size ? `${+m.pack_size} ${m.pack_unit}` : '—' },
          { key: 'find', label: '', align: 'right', render: m => (
            <Button variant={m.unit_cost == null ? 'soft' : 'ghost'} size="sm" onClick={() => setPricing(m)}>
              {m.unit_cost == null ? 'Find price' : 'Change price'}</Button>) },
        ]} />
      {pricing && <PriceFinder m={pricing} call={call} onDone={() => { setPricing(null); reload() }} />}
      <Card>
        <b>Book a delivery</b>
        <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '2px 0 10px' }}>What arrived and what it cost in total (excluding VAT you can claim back). The average cost updates.</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8 }}>
          <Select value={rec.material} onChange={e => setRec({ ...rec, material: e.target.value })}>
            <option value="">Material…</option>{data.materials.map(m => <option key={m.id} value={m.name}>{m.name}</option>)}</Select>
          <Input type="number" step="any" min="0" placeholder="Amount" value={rec.qty} onChange={e => setRec({ ...rec, qty: e.target.value })} />
          <Select value={rec.unit} onChange={e => setRec({ ...rec, unit: e.target.value })}>{UNITS.map(u => <option key={u}>{u}</option>)}<option value="bag">bag</option><option value="pack">pack</option></Select>
          <Input placeholder="Total cost R" value={rec.total} onChange={e => setRec({ ...rec, total: e.target.value })} />
          <Button disabled={busy} onClick={receive}>Book in</Button>
        </div>
      </Card>
      <Card>
        <b>Add a material</b>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8, marginTop: 8 }}>
          <Input placeholder="Name, e.g. Concrete mix" value={add.name} onChange={e => setAdd({ ...add, name: e.target.value })} />
          <Field label="Measured in"><Select value={add.unit} onChange={e => setAdd({ ...add, unit: e.target.value })}>{UNITS.map(u => <option key={u}>{u}</option>)}</Select></Field>
          <Field label="Pack size (optional)"><Input type="number" step="any" min="0" placeholder="e.g. 25" value={add.pack_size} onChange={e => setAdd({ ...add, pack_size: e.target.value })} /></Field>
          <Field label="Pack unit"><Select value={add.pack_unit} onChange={e => setAdd({ ...add, pack_unit: e.target.value })}>{UNITS.map(u => <option key={u}>{u}</option>)}</Select></Field>
          <Button disabled={busy} onClick={addMaterial}>Add</Button>
        </div>
      </Card>
    </div>
  )
}

// ── Batches ─────────────────────────────────────────────────────────────────────────────
function Batches({ data, call, reload }) {
  const [f, setF] = useState({ product_id: '', good: '', rejects: '', labour_hours: '' })
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const recipes = data.recipes
  const body = () => ({ product_id: f.product_id, good: Number(f.good), rejects: f.rejects === '' ? 0 : Number(f.rejects),
    labour_hours: f.labour_hours === '' ? null : Number(f.labour_hours) })
  async function doPreview() {
    if (!f.product_id || f.good === '') { toast('Product and good units, please', 'danger'); return }
    setBusy(true)
    try { setPreview(await call('/batches/preview', 'POST', body())) } catch (e) { toast(e.message, 'danger'); setPreview(null) }
    setBusy(false)
  }
  async function log() {
    setBusy(true)
    try { const d = await call('/batches', 'POST', body()); toast('Batch logged'); setPreview(null); setF({ product_id: '', good: '', rejects: '', labour_hours: '' }); reload(); if (d.message) toast(d.message.split('\n')[1] || 'Logged') }
    catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  async function reverse(run) {
    if (!(await confirmDialog(`Reverse this batch of ${+run.good_qty} × ${run.product}? Materials go back to stock and the units come off.`))) return
    try { await call(`/batches/${run.id}/reverse`, 'POST', {}); toast('Batch reversed'); reload() } catch (e) { toast(e.message, 'danger') }
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Card>
        <b>Log a batch</b>
        {recipes.length === 0 ? <p style={{ fontSize: 13, color: 'var(--muted)' }}>Add a recipe first (Cost sheets).</p> : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8, marginTop: 8 }}>
            <Select value={f.product_id} onChange={e => { setF({ ...f, product_id: e.target.value }); setPreview(null) }}>
              <option value="">Product…</option>{recipes.map(s => <option key={s.product_id} value={s.product_id}>{s.product}</option>)}</Select>
            <Input type="number" step="any" min="0" placeholder="Good units" value={f.good} onChange={e => { setF({ ...f, good: e.target.value }); setPreview(null) }} />
            <Input type="number" step="any" min="0" placeholder="Rejects" value={f.rejects} onChange={e => { setF({ ...f, rejects: e.target.value }); setPreview(null) }} />
            <Input type="number" step="any" min="0" placeholder="Hours (optional)" value={f.labour_hours} onChange={e => { setF({ ...f, labour_hours: e.target.value }); setPreview(null) }} />
            <Button variant="soft" disabled={busy} onClick={doPreview}>Work out the cost</Button>
          </div>)}
        {preview && (
          <div style={{ marginTop: 12, padding: 12, background: 'var(--surface-alt)', borderRadius: 10 }}>
            <div style={{ fontSize: 13 }}>Materials {preview.materials} · labour {preview.labour} · overhead {preview.overhead}</div>
            <div style={{ fontSize: 18, fontWeight: 700, margin: '4px 0 8px' }}>{preview.cost_per_unit} each</div>
            <Button disabled={busy} onClick={log}>Log this batch</Button>
          </div>)}
      </Card>
      <Table rowKey="id" rows={data.runs} empty="No batches logged yet."
        columns={[
          { key: 'at', label: 'When', render: r => String(r.confirmed_at || r.created_at || '').slice(0, 16).replace('T', ' ') },
          { key: 'product', label: 'Product' },
          { key: 'good', label: 'Good', align: 'right', render: r => +r.good_qty },
          { key: 'rej', label: 'Rejects', align: 'right', render: r => +r.reject_qty || '—' },
          { key: 'unit', label: 'Cost each', align: 'right', render: r => r.unit_cost == null ? '—' : R(Math.round(Number(r.unit_cost) * 100)) },
          { key: 'status', label: 'Status', render: r => <Badge tone={r.status === 'confirmed' ? 'ok' : r.status === 'reversed' ? 'muted' : 'warn'}>{r.status}</Badge> },
          { key: 'act', label: '', render: r => r.status === 'confirmed' ? <Button variant="ghost" size="sm" onClick={() => reverse(r)}>Reverse</Button> : null },
        ]} />
    </div>
  )
}

// ── Labour & overhead ───────────────────────────────────────────────────────────────────
// What past supplier invoices say a material costs — the owner picks one, or types what they pay.
function PriceFinder({ m, call, onDone }) {
  const [sug, setSug] = useState(null)
  const [own, setOwn] = useState({ total: '', qty: '', unit: m.base_unit === 'g' ? 'kg' : m.base_unit === 'ml' ? 'l' : m.base_unit })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    call(`/materials/${m.id}/suggest`, 'GET').then(d => setSug(d.suggestions || [])).catch(() => setSug([]))
  }, [m.id])  // eslint-disable-line
  async function save(body) {
    setBusy(true)
    try { const d = await call(`/materials/${m.id}/price`, 'POST', body); toast(d.message || 'Price set'); onDone() }
    catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  function saveOwn() {
    const total = Number(String(own.total).replace(/[R,\s]/g, ''))
    if (!total || !Number(own.qty)) { toast('Price and amount, please', 'danger'); return }
    save({ total_cents: Math.round(total * 100), qty: Number(own.qty), unit: own.unit })
  }
  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <b>Price for {m.name}</b><Button variant="ghost" size="sm" onClick={onDone}>Close</Button>
      </div>
      <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '2px 0 10px' }}>From your supplier invoices — VAT is taken off or left on depending on how each invoice was priced and whether you're VAT registered. Pick the right item, or type what you pay.</p>
      {sug == null ? <Skeleton height={60} /> : sug.length === 0
        ? <p style={{ fontSize: 13 }}>Nothing like {m.name} on your invoices yet.</p>
        : sug.map((x, i) => (
          <div key={x.key} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
            <div style={{ flex: 1, fontSize: 13 }}>
              <b>{x.description}</b> — {R(x.invoice_price_cents)}
              <div style={{ color: 'var(--muted)', fontSize: 12 }}>{[x.supplier, x.latest_on].filter(Boolean).join(' · ')}{x.observations > 1 ? ` · ${x.observations} times, ${R(x.low_cents)}–${R(x.high_cents)}` : ''}{x.vat_note ? ` · ${x.vat_note}` : ''}</div>
            </div>
            <span style={{ fontWeight: 700, whiteSpace: 'nowrap' }}>{x.per_unit}</span>
            <Button size="sm" disabled={busy} onClick={() => save({ pick: i + 1 })}>Use</Button>
          </div>))}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, marginTop: 12 }}>
        <Input placeholder="Price R" value={own.total} onChange={e => setOwn({ ...own, total: e.target.value })} />
        <Input type="number" step="any" min="0" placeholder="for how much" value={own.qty} onChange={e => setOwn({ ...own, qty: e.target.value })} />
        <Select value={own.unit} onChange={e => setOwn({ ...own, unit: e.target.value })}>{UNITS.map(u => <option key={u}>{u}</option>)}{m.pack_size ? <option value="pack">pack</option> : null}</Select>
        <Button variant="soft" disabled={busy} onClick={saveOwn}>Set my price</Button>
      </div>
    </Card>
  )
}

// Labour and running costs from the bank, turned into a rate for the counts the owner gives.
function BankSuggest({ call, onUse }) {
  const [q, setQ] = useState({ hours: '', batches: '', units: '' })
  const [b, setB] = useState(null)
  const look = useCallback(() => {
    const p = new URLSearchParams()
    if (q.hours) p.set('hours_per_week', q.hours)
    if (q.batches) p.set('batches_per_month', q.batches)
    if (q.units) p.set('units_per_month', q.units)
    call(`/settings/suggest?${p}`, 'GET').then(setB).catch(() => setB({ error: true }))
  }, [q, call])
  useEffect(() => { look() }, [])  // eslint-disable-line
  if (!b) return <Skeleton height={60} />
  if (b.error) return null
  if (!b.labour_monthly_cents && !b.overhead_monthly_cents) return (
    <p style={{ fontSize: 12.5, color: 'var(--muted)' }}>No wages or running costs on your bank statements yet — upload a statement in Finances and I'll suggest these.</p>)
  return (
    <div style={{ background: 'var(--surface-alt)', borderRadius: 10, padding: 12, marginBottom: 14 }}>
      <b style={{ fontSize: 13.5 }}>From your bank (since {b.since})</b>
      <p style={{ fontSize: 13, margin: '6px 0' }}>
        {b.labour_monthly_cents ? <>Wages & casual labour about <b>{R(b.labour_monthly_cents)}</b> a month. </> : null}
        {b.overhead_monthly_cents ? <>Running costs about <b>{R(b.overhead_monthly_cents)}</b> a month ({b.overhead_by_account.slice(0, 5).map(a => `${a.label} ${R(a.monthly_cents)}`).join(' · ')}).</> : null}
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
        <Field label="Team hours a week (all)"><Input type="number" min="0" value={q.hours} onChange={e => setQ({ ...q, hours: e.target.value })} /></Field>
        <Field label="Batches a month"><Input type="number" min="0" value={q.batches} onChange={e => setQ({ ...q, batches: e.target.value })} /></Field>
        <Field label="Units made a month"><Input type="number" min="0" value={q.units} onChange={e => setQ({ ...q, units: e.target.value })} /></Field>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8, alignItems: 'center' }}>
        <Button variant="ghost" size="sm" onClick={look}>Work it out</Button>
        {b.labour_rate_cents != null && <Button variant="soft" size="sm" onClick={() => onUse({ labour: b.labour_rate_cents })}>Labour {R(b.labour_rate_cents)}/h</Button>}
        {b.overhead_per_hour_cents != null && <Button variant="soft" size="sm" onClick={() => onUse({ rule: 'per_labour_hour', rate: b.overhead_per_hour_cents })}>Overhead {R(b.overhead_per_hour_cents)}/labour hour</Button>}
        {b.overhead_per_batch_cents != null && <Button variant="soft" size="sm" onClick={() => onUse({ rule: 'per_run', rate: b.overhead_per_batch_cents })}>Overhead {R(b.overhead_per_batch_cents)}/batch</Button>}
        {b.overhead_per_unit_cents != null && <Button variant="soft" size="sm" onClick={() => onUse({ rule: 'per_unit', rate: b.overhead_per_unit_cents })}>Overhead {R(b.overhead_per_unit_cents)}/unit</Button>}
      </div>
    </div>)
}

function Settings({ data, call, reload }) {
  const s = data.settings || {}
  const [f, setF] = useState({ labour: s.labour_rate_cents ? (s.labour_rate_cents / 100).toString() : '',
    rule: s.overhead_rule || 'none', rate: s.overhead_rate_cents ? (s.overhead_rate_cents / 100).toString() : '' })
  const [busy, setBusy] = useState(false)
  async function save() {
    setBusy(true)
    try {
      const d = await call('/settings', 'PUT', { labour_rate_cents: Math.round(Number(f.labour || 0) * 100),
        overhead_rule: f.rule, overhead_rate_cents: Math.round(Number(f.rate || 0) * 100) })
      toast(d.message || 'Saved'); reload()
    } catch (e) { toast(e.message, 'danger') }
    setBusy(false)
  }
  const rateLabel = { per_labour_hour: 'Overhead per labour hour (R)', per_run: 'Overhead per batch (R)', per_unit: 'Overhead per unit made (R)' }[f.rule]
  return (
    <Card>
      <p style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 0 }}>Labour is costed at this hourly rate. Overheads (rent, power, tooling wear) are shared by the one rule you pick — it shows on every cost sheet.</p>
      <BankSuggest call={call} onUse={u => setF(cur => ({ ...cur,
        ...(u.labour != null ? { labour: (u.labour / 100).toFixed(2) } : {}),
        ...(u.rule ? { rule: u.rule, rate: (u.rate / 100).toFixed(2) } : {}) }))} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
        <Field label="Labour rate per hour (R)"><Input type="number" step="any" min="0" value={f.labour} onChange={e => setF({ ...f, labour: e.target.value })} /></Field>
        <Field label="Overhead rule"><Select value={f.rule} onChange={e => setF({ ...f, rule: e.target.value })}>{RULES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        {f.rule !== 'none' && <Field label={rateLabel}><Input type="number" step="any" min="0" value={f.rate} onChange={e => setF({ ...f, rate: e.target.value })} /></Field>}
      </div>
      <div style={{ marginTop: 12 }}><Button disabled={busy} onClick={save}>Save</Button></div>
    </Card>
  )
}
