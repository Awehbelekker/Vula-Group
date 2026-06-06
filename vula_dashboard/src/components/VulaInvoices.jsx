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
  const [showCreate, setShowCreate] = useState(null) // null | 'invoice' | 'quote'
  const [docType, setDocType] = useState('invoice')
  const [matchResults, setMatchResults] = useState({})  // { [invId]: result }
  const [matchingId, setMatchingId] = useState(null)     // id currently matching

  const load = useCallback(async () => {
    setLoading(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices?doc_type=${docType}`)
    const d = await r.json()
    setInvoices(d.invoices || [])
    setLoading(false)
  }, [tenantId, docType])

  useEffect(() => { load() }, [load])

  async function convertToInvoice(quote) {
    if (!confirm(`Convert ${quote.invoice_number} to a tax invoice?`)) return
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${quote.id}/convert`, {
      method: 'POST'
    })
    if (r.ok) {
      setDocType('invoice')
      load()
    }
  }

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

  // Open the customer's WhatsApp with a prefilled message + PDF download link.
  // Used as a fallback when the server-side document send is unavailable.
  function whatsAppLinkFallback(inv) {
    const phone = (inv.customer_phone || '').replace(/[^\d]/g, '').replace(/^0/, '27')
    const pdfUrl = `${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/pdf`
    const msg = `Hi ${inv.customer_name}, here's your invoice ${inv.invoice_number} for ${fmt(inv.total_cents)}. ` +
      (inv.due_date ? `Due ${inv.due_date}. ` : '') + `Download: ${pdfUrl}`
    window.open(`https://wa.me/${phone}?text=${encodeURIComponent(msg)}`, '_blank')
    if (inv.status === 'draft') setStatus(inv, 'sent')
  }

  async function sendWhatsApp(inv) {
    if (!confirm(`Send ${inv.invoice_number} to ${inv.customer_phone} on WhatsApp?`)) return
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/send-whatsapp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) {
        alert(`Sent to ${d.to}`)
        load()
        return
      }
      // Server can't deliver the document (e.g. WhatsApp not configured) — fall
      // back to opening WhatsApp with a PDF download link.
      whatsAppLinkFallback(inv)
    } catch {
      whatsAppLinkFallback(inv)
    }
  }

  async function matchSupplier(inv) {
    setMatchingId(inv.id)
    try {
      const r = await fetch(
        `${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/match-supplier`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
      )
      const d = await r.json().catch(() => ({}))
      setMatchResults(prev => ({ ...prev, [inv.id]: d }))
      if (!d.matched) alert('No supplier match found for this invoice.')
    } catch {
      alert('Could not reach the matching service.')
    } finally {
      setMatchingId(null)
    }
  }

  function downloadPdf(inv) {
    window.open(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/pdf`, '_blank')
  }

  async function emailInvoice(inv) {
    if (!confirm(`Email ${inv.invoice_number} to ${inv.customer_email}?`)) return
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/send-email`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      })
      const d = await r.json().catch(() => ({}))
      if (r.ok) {
        alert(`Sent to ${d.to}`)
        load()
      } else {
        alert(d.detail || 'Could not send email.')
      }
    } catch {
      alert('Could not send email.')
    }
  }

  const fmt = c => `R${(c / 100).toFixed(2)}`

  if (showCreate) {
    return <InvoiceCreate
      tenantId={tenantId}
      products={products}
      docType={showCreate}
      onDone={() => { setShowCreate(null); load() }}
      onCancel={() => setShowCreate(null)}
    />
  }

  return (
    <div>
      <div style={s.tabs}>
        <button onClick={() => setDocType('invoice')} style={{...s.tab, ...(docType === 'invoice' ? s.tabActive : {})}}>Invoices</button>
        <button onClick={() => setDocType('quote')} style={{...s.tab, ...(docType === 'quote' ? s.tabActive : {})}}>Quotes</button>
      </div>

      <div style={s.topBar}>
        <p style={s.count}>{invoices.length} {docType}{invoices.length !== 1 ? 's' : ''}</p>
        <button onClick={() => setShowCreate(docType)} style={s.newBtn}>+ New {docType}</button>
      </div>

      {loading ? <p style={s.muted}>Loading…</p> : invoices.length === 0 ? (
        <p style={s.muted}>No {docType}s yet. Create one, or scan an existing document with the Smart Scanner.</p>
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
                  {inv.doc_type === 'quote' ? 'Quoted' : 'Issued'} {inv.issue_date}
                  {inv.due_date ? ` · Due ${inv.due_date}` : ''}
                  {inv.valid_until ? ` · Valid until ${inv.valid_until}` : ''}
                </p>
                <div style={s.cardActions}>
                  {inv.customer_phone && <button onClick={() => sendWhatsApp(inv)} style={s.actWa}>💬 WhatsApp</button>}
                  <button onClick={() => downloadPdf(inv)} style={s.actPdf}>📄 PDF</button>
                  {inv.customer_email && <button onClick={() => emailInvoice(inv)} style={s.actEmail}>✉️ Email</button>}
                  {inv.doc_type === 'invoice' && (
                    <button
                      onClick={() => matchSupplier(inv)}
                      disabled={matchingId === inv.id}
                      style={s.actMatch}
                    >
                      {matchingId === inv.id ? 'Matching…' : '🔗 Match Supplier'}
                    </button>
                  )}
                  {inv.doc_type === 'quote' && inv.status !== 'accepted' && (
                    <button onClick={() => convertToInvoice(inv)} style={s.actPaid}>Convert to Invoice</button>
                  )}
                  {inv.doc_type === 'invoice' && inv.status !== 'paid' && (
                    <button onClick={() => setStatus(inv, 'paid')} style={s.actPaid}>✓ Mark paid</button>
                  )}
                  <button onClick={() => del(inv.id)} style={s.actDel}>Delete</button>
                </div>
                {matchResults[inv.id]?.matched && (
                  <div style={s.matchBanner}>
                    🔗 Matched <strong>{matchResults[inv.id].supplier?.name}</strong>
                    {' · '}{matchResults[inv.id].supplier?.payment_terms_days ?? 30} day terms
                    {' · '}via {matchResults[inv.id].tier}
                    {' ('}{Math.round((matchResults[inv.id].confidence || 0) * 100)}%{')'}
                    {matchResults[inv.id].auto_apply
                      ? ' — applied automatically'
                      : ' — confirm to apply'}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Create invoice form ─────────────────────────────────────────────────────

function InvoiceCreate({ tenantId, products, docType, onDone, onCancel }) {
  const [customer, setCustomer] = useState({ name: '', phone: '', email: '', address: '' })
  const [items, setItems] = useState([{ description: '', quantity: 1, unit_price: '' }])
  const [dueDate, setDueDate] = useState('')
  const [validUntil, setValidUntil] = useState('')
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
        doc_type: docType,
        customer_name: customer.name, customer_phone: customer.phone,
        customer_email: customer.email, customer_address: customer.address,
        line_items: lineItems, vat_rate: vatRate,
        issue_date: new Date().toISOString().slice(0, 10),
        due_date: dueDate || null,
        valid_until: validUntil || null,
        status: 'draft',
      }),
    })
    setSaving(false)
    onDone()
  }

  return (
    <div>
      <div style={s.topBar}>
        <button onClick={onCancel} style={s.backBtn}>← Back</button>
        <h3 style={s.formTitle}>New {docType}</h3>
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
        {docType === 'invoice' ? (
          <label style={s.dueLabel}>Due date <input type="date" value={dueDate} onChange={e => setDueDate(e.target.value)} style={s.fInput} /></label>
        ) : (
          <label style={s.dueLabel}>Valid until <input type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} style={s.fInput} /></label>
        )}
        <label style={s.dueLabel}>VAT % <input type="number" value={vatRate} onChange={e => setVatRate(parseFloat(e.target.value) || 0)} style={{ ...s.fInput, width: 60 }} /></label>
      </div>

      <div style={s.totals}>
        <div style={s.totRow}><span>Subtotal</span><span>{fmt(subtotal)}</span></div>
        <div style={s.totRow}><span>VAT ({vatRate}%)</span><span>{fmt(vat)}</span></div>
        <div style={{ ...s.totRow, ...s.totFinal }}><span>Total</span><span>{fmt(total)}</span></div>
      </div>

      <button onClick={save} disabled={saving || !customer.name} style={s.saveInvBtn}>
        {saving ? 'Saving…' : `Create ${docType}`}
      </button>
    </div>
  )
}

const s = {
  tabs:       { display: 'flex', gap: 4, marginBottom: 16, borderBottom: '1px solid #DDD8CE', paddingBottom: 8 },
  tab:        { padding: '6px 16px', background: 'transparent', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  tabActive:  { background: 'rgba(44,85,69,0.1)', color: 'var(--accent, #2C5545)' },
  topBar:     { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 },
  count:      { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  newBtn:     { marginLeft: 'auto', padding: '8px 16px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  backBtn:    { padding: '6px 12px', background: 'transparent', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  formTitle:  { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: 0 },
  muted:      { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  list:       { display: 'flex', flexDirection: 'column', gap: 8 },
  card:       { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '14px 16px' },
  cardTop:    { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  invNum:     { fontFamily: "'Source Code Pro', monospace", fontSize: 13, fontWeight: 600, color: '#1E1E1E', marginRight: 8 },
  amount:     { fontFamily: 'system-ui', fontSize: 15, fontWeight: 700, color: 'var(--accent, #2C5545)' },
  badge:      { padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600 },
  cust:       { fontFamily: 'system-ui', fontSize: 13, color: '#444', margin: '2px 0' },
  dates:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', margin: '0 0 8px' },
  cardActions:{ display: 'flex', gap: 6 },
  actWa:      { padding: '5px 10px', background: 'rgba(37,211,102,0.1)', color: '#1da851', border: '1px solid rgba(37,211,102,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actPdf:     { padding: '5px 10px', background: 'rgba(0,119,182,0.08)', color: '#0077b6', border: '1px solid rgba(0,119,182,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actEmail:   { padding: '5px 10px', background: 'rgba(212,160,23,0.1)', color: '#a8780a', border: '1px solid rgba(212,160,23,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actPaid:    { padding: '5px 10px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui', fontWeight: 600 },
  actDel:     { padding: '5px 10px', background: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actMatch:   { padding: '5px 10px', background: 'rgba(44,85,69,0.08)', color: 'var(--accent, #2C5545)', border: '1px solid rgba(44,85,69,0.25)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  matchBanner:{ marginTop: 8, padding: '8px 10px', background: 'rgba(44,85,69,0.07)', border: '1px solid rgba(44,85,69,0.2)', borderRadius: 6, fontSize: 12, fontFamily: 'system-ui', color: '#2C5545' },
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
  totFinal:   { borderTop: '1px solid #DDD8CE', marginTop: 6, paddingTop: 8, fontWeight: 700, fontSize: 15, color: 'var(--accent, #2C5545)' },
  saveInvBtn: { width: '100%', padding: '12px', background: 'var(--accent, #2C5545)', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
}
