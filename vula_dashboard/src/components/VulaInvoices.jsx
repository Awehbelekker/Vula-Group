/**
 * VulaInvoices.jsx — Invoice management for a tenant.
 * Adapted from Awake SA admin/invoices, wired to Vula backend.
 *
 * - List invoices with status (draft/sent/paid/overdue)
 * - Create invoice (line items + VAT)
 * - Send via WhatsApp, mark paid, delete
 */

import { useState, useEffect, useCallback } from 'react'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const STATUS = {
  draft:     { label: 'Draft',     color: '#6b7280', bg: 'rgba(107,114,128,0.12)' },
  sent:      { label: 'Sent',      color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  paid:      { label: 'Paid',      color: '#16a34a', bg: 'rgba(34,197,94,0.12)' },
  overdue:   { label: 'Overdue',   color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  cancelled: { label: 'Cancelled', color: '#9ca3af', bg: 'rgba(156,163,175,0.12)' },
}

export default function VulaInvoices({ tenantId, products = [] }) {
  const [invoices, setInvoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices`)
    const d = await r.json()
    setInvoices(d.invoices || [])
    setLoading(false)
  }, [tenantId])

  useEffect(() => { load() }, [load])

  async function setStatus(inv, status) {
    const body = { status }
    if (status === 'paid') body.paid_at = new Date().toISOString()
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    load()
  }

  async function del(id) {
    if (!confirm('Delete this invoice?')) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${id}`, { method: 'DELETE' })
    load()
  }

  function sendWhatsApp(inv) {
    const phone = (inv.customer_phone || '').replace(/[^\d]/g, '').replace(/^0/, '27')
    const msg = `Hi ${inv.customer_name}, here's your invoice ${inv.invoice_number} for ${fmt(inv.total_cents)}. ` +
      (inv.due_date ? `Due ${inv.due_date}. ` : '') + `Thank you!`
    window.open(`https://wa.me/${phone}?text=${encodeURIComponent(msg)}`, '_blank')
    if (inv.status === 'draft') setStatus(inv, 'sent')
  }

  const fmt = c => `R${(c / 100).toFixed(2)}`

  if (showCreate) {
    return <InvoiceCreate tenantId={tenantId} products={products} onDone={() => { setShowCreate(false); load() }} onCancel={() => setShowCreate(false)} />
  }

  return (
    <div>
      <div style={s.topBar}>
        <p style={s.count}>{invoices.length} invoice{invoices.length !== 1 ? 's' : ''}</p>
        <button onClick={() => setShowCreate(true)} style={s.newBtn}>+ New invoice</button>
      </div>

      {loading ? <p style={s.muted}>Loading…</p> : invoices.length === 0 ? (
        <p style={s.muted}>No invoices yet. Create one, or scan an existing invoice with the Smart Scanner.</p>
      ) : (
        <div style={s.list}>
          {invoices.map(inv => {
            const st = STATUS[inv.status] || STATUS.draft
            return (
              <div key={inv.id} style={s.card}>
                <div style={s.cardTop}>
                  <div>
                    <span style={s.invNum}>{inv.invoice_number}</span>
                    <span style={{ ...s.badge, color: st.color, background: st.bg }}>{st.label}</span>
                  </div>
                  <span style={s.amount}>{fmt(inv.total_cents)}</span>
                </div>
                <p style={s.cust}>{inv.customer_name}{inv.customer_phone ? ` · ${inv.customer_phone}` : ''}</p>
                <p style={s.dates}>
                  Issued {inv.issue_date}{inv.due_date ? ` · Due ${inv.due_date}` : ''}
                </p>
                <div style={s.cardActions}>
                  {inv.customer_phone && <button onClick={() => sendWhatsApp(inv)} style={s.actWa}>💬 WhatsApp</button>}
                  {inv.status !== 'paid' && <button onClick={() => setStatus(inv, 'paid')} style={s.actPaid}>✓ Mark paid</button>}
                  <button onClick={() => del(inv.id)} style={s.actDel}>Delete</button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Create invoice form ─────────────────────────────────────────────────────

function InvoiceCreate({ tenantId, products, onDone, onCancel }) {
  const [customer, setCustomer] = useState({ name: '', phone: '', email: '', address: '' })
  const [items, setItems] = useState([{ description: '', quantity: 1, unit_price: '' }])
  const [dueDate, setDueDate] = useState('')
  const [vatRate, setVatRate] = useState(15)
  const [saving, setSaving] = useState(false)

  function updateItem(i, field, val) {
    setItems(items.map((it, idx) => idx === i ? { ...it, [field]: val } : it))
  }
  function addItem() { setItems([...items, { description: '', quantity: 1, unit_price: '' }]) }
  function removeItem(i) { setItems(items.filter((_, idx) => idx !== i)) }

  const lineItems = items.map(it => {
    const cents = Math.round((parseFloat(it.unit_price) || 0) * 100)
    return {
      description: it.description,
      quantity: parseFloat(it.quantity) || 0,
      unit_price_cents: cents,
      total_cents: Math.round(cents * (parseFloat(it.quantity) || 0)),
    }
  })
  const subtotal = lineItems.reduce((sum, i) => sum + i.total_cents, 0)
  const vat = Math.round(subtotal * vatRate / 100)
  const total = subtotal + vat
  const fmt = c => `R${(c / 100).toFixed(2)}`

  async function save() {
    setSaving(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        customer_name: customer.name, customer_phone: customer.phone,
        customer_email: customer.email, customer_address: customer.address,
        line_items: lineItems, vat_rate: vatRate,
        issue_date: new Date().toISOString().slice(0, 10),
        due_date: dueDate || null, status: 'draft',
      }),
    })
    setSaving(false)
    onDone()
  }

  return (
    <div>
      <div style={s.topBar}>
        <button onClick={onCancel} style={s.backBtn}>← Back</button>
        <h3 style={s.formTitle}>New invoice</h3>
      </div>

      <div style={s.formSection}>
        <input placeholder="Customer name" value={customer.name} onChange={e => setCustomer({ ...customer, name: e.target.value })} style={s.fInput} />
        <div style={s.fRow}>
          <input placeholder="Phone" value={customer.phone} onChange={e => setCustomer({ ...customer, phone: e.target.value })} style={s.fInput} />
          <input placeholder="Email (optional)" value={customer.email} onChange={e => setCustomer({ ...customer, email: e.target.value })} style={s.fInput} />
        </div>
      </div>

      <p style={s.sectionLabel}>Line items</p>
      {items.map((it, i) => (
        <div key={i} style={s.itemRow}>
          <input list="prod-list" placeholder="Description" value={it.description} onChange={e => updateItem(i, 'description', e.target.value)} style={{ ...s.fInput, flex: 2 }} />
          <input type="number" placeholder="Qty" value={it.quantity} onChange={e => updateItem(i, 'quantity', e.target.value)} style={{ ...s.fInput, width: 60 }} />
          <input type="number" step="0.01" placeholder="R" value={it.unit_price} onChange={e => updateItem(i, 'unit_price', e.target.value)} style={{ ...s.fInput, width: 80 }} />
          {items.length > 1 && <button onClick={() => removeItem(i)} style={s.rmBtn}>×</button>}
        </div>
      ))}
      <datalist id="prod-list">
        {products.map(p => <option key={p.id} value={p.name}>{`R${(p.price_cents / 100).toFixed(2)}`}</option>)}
      </datalist>
      <button onClick={addItem} style={s.addItemBtn}>+ Add line</button>

      <div style={s.fRow}>
        <label style={s.dueLabel}>Due date <input type="date" value={dueDate} onChange={e => setDueDate(e.target.value)} style={s.fInput} /></label>
        <label style={s.dueLabel}>VAT % <input type="number" value={vatRate} onChange={e => setVatRate(parseFloat(e.target.value) || 0)} style={{ ...s.fInput, width: 60 }} /></label>
      </div>

      <div style={s.totals}>
        <div style={s.totRow}><span>Subtotal</span><span>{fmt(subtotal)}</span></div>
        <div style={s.totRow}><span>VAT ({vatRate}%)</span><span>{fmt(vat)}</span></div>
        <div style={{ ...s.totRow, ...s.totFinal }}><span>Total</span><span>{fmt(total)}</span></div>
      </div>

      <button onClick={save} disabled={saving || !customer.name} style={s.saveInvBtn}>
        {saving ? 'Saving…' : 'Create invoice'}
      </button>
    </div>
  )
}

const s = {
  topBar:     { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 },
  count:      { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  newBtn:     { marginLeft: 'auto', padding: '8px 16px', background: '#2C5545', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  backBtn:    { padding: '6px 12px', background: 'transparent', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  formTitle:  { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: 0 },
  muted:      { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  list:       { display: 'flex', flexDirection: 'column', gap: 8 },
  card:       { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '14px 16px' },
  cardTop:    { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  invNum:     { fontFamily: "'Source Code Pro', monospace", fontSize: 13, fontWeight: 600, color: '#1E1E1E', marginRight: 8 },
  amount:     { fontFamily: 'system-ui', fontSize: 15, fontWeight: 700, color: '#2C5545' },
  badge:      { padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600 },
  cust:       { fontFamily: 'system-ui', fontSize: 13, color: '#444', margin: '2px 0' },
  dates:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', margin: '0 0 8px' },
  cardActions:{ display: 'flex', gap: 6 },
  actWa:      { padding: '5px 10px', background: 'rgba(37,211,102,0.1)', color: '#1da851', border: '1px solid rgba(37,211,102,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actPaid:    { padding: '5px 10px', background: '#2C5545', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui', fontWeight: 600 },
  actDel:     { padding: '5px 10px', background: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  formSection:{ marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 8 },
  fInput:     { padding: '9px 11px', border: '1px solid #DDD8CE', borderRadius: 6, fontFamily: 'system-ui', fontSize: 13, boxSizing: 'border-box', flex: 1 },
  fRow:       { display: 'flex', gap: 8 },
  sectionLabel:{ fontFamily: 'system-ui', fontSize: 12, fontWeight: 600, color: '#1E1E1E', margin: '8px 0 6px' },
  itemRow:    { display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' },
  rmBtn:      { background: 'transparent', border: 'none', color: '#ef4444', fontSize: 20, cursor: 'pointer', lineHeight: 1 },
  addItemBtn: { padding: '6px 12px', background: 'transparent', border: '1px dashed #DDD8CE', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680', marginBottom: 12 },
  dueLabel:   { fontFamily: 'system-ui', fontSize: 12, color: '#8A8680', display: 'flex', flexDirection: 'column', gap: 4, flex: 1 },
  totals:     { background: '#F7F4EE', borderRadius: 8, padding: 14, margin: '12px 0' },
  totRow:     { display: 'flex', justifyContent: 'space-between', fontFamily: 'system-ui', fontSize: 13, color: '#444', padding: '3px 0' },
  totFinal:   { borderTop: '1px solid #DDD8CE', marginTop: 6, paddingTop: 8, fontWeight: 700, fontSize: 15, color: '#2C5545' },
  saveInvBtn: { width: '100%', padding: '12px', background: '#2C5545', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
}
