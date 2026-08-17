/**
 * VulaInvoices.jsx — Invoice management for a tenant.
 * Adapted from Awake SA admin/invoices, wired to Vula backend.
 *
 * - List invoices with status (draft/sent/paid/overdue)
 * - Create invoice (line items + VAT)
 * - Send via WhatsApp, mark paid, delete
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { toWhatsAppNumber } from '../lib/phone'
import { supabase } from '../lib/supabase'
import { FONT_PAIRINGS } from '../theme/tokens'

const VULA_API = import.meta.env.VITE_API_URL || 'https://vula-group-production.up.railway.app'

const STATUS = {
  draft:     { label: 'Draft',      color: '#6b7280', bg: 'rgba(107,114,128,0.12)' },
  sent:      { label: 'Sent',       color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  part_paid: { label: 'Part paid',  color: '#d97706', bg: 'rgba(217,119,6,0.12)' },
  paid:      { label: 'Paid',       color: '#16a34a', bg: 'rgba(34,197,94,0.12)' },
  overdue:   { label: 'Overdue',    color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  cancelled: { label: 'Cancelled',  color: '#9ca3af', bg: 'rgba(156,163,175,0.12)' },
  accepted:  { label: 'Accepted',   color: '#16a34a', bg: 'rgba(34,197,94,0.12)' },
  declined:  { label: 'Declined',   color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  expired:   { label: 'Expired',    color: '#9ca3af', bg: 'rgba(156,163,175,0.12)' },
}

export default function VulaInvoices({ tenantId, products = [], initialSupplierId = null, onClearSupplierFilter }) {
  const [invoices, setInvoices] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(null) // null | 'invoice' | 'quote'
  const [docType, setDocType] = useState('invoice')
  // 'outbound' = we sent it to a client (the tab's original behaviour). 'inbound' = a supplier
  // sent it to us — booked by the Smart Scanner / email / WhatsApp / dashboard-upload intake
  // pipelines, but invisible until now because the backend defaulted to outbound with no toggle.
  const [direction, setDirection] = useState(initialSupplierId ? 'inbound' : 'outbound')
  // Deep-linked from the Suppliers tab's "🧾 Invoices" button — filters to that one supplier.
  const [supplierFilter, setSupplierFilter] = useState(initialSupplierId)
  const [matchResults, setMatchResults] = useState({})  // { [invId]: result }
  const [matchingId, setMatchingId] = useState(null)     // id currently matching
  const [settings, setSettings] = useState(null)         // commerce_invoice_settings row
  const [showSettings, setShowSettings] = useState(false)
  const [showRecurring, setShowRecurring] = useState(false)
  const autoOpened = useRef(false)                       // first-run wizard opened once
  const [selectedQuotes, setSelectedQuotes] = useState(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [creditingInvoice, setCreditingInvoice] = useState(null)  // invoice row being partially credited
  const [editingInvoice, setEditingInvoice] = useState(null)      // quote/invoice row being edited (draft/sent only)
  const [emailConfigured, setEmailConfigured] = useState(true)    // assume yes until told otherwise — avoids a flash of "disabled" on first paint

  // A fresh deep-link (e.g. clicking a different supplier while already on this tab) —
  // re-sync local state to match.
  useEffect(() => {
    if (initialSupplierId && initialSupplierId !== supplierFilter) {
      setSupplierFilter(initialSupplierId)
      setDirection('inbound')
    }
  }, [initialSupplierId])  // eslint-disable-line

  function clearSupplierFilter() {
    setSupplierFilter(null)
    if (onClearSupplierFilter) onClearSupplierFilter()
  }

  const load = useCallback(async () => {
    setLoading(true)
    const supplierQs = supplierFilter ? `&supplier_id=${supplierFilter}` : ''
    const [d, sr] = await Promise.all([
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices?doc_type=${docType}&direction=${direction}${supplierQs}`).then(r => r.json()),
      fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-settings`).then(r => r.json()).catch(() => null),
    ])
    setInvoices(d.invoices || [])
    setSelectedQuotes(new Set())
    if (sr) {
      setSettings(sr.settings || null)
      setEmailConfigured(sr.email_configured !== false)
      // First visit: open the setup wizard once if the tenant hasn't onboarded.
      if (!sr.onboarded && !autoOpened.current) {
        autoOpened.current = true
        setShowSettings(true)
      }
    }
    setLoading(false)
  }, [tenantId, docType, direction, supplierFilter])

  useEffect(() => { load() }, [load])

  // Quotes converted before migration 134 (invoiced_cents) have converted_invoice_id set but
  // invoiced_cents still 0 — that single old-system conversion was always for the full total,
  // so treat it as fully invoiced rather than reopening it for a "new" partial conversion.
  function quoteInvoicedCents(inv) {
    if (inv.invoiced_cents) return inv.invoiced_cents
    return inv.converted_invoice_id ? (inv.total_cents || 0) : 0
  }

  async function convertToInvoice(quote) {
    const remaining = (quote.total_cents || 0) - quoteInvoicedCents(quote)
    let amountCents = null  // null = invoice whatever remains (the whole quote on a first call)
    if (remaining > 0 && quoteInvoicedCents(quote) === 0) {
      // First conversion of this quote — offer a deposit/portion instead of always the full amount.
      const full = remaining / 100
      const answer = window.prompt(
        `Convert ${quote.invoice_number} to a tax invoice.\n\nInvoice the full R${full.toFixed(2)}, ` +
        `or enter a deposit amount in Rand (e.g. ${(full * 0.3).toFixed(2)} for 30%):`,
        full.toFixed(2),
      )
      if (answer === null) return
      const parsed = Math.round(parseFloat(answer) * 100)
      if (!parsed || parsed <= 0) { alert('Please enter a valid amount.'); return }
      if (parsed > remaining) { alert(`Only R${full.toFixed(2)} remains on this quote.`); return }
      if (parsed < remaining) amountCents = parsed
    } else if (remaining > 0) {
      // Already partially invoiced — this call is for the remaining balance.
      if (!confirm(`Invoice the remaining R${(remaining / 100).toFixed(2)} balance on ${quote.invoice_number}?`)) return
    } else {
      return
    }
    try {
      // NOTE: this is /admin/quotes/.../convert, not /admin/invoices/.../convert — the two
      // previously didn't match (the route lives under quotes, this used to call invoices),
      // so every conversion silently 404'd. Fixed 2026-08-15.
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/quotes/${quote.id}/convert`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(amountCents ? { amount_cents: amountCents } : {}),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        alert(d.detail || 'Could not convert this quote to an invoice.')
        return
      }
      setDocType('invoice')
      load()
    } catch {
      alert('Could not convert this quote to an invoice — please try again.')
    }
  }

  async function setStatus(inv, status) {
    // paid_at is stamped server-side now (never trust a client timestamp for this) — see
    // markPaid() below for the 'paid' transition specifically.
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) {
        alert(d.detail || `Could not update this ${inv.doc_type}.`)
        return
      }
    } catch {
      alert(`Could not update this ${inv.doc_type} — please try again.`)
      return
    }
    load()
  }

  async function markPaid(inv) {
    const method = window.prompt('How was this paid? (cash / eft / card / other — leave blank to skip)', 'eft')
    if (method === null) return  // cancelled
    const body = { status: 'paid' }
    if (method.trim()) body.payment_method = method.trim().toLowerCase()
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    load()
  }

  async function del(id) {
    if (!confirm('Delete this invoice?')) return
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${id}`, { method: 'DELETE' })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { alert(d.detail || 'Could not delete this document.'); return }
    } catch {
      alert('Could not delete this document — please try again.')
      return
    }
    load()
  }

  async function recordPayment(inv) {
    const balance = (inv.total_cents || 0) - (inv.total_paid_cents || 0)
    const raw = window.prompt(
      `How much was paid? (Rand — balance due is R${(balance / 100).toFixed(2)})`, (balance / 100).toFixed(2))
    if (raw === null) return
    const rand = parseFloat(raw)
    if (!rand || rand <= 0) { alert('Enter an amount greater than zero.'); return }
    const method = window.prompt('How was this paid? (cash / eft / card / other — leave blank to skip)', 'eft')
    const body = { amount_cents: Math.round(rand * 100) }
    if (method && method.trim()) body.payment_method = method.trim().toLowerCase()
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/payments`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) { alert(d.detail || 'Could not record that payment.'); return }
    load()
  }

  async function cancelInvoice(inv) {
    const reason = window.prompt(`Cancel ${inv.invoice_number}? Optionally say why (or leave blank):`, '')
    if (reason === null) return
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/cancel`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: reason || null }),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) { alert(d.detail || 'Could not cancel this invoice.'); return }
    load()
  }

  function toggleQuoteSelect(id) {
    setSelectedQuotes(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function deleteSelectedQuotes() {
    const ids = [...selectedQuotes]
    if (!ids.length) return
    if (!confirm(`Delete ${ids.length} selected quote${ids.length === 1 ? '' : 's'}? This can't be undone.`)) return
    setBulkDeleting(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/quotes/bulk-delete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quote_ids: ids }),
    })
    await load()
    setBulkDeleting(false)
  }

  // Open the customer's WhatsApp with a prefilled message + PDF download link.
  // Used as a fallback when the server-side document send is unavailable — e.g. WhatsApp's own
  // 24-hour messaging window (Meta only allows a free-form document send to a customer who's
  // messaged the business recently; outside that window a manual send is the only option).
  // reason (optional) explains WHY the automatic send didn't happen — shown up front rather
  // than silently swapping to the manual link with no context.
  function whatsAppLinkFallback(inv, reason) {
    if (reason) alert(`Couldn't send automatically (${reason}) — opening WhatsApp for you to send it yourself.`)
    const phone = toWhatsAppNumber(inv.customer_phone)
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
      // Server can't deliver the document (e.g. WhatsApp not configured, or the customer
      // hasn't messaged this business in the last 24 hours — Meta's own window for a free-form
      // document send) — fall back to opening WhatsApp with a PDF download link.
      whatsAppLinkFallback(inv, d.detail || `server error ${r.status}`)
    } catch {
      whatsAppLinkFallback(inv, 'network error')
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

  // A fuzzy/layout match isn't confident enough to apply automatically — route it to the
  // tenant's own admin team as a yes/no over WhatsApp instead of silently guessing.
  async function requestSupplierApproval(inv) {
    const m = matchResults[inv.id]
    if (!m?.matched) return
    try {
      const r = await fetch(
        `${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/request-supplier-approval`,
        {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            candidate_supplier_id: m.supplier?.id, candidate_supplier_name: m.supplier?.name,
            tier: m.tier, confidence: m.confidence,
          }),
        }
      )
      const d = await r.json().catch(() => ({}))
      if (r.ok) alert(`Sent to ${d.approvers} admin${d.approvers !== 1 ? 's' : ''} on WhatsApp for confirmation.`)
      else alert(d.detail || 'Could not request approval.')
    } catch {
      alert('Could not request approval.')
    }
  }

  // A raw window.open() against this admin endpoint 401s with "Sign in required" — it's a plain
  // browser navigation, so it never carries the Authorization header the installAuthFetch()
  // patch attaches to fetch() calls. Fetch it as an authenticated request instead and open the
  // resulting bytes as a blob URL.
  async function downloadPdf(inv) {
    try {
      const resp = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/pdf`)
      if (!resp.ok) { alert('Could not open the PDF — please try again.'); return }
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch {
      alert('Could not open the PDF — please try again.')
    }
  }

  // The real document a supplier sent (scanned/emailed/WhatsApp'd) — linked via migration 102's
  // vula_filed_documents.commerce_invoice_id bridge. file_url is a direct Supabase Storage link
  // (not behind the admin auth guard), so it opens with a plain window.open().
  async function openFiledDocument(inv) {
    try {
      const d = await fetch(`${VULA_API}/v1/documents/${tenantId}/filed?commerce_invoice_id=${inv.id}`).then(r => r.json())
      const doc = (d.documents || [])[0]
      if (doc?.file_url) window.open(doc.file_url, '_blank')
      else alert('No original document linked to this invoice.')
    } catch {
      alert('Could not look up the original document.')
    }
  }

  async function creditNote(inv) {
    let autoRefund = false
    if (inv.yoco_checkout_id) {
      autoRefund = confirm(
        `This invoice was paid online via Yoco (${fmt(inv.total_cents)}).\n\n` +
        `OK = create the credit note AND refund the customer through Yoco now.\nCancel = just create the credit note (you'll refund them yourself).`
      )
    } else if (!confirm(`Create a credit note for ${inv.invoice_number} (${fmt(inv.total_cents)})?`)) {
      return
    }
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/credit-note`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ auto_refund: autoRefund }) })
      .then(r => r.json()).catch(() => ({}))
    if (d.credit_note) {
      let msg = `Credit note ${d.credit_note.invoice_number} created.`
      if (d.refund?.status === 'pending') msg += ` Refunded ${fmt(d.refund.amount_cents)} via Yoco.`
      else if (d.refund?.status === 'failed') msg += ` Automatic Yoco refund failed (${d.refund.detail || 'unknown error'}) — please refund manually.`
      alert(msg); load()
    }
    else alert(d.detail || 'Could not create credit note.')
  }

  async function payLink(inv) {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${inv.id}/pay-link`, { method: 'POST' })
      .then(r => r.json()).catch(() => ({}))
    if (d.pay_url) {
      try { await navigator.clipboard.writeText(d.pay_url) } catch (e) { /* clipboard may be blocked */ }
      alert(`Pay link created & copied to clipboard:\n\n${d.pay_url}\n\nSend it to your customer — the invoice marks itself paid once they pay.`)
      load()
    } else if (d.already_paid) {
      alert('This invoice is already paid.')
    } else {
      alert(d.detail || 'Could not create pay link — connect Yoco in Branding/Settings first.')
    }
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

  if (showSettings) {
    return <InvoiceSettings
      tenantId={tenantId}
      settings={settings}
      firstRun={!settings || !settings.onboarded}
      onDone={() => { setShowSettings(false); load() }}
      onCancel={() => setShowSettings(false)}
    />
  }

  if (showCreate) {
    return <InvoiceCreate
      tenantId={tenantId}
      products={products}
      docType={showCreate}
      editingInvoice={editingInvoice}
      onDone={() => { setShowCreate(null); setEditingInvoice(null); load() }}
      onCancel={() => { setShowCreate(null); setEditingInvoice(null) }}
    />
  }

  if (showRecurring) {
    return <RecurringManager tenantId={tenantId} onCancel={() => setShowRecurring(false)} />
  }

  if (creditingInvoice) {
    return <CreditNoteForm
      tenantId={tenantId}
      invoice={creditingInvoice}
      onDone={() => { setCreditingInvoice(null); load() }}
      onCancel={() => setCreditingInvoice(null)}
    />
  }

  return (
    <div>
      <div style={s.dirTabs}>
        <button onClick={() => { setDirection('outbound'); clearSupplierFilter() }} style={{...s.dirTab, ...(direction === 'outbound' ? s.dirTabActive : {})}}>📤 Sent to clients</button>
        <button onClick={() => setDirection('inbound')} style={{...s.dirTab, ...(direction === 'inbound' ? s.dirTabActive : {})}}>📥 Received from suppliers</button>
        {supplierFilter && (
          <button onClick={clearSupplierFilter} style={s.dirFilterChip}>Filtered by supplier · ✕ clear</button>
        )}
      </div>

      <div style={s.tabs}>
        <button onClick={() => setDocType('invoice')} style={{...s.tab, ...(docType === 'invoice' ? s.tabActive : {})}}>Invoices</button>
        <button onClick={() => setDocType('quote')} style={{...s.tab, ...(docType === 'quote' ? s.tabActive : {})}}>Quotes</button>
        {direction === 'outbound' && <button onClick={() => setDocType('credit_note')} style={{...s.tab, ...(docType === 'credit_note' ? s.tabActive : {})}}>Credit Notes</button>}
      </div>

      <div style={s.topBar}>
        <p style={s.count}>{invoices.length} {docType}{invoices.length !== 1 ? 's' : ''}</p>
        {direction === 'outbound' && <button onClick={() => setShowRecurring(true)} style={s.brandBtn}>🔁 Recurring</button>}
        <button onClick={() => setShowSettings(true)} style={s.brandBtn}>⚙ Branding</button>
        {direction === 'outbound' && docType !== 'credit_note' && <button onClick={() => setShowCreate(docType)} style={s.newBtn}>+ New {docType}</button>}
      </div>

      {docType === 'quote' && selectedQuotes.size > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'rgba(162,59,45,0.06)',
          border: '1px solid rgba(162,59,45,0.25)', borderRadius: 8, padding: '8px 12px', marginBottom: 10 }}>
          <span style={{ fontSize: 13, fontFamily: 'system-ui' }}>{selectedQuotes.size} selected</span>
          <button onClick={deleteSelectedQuotes} disabled={bulkDeleting} style={s.actDel}>
            {bulkDeleting ? 'Deleting…' : `🗑 Delete ${selectedQuotes.size} selected`}
          </button>
          <button onClick={() => setSelectedQuotes(new Set())} style={{ ...s.actMatch, marginLeft: 'auto' }}>Clear</button>
        </div>
      )}

      {loading ? <p style={s.muted}>Loading…</p> : invoices.length === 0 ? (
        <p style={s.muted}>
          {direction === 'inbound'
            ? `No ${docType}s from suppliers yet — they'll appear here once scanned, emailed, or WhatsApp'd in.`
            : `No ${docType}s yet. Create one, or scan an existing document with the Smart Scanner.`}
        </p>
      ) : (
        <div style={s.list}>
          {invoices.map(inv => {
            const st = STATUS[inv.status] || STATUS.draft
            const quoteDeletable = inv.doc_type === 'quote' && (inv.status === 'draft' || inv.status === 'sent')
            return (
              <div key={inv.id} style={s.card}>
                <div style={s.cardTop}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {quoteDeletable && (
                      <input type="checkbox" checked={selectedQuotes.has(inv.id)} onChange={() => toggleQuoteSelect(inv.id)}
                        title="Select for bulk delete" style={{ cursor: 'pointer' }} />
                    )}
                    {['draft', 'sent'].includes(inv.status) ? (
                      <span
                        style={{ ...s.invNum, cursor: 'pointer', textDecoration: 'underline', textDecorationStyle: 'dotted' }}
                        title="Click to edit"
                        onClick={() => { setEditingInvoice(inv); setShowCreate(inv.doc_type) }}
                      >
                        {inv.invoice_number}
                      </span>
                    ) : (
                      <span style={s.invNum}>{inv.invoice_number}</span>
                    )}
                    <span style={{ ...s.badge, color: st.color, background: st.bg }}>{st.label}</span>
                  </div>
                  <span style={s.amount}>{fmt(inv.total_cents)}</span>
                </div>
                <p style={s.cust}>
                  {inv.direction === 'inbound' ? (inv.supplier || inv.customer_name) : inv.customer_name}
                  {inv.customer_phone ? ` · ${inv.customer_phone}` : ''}
                  {inv.direction === 'inbound' && <span style={s.supplierBadge}>{inv.supplier_id ? '🔗 supplier matched' : '⚠ needs review'}</span>}
                </p>
                <p style={s.dates}>
                  {inv.doc_type === 'quote' ? 'Quoted' : 'Issued'} {inv.issue_date}
                  {inv.due_date ? ` · Due ${inv.due_date}` : ''}
                  {inv.valid_until ? ` · Valid until ${inv.valid_until}` : ''}
                  {inv.project ? ` · ${inv.project}` : ''}
                </p>
                <div style={s.cardActions}>
                  {inv.direction === 'inbound' ? (
                    <>
                      <button onClick={() => openFiledDocument(inv)} style={s.actPdf}>📎 Original document</button>
                      <button
                        onClick={() => matchSupplier(inv)}
                        disabled={matchingId === inv.id}
                        style={s.actMatch}
                      >
                        {matchingId === inv.id ? 'Matching…' : '🔗 Match Supplier'}
                      </button>
                      {inv.status !== 'paid' && (
                        <button onClick={() => markPaid(inv)} style={s.actPaid}>✓ Mark paid</button>
                      )}
                    </>
                  ) : (
                    <>
                      {inv.customer_phone && <button onClick={() => sendWhatsApp(inv)} style={s.actWa}>💬 WhatsApp</button>}
                      <button onClick={() => downloadPdf(inv)} style={s.actPdf}>📄 PDF</button>
                      {inv.customer_email && (
                        emailConfigured ? (
                          <button onClick={() => emailInvoice(inv)} style={s.actEmail}>✉️ Email</button>
                        ) : (
                          <button disabled style={s.actDisabled}
                                  title="Email sending isn't set up for this account yet — ask your Vula admin to configure it. Use WhatsApp or download the PDF instead.">
                            ✉️ Email — not set up
                          </button>
                        )
                      )}
                      {inv.doc_type === 'invoice' && inv.status === 'draft' && (
                        <button onClick={() => setStatus(inv, 'sent')} style={s.actMatch}
                                title="Already sent this outside Vula (email/WhatsApp/in person)? Mark it sent so it shows up correctly on your dashboard and gets payment reminders.">
                          📤 Mark as sent
                        </button>
                      )}
                      {inv.doc_type === 'invoice' && inv.status !== 'paid' && <button onClick={() => payLink(inv)} style={s.actPaid}>💳 Pay link</button>}
                      {inv.doc_type === 'invoice' && <button onClick={() => creditNote(inv)} style={s.actMatch}>↩️ Credit note</button>}
                      {inv.doc_type === 'invoice' && Array.isArray(inv.line_items) && inv.line_items.length > 1 && (
                        <button onClick={() => setCreditingInvoice(inv)} style={s.actMatch}>✂️ Partial credit</button>
                      )}
                      {inv.doc_type === 'invoice' && (
                        <button
                          onClick={() => matchSupplier(inv)}
                          disabled={matchingId === inv.id}
                          style={s.actMatch}
                        >
                          {matchingId === inv.id ? 'Matching…' : '🔗 Match Supplier'}
                        </button>
                      )}
                      {inv.doc_type === 'quote' && !['accepted', 'declined', 'expired'].includes(inv.status) && (
                        <button onClick={() => setStatus(inv, 'accepted')} style={s.actMatch}>✓ Mark accepted</button>
                      )}
                      {inv.doc_type === 'quote' && quoteInvoicedCents(inv) < (inv.total_cents || 0) && (
                        inv.status === 'accepted' ? (
                          <button onClick={() => convertToInvoice(inv)} style={s.actPaid}>
                            {quoteInvoicedCents(inv) > 0 ? 'Invoice remaining balance' : 'Convert to Invoice'}
                          </button>
                        ) : (
                          <button disabled title="Mark this quote accepted before converting it to an invoice"
                                  style={s.actDisabled}>Convert to Invoice — mark accepted first</button>
                        )
                      )}
                      {inv.doc_type === 'quote' && quoteInvoicedCents(inv) > 0 && quoteInvoicedCents(inv) < (inv.total_cents || 0) && (
                        <span style={s.balanceTag}>
                          Balance: R{(((inv.total_cents || 0) - quoteInvoicedCents(inv)) / 100).toFixed(2)}
                        </span>
                      )}
                      {inv.doc_type === 'invoice' && !['paid', 'cancelled'].includes(inv.status) && (
                        <button onClick={() => markPaid(inv)} style={s.actPaid}>✓ Mark paid</button>
                      )}
                      {inv.doc_type === 'invoice' && !['paid', 'cancelled'].includes(inv.status) && (
                        <button onClick={() => recordPayment(inv)} style={s.actMatch}>💵 Record payment</button>
                      )}
                      {inv.doc_type === 'invoice' && !['paid', 'part_paid', 'cancelled'].includes(inv.status) && (
                        <button onClick={() => cancelInvoice(inv)} style={s.actMatch}>✕ Cancel</button>
                      )}
                    </>
                  )}
                  <button onClick={() => del(inv.id)} style={s.actDel}>Delete</button>
                </div>
                {matchResults[inv.id]?.matched && (
                  <div style={s.matchBanner}>
                    🔗 Matched <strong>{matchResults[inv.id].supplier?.name}</strong>
                    {' · '}{matchResults[inv.id].supplier?.payment_terms_days ?? 30} day terms
                    {' · '}via {matchResults[inv.id].tier}
                    {' ('}{Math.round((matchResults[inv.id].confidence || 0) * 100)}%{')'}
                    {matchResults[inv.id].auto_apply ? ' — applied automatically' : (
                      <>
                        {' — not confident enough to apply automatically. '}
                        <button onClick={() => requestSupplierApproval(inv)} style={s.actMatch}>
                          📨 Ask admin team on WhatsApp
                        </button>
                      </>
                    )}
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

function InvoiceCreate({ tenantId, products, docType, editingInvoice, onDone, onCancel }) {
  const [customer, setCustomer] = useState(editingInvoice
    ? { name: editingInvoice.customer_name || '', phone: editingInvoice.customer_phone || '',
        email: editingInvoice.customer_email || '', address: editingInvoice.customer_address || '' }
    : { name: '', phone: '', email: '', address: '' })
  const [items, setItems] = useState(editingInvoice && Array.isArray(editingInvoice.line_items) && editingInvoice.line_items.length
    ? editingInvoice.line_items.map(it => ({
        description: it.description || '', quantity: it.quantity ?? 1, unit: it.unit || '',
        unit_price: it.unit_price_cents != null ? (it.unit_price_cents / 100).toFixed(2) : '',
        discount: it.discount_pct || '', section: it.section || '',
      }))
    : [{ description: '', quantity: 1, unit: '', unit_price: '', discount: '', section: '' }])
  const [dueDate, setDueDate] = useState(editingInvoice?.due_date || '')
  const [validUntil, setValidUntil] = useState(editingInvoice?.valid_until || '')
  const [vatRate, setVatRate] = useState(editingInvoice?.vat_rate ?? 15)
  const [discountPct, setDiscountPct] = useState(editingInvoice?.discount_pct ? String(editingInvoice.discount_pct) : '')   // invoice-level discount %
  const [deposit, setDeposit] = useState(editingInvoice?.deposit_cents ? (editingInvoice.deposit_cents / 100).toFixed(2) : '')           // deposit already paid (rands)
  const [saving, setSaving] = useState(false)
  const [savedClients, setSavedClients] = useState([])
  const [crmCustomers, setCrmCustomers] = useState([])  // real order/WhatsApp history — the larger, phone-deduplicated list
  const [project, setProject] = useState(editingInvoice?.project || '')
  const [projectList, setProjectList] = useState([])
  const [catalogItems, setCatalogItems] = useState([])   // products & services quick-add catalog
  const [showCatalog, setShowCatalog] = useState(false)

  const loadCatalog = useCallback(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-items`)
      .then(r => r.json()).then(d => setCatalogItems(d.items || [])).catch(() => {})
  }, [tenantId])

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-clients`)
      .then(r => r.json()).then(d => setSavedClients(d.clients || [])).catch(() => {})
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/customers`)
      .then(r => r.json()).then(d => setCrmCustomers(d.customers || [])).catch(() => {})
    fetch(`${VULA_API}/v1/projects/${tenantId}/labels`)
      .then(r => r.json()).then(d => setProjectList(d.projects || [])).catch(() => {})
    loadCatalog()
  }, [tenantId, loadCatalog])

  // Real customers (order/WhatsApp history, phone-deduplicated) come first — commerce_invoice_clients
  // is only for one-off invoice clients who never actually ordered (e.g. a construction client),
  // so it's filtered down to just those not already represented in the CRM list, to avoid ever
  // listing the same person twice under two different identities.
  const pickerOptions = [
    ...crmCustomers.map(c => ({ id: `crm:${c.phone}`, name: c.name || c.phone, phone: c.phone, email: '', address: '' })),
    ...savedClients
      .filter(c => !crmCustomers.some(cc => cc.phone && c.phone && toWhatsAppNumber(cc.phone) === toWhatsAppNumber(c.phone)))
      .map(c => ({ id: c.id, name: c.name, phone: c.phone, email: c.email, address: c.address })),
  ]

  // Re-interpolate any line item whose description came from a catalog template containing
  // {project} whenever the invoice's project changes, so it always names the current project.
  useEffect(() => {
    if (!project) return
    setItems(prev => prev.map(it => (
      it._tplDescription && it._tplDescription.includes('{project}')
        ? { ...it, description: it._tplDescription.replaceAll('{project}', project) }
        : it
    )))
  }, [project])

  function pickClient(id) {
    const c = pickerOptions.find(x => x.id === id)
    if (c) setCustomer({ name: c.name || '', phone: c.phone || '', email: c.email || '', address: c.address || '' })
  }
  async function saveClient() {
    if (!customer.name) return
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-clients`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'client', ...customer }),
    }).then(r => r.json())
    if (d.client) setSavedClients([d.client, ...savedClients.filter(c => c.id !== d.client.id)])
  }

  function updateItem(i, field, val) {
    setItems(items.map((it, idx) => {
      if (idx !== i) return it
      const next = { ...it, [field]: val }
      // Pick a product/service from the catalog → autofill price + unit (still fully editable).
      if (field === 'description') {
        // Tenant-wide products & services catalog (migration 083) — its description may carry
        // a {project} token, substituted with whichever project is selected on this invoice.
        const ci = catalogItems.find(c => (c.name || '').toLowerCase() === (val || '').toLowerCase())
        if (ci) {
          next.unit_price = ((ci.unit_price_cents || 0) / 100).toFixed(2)
          next.unit = ci.unit || next.unit
          const tpl = ci.description || ci.name
          next._tplDescription = tpl
          next.description = project ? tpl.replaceAll('{project}', project) : tpl
        } else {
          delete next._tplDescription
          const p = products.find(pr => (pr.name || '').toLowerCase() === (val || '').toLowerCase())
          if (p) {
            next.unit_price = (p.price_cents / 100).toFixed(2)
            if (!next.unit) next.unit = p.sold_by === 'kg' ? 'kg' : (p.sold_by === 'hour' ? 'hr' : '')
          }
        }
      }
      return next
    }))
  }
  function addItem() { setItems([...items, { description: '', quantity: 1, unit: '', unit_price: '', discount: '', section: '' }]) }
  function removeItem(i) { setItems(items.filter((_, idx) => idx !== i)) }

  const lineItems = items.map(it => {
    const cents = Math.round((parseFloat(it.unit_price) || 0) * 100)
    const disc = parseFloat(it.discount) || 0
    const qty = parseFloat(it.quantity) || 0
    return {
      description: it.description,
      quantity: qty,
      unit: (it.unit || '').trim(),
      unit_price_cents: cents,
      discount_pct: disc,
      total_cents: Math.round(cents * qty * (1 - disc / 100)),
      section: (it.section || '').trim() || undefined,
    }
  })
  const subtotal = lineItems.reduce((sum, i) => sum + i.total_cents, 0)
  const invDiscCents = Math.round(subtotal * (parseFloat(discountPct) || 0) / 100)
  const taxable = subtotal - invDiscCents
  const vat = Math.round(taxable * vatRate / 100)
  const total = taxable + vat
  const depositCents = Math.round((parseFloat(deposit) || 0) * 100)
  const balance = total - depositCents
  const fmt = c => `R${(c / 100).toFixed(2)}`

  async function save() {
    setSaving(true)
    if (editingInvoice) {
      // Edit never touches status — a draft stays draft, a sent document stays sent (the
      // customer already has it; only its recorded numbers change here, not its lifecycle).
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${editingInvoice.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          customer_name: customer.name, customer_phone: customer.phone,
          customer_email: customer.email, customer_address: customer.address,
          line_items: lineItems, subtotal_cents: subtotal, vat_rate: vatRate, vat_cents: vat,
          total_cents: total, discount_cents: invDiscCents,
          deposit_cents: depositCents,
          project: project || null,
          due_date: dueDate || null,
        }),
      })
      const d = await r.json().catch(() => ({}))
      setSaving(false)
      if (!r.ok) { alert(d.detail || `Could not save changes to this ${docType}.`); return }
      onDone()
      return
    }
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doc_type: docType,
        customer_name: customer.name, customer_phone: customer.phone,
        customer_email: customer.email, customer_address: customer.address,
        line_items: lineItems, vat_rate: vatRate,
        discount_pct: parseFloat(discountPct) || 0,
        deposit_cents: depositCents,
        project: project || null,
        issue_date: new Date().toISOString().slice(0, 10),
        due_date: dueDate || null,
        valid_until: validUntil || null,
        status: 'draft',
      }),
    })
    setSaving(false)
    onDone()
  }

  if (showCatalog) {
    return <InvoiceItemsCatalog tenantId={tenantId} onDone={() => { setShowCatalog(false); loadCatalog() }} />
  }

  return (
    <div>
      <div style={s.topBar}>
        <button onClick={onCancel} style={s.backBtn}>← Back</button>
        <h3 style={s.formTitle}>{editingInvoice ? `Edit ${docType}` : `New ${docType}`}</h3>
      </div>

      <div style={s.formSection}>
        {pickerOptions.length > 0 && (
          <select onChange={e => pickClient(e.target.value)} defaultValue="" style={{ ...s.fInput, color: 'var(--muted, #8A8680)' }}>
            <option value="">📇 Pick an existing customer…</option>
            {pickerOptions.map(c => <option key={c.id} value={c.id} style={{ color: '#2A2A2A' }}>{c.name}{c.phone ? ` · ${c.phone}` : ''}</option>)}
          </select>
        )}
        <input placeholder="Customer name" value={customer.name} onChange={e => setCustomer({ ...customer, name: e.target.value })} style={s.fInput} />
        <div style={s.fRow}>
          <input placeholder="Phone" value={customer.phone} onChange={e => setCustomer({ ...customer, phone: e.target.value })} style={s.fInput} />
          <input placeholder="Email (optional)" value={customer.email} onChange={e => setCustomer({ ...customer, email: e.target.value })} style={s.fInput} />
        </div>
        {customer.name && <button type="button" onClick={saveClient} style={{ alignSelf: 'flex-start', padding: '5px 12px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' }}>💾 Save as client</button>}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '8px 0 6px' }}>
        <p style={{ ...s.sectionLabel, margin: 0 }}>Line items</p>
        <button type="button" onClick={() => setShowCatalog(true)} style={{ marginLeft: 'auto', padding: '4px 10px', background: 'transparent', border: '1px dashed #DDD8CE', borderRadius: 6, fontSize: 11, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' }}>🧰 Products &amp; services</button>
      </div>
      {items.map((it, i) => (
        <div key={i} style={s.itemRow}>
          <input list="section-list" placeholder="Section (optional)" title="Group this line under a BoQ trade section, e.g. Demolition, Structure, Finishes" value={it.section} onChange={e => updateItem(i, 'section', e.target.value)} style={{ ...s.fInput, width: 90 }} />
          <input list="prod-list" placeholder="Description / pick product or service" value={it.description} onChange={e => updateItem(i, 'description', e.target.value)} style={{ ...s.fInput, flex: 2 }} />
          <input type="number" step="0.001" placeholder="Qty" value={it.quantity} onChange={e => updateItem(i, 'quantity', e.target.value)} style={{ ...s.fInput, width: 54 }} />
          <input list="unit-list" placeholder="unit" value={it.unit} onChange={e => updateItem(i, 'unit', e.target.value)} style={{ ...s.fInput, width: 58 }} />
          <input type="number" step="0.01" placeholder="R" value={it.unit_price} onChange={e => updateItem(i, 'unit_price', e.target.value)} style={{ ...s.fInput, width: 76 }} />
          <input type="number" step="1" placeholder="-%" title="Line discount %" value={it.discount} onChange={e => updateItem(i, 'discount', e.target.value)} style={{ ...s.fInput, width: 48 }} />
          {items.length > 1 && <button onClick={() => removeItem(i)} style={s.rmBtn}>×</button>}
        </div>
      ))}
      <datalist id="prod-list">
        {catalogItems.map(c => <option key={c.id} value={c.name}>{`${c.kind === 'product' ? '📦' : '🛠'} R${(c.unit_price_cents / 100).toFixed(2)}${c.unit ? `/${c.unit}` : ''}`}</option>)}
        {products.map(p => <option key={p.id} value={p.name}>{`R${(p.price_cents / 100).toFixed(2)}${p.sold_by === 'kg' ? '/kg' : ''}`}</option>)}
      </datalist>
      <datalist id="section-list">
        {[...new Set(items.map(it => it.section).filter(Boolean))].map(sec => <option key={sec} value={sec} />)}
        {['Demolition', 'Structure', 'Finishes', 'Electrical', 'Plumbing', 'External Works'].map(sec => <option key={sec} value={sec} />)}
      </datalist>
      {items.some(it => it._tplDescription) && !project && (
        <p style={{ fontFamily: 'system-ui', fontSize: 11, color: '#a8780a', margin: '0 0 8px' }}>
          ⓘ Pick a project below to fill it into the highlighted line item(s).
        </p>
      )}
      <datalist id="unit-list">
        {['ea', 'no.', 'item', 'kg', 'm', 'm²', 'm³', 'lin.m', 'hr', 'day', '%'].map(u => <option key={u} value={u} />)}
      </datalist>
      <button onClick={addItem} style={s.addItemBtn}>+ Add line</button>

      <div style={s.fRow}>
        {docType === 'invoice' ? (
          <label style={s.dueLabel}>Due date <input type="date" value={dueDate} onChange={e => setDueDate(e.target.value)} style={s.fInput} /></label>
        ) : (
          <label style={s.dueLabel}>Valid until <input type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} style={s.fInput} /></label>
        )}
        <label style={s.dueLabel}>VAT % <input type="number" value={vatRate} onChange={e => setVatRate(parseFloat(e.target.value) || 0)} style={{ ...s.fInput, width: 60 }} /></label>
        <label style={s.dueLabel}>Discount % <input type="number" step="1" placeholder="0" value={discountPct} onChange={e => setDiscountPct(e.target.value)} style={{ ...s.fInput, width: 60 }} /></label>
        <label style={s.dueLabel}>Deposit R <input type="number" step="0.01" placeholder="0" value={deposit} onChange={e => setDeposit(e.target.value)} style={{ ...s.fInput, width: 76 }} /></label>
        {projectList.length > 0 && (
          <label style={s.dueLabel}>Project
            <input list="proj-list" placeholder="none" value={project} onChange={e => setProject(e.target.value)} style={{ ...s.fInput, width: 150 }} />
            <datalist id="proj-list">{projectList.map(p => <option key={p} value={p} />)}</datalist>
          </label>
        )}
      </div>

      <div style={s.totals}>
        <div style={s.totRow}><span>Subtotal</span><span>{fmt(subtotal)}</span></div>
        {invDiscCents > 0 && <div style={s.totRow}><span>Discount ({discountPct}%)</span><span>−{fmt(invDiscCents)}</span></div>}
        <div style={s.totRow}><span>VAT ({vatRate}%)</span><span>{fmt(vat)}</span></div>
        <div style={{ ...s.totRow, ...s.totFinal }}><span>{depositCents > 0 ? 'Total' : 'Total'}</span><span>{fmt(total)}</span></div>
        {depositCents > 0 && <div style={s.totRow}><span>Deposit paid</span><span>−{fmt(depositCents)}</span></div>}
        {depositCents > 0 && <div style={{ ...s.totRow, ...s.totFinal }}><span>Balance due</span><span>{fmt(balance)}</span></div>}
      </div>

      <button onClick={save} disabled={saving || !customer.name} style={s.saveInvBtn}>
        {saving ? 'Saving…' : (editingInvoice ? 'Save changes' : `Create ${docType}`)}
      </button>
    </div>
  )
}

// ── Partial credit note: pick which lines (and how much of each) to credit ───
// Defaults to every line fully checked, so a plain "Create credit note" click here is
// equivalent to the quick full-credit button — this only adds the ability to narrow it down.

function CreditNoteForm({ tenantId, invoice, onDone, onCancel }) {
  const rawItems = Array.isArray(invoice.line_items) ? invoice.line_items
    : (typeof invoice.line_items === 'string'
        ? (() => { try { return JSON.parse(invoice.line_items) } catch { return [] } })()
        : [])
  const [rows, setRows] = useState(rawItems.map(it => ({ ...it, checked: true, creditQty: it.quantity })))
  const [saving, setSaving] = useState(false)
  const [autoRefund, setAutoRefund] = useState(true)  // only shown/sent when the invoice was paid via Yoco
  const fmt = c => `R${((c || 0) / 100).toFixed(2)}`

  function toggle(i) { setRows(rows.map((r, idx) => idx === i ? { ...r, checked: !r.checked } : r)) }
  function setQty(i, val) { setRows(rows.map((r, idx) => idx === i ? { ...r, creditQty: val } : r)) }

  const creditItems = rows.filter(r => r.checked && (parseFloat(r.creditQty) || 0) > 0).map(r => {
    const qty = parseFloat(r.creditQty) || 0
    const cents = r.unit_price_cents || 0
    const disc = r.discount_pct || 0
    return {
      description: r.description, quantity: qty, unit: r.unit || '',
      unit_price_cents: cents, discount_pct: disc,
      total_cents: Math.round(cents * qty * (1 - disc / 100)),
      section: r.section || undefined,
    }
  })
  const vatRate = parseFloat(invoice.vat_rate) || 15
  const subtotal = creditItems.reduce((sum, i) => sum + i.total_cents, 0)
  const vat = Math.round(subtotal * vatRate / 100)
  const total = subtotal + vat
  const isFullCredit = rows.every(r => r.checked && parseFloat(r.creditQty) === parseFloat(r.quantity))

  async function save() {
    if (creditItems.length === 0) return
    setSaving(true)
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoices/${invoice.id}/credit-note`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ line_items: creditItems, auto_refund: !!invoice.yoco_checkout_id && autoRefund }),
    }).then(r => r.json()).catch(() => ({}))
    setSaving(false)
    if (d.credit_note) {
      let msg = `Credit note ${d.credit_note.invoice_number} created.`
      if (d.refund?.status === 'pending') msg += ` Refunded ${fmt(d.refund.amount_cents)} via Yoco.`
      else if (d.refund?.status === 'failed') msg += ` Automatic Yoco refund failed (${d.refund.detail || 'unknown error'}) — please refund manually.`
      alert(msg); onDone()
    }
    else alert(d.detail || 'Could not create credit note.')
  }

  return (
    <div>
      <div style={s.topBar}>
        <button onClick={onCancel} style={s.backBtn}>← Back</button>
        <h3 style={s.formTitle}>Credit note — {invoice.invoice_number}</h3>
      </div>
      <p style={{ fontFamily: 'system-ui', fontSize: 12, color: '#8A8680', margin: '0 0 10px' }}>
        Untick a line, or reduce its quantity, to credit only part of this invoice.
        {isFullCredit ? ' Every line is currently full — this will be a full credit note.' : ''}
      </p>
      {rows.map((r, i) => (
        <div key={i} style={{ ...s.itemRow, opacity: r.checked ? 1 : 0.45, alignItems: 'center' }}>
          <input type="checkbox" checked={r.checked} onChange={() => toggle(i)} style={{ marginRight: 2 }} />
          <span style={{ flex: 2, fontFamily: 'system-ui', fontSize: 13 }}>{r.description}</span>
          <input type="number" step="0.001" disabled={!r.checked} value={r.creditQty}
            onChange={e => setQty(i, e.target.value)} style={{ ...s.fInput, width: 60 }} />
          <span style={{ width: 70, fontSize: 12, color: '#8A8680', fontFamily: 'system-ui' }}>of {r.quantity} {r.unit || ''}</span>
          <span style={{ width: 80, textAlign: 'right', fontFamily: 'system-ui', fontSize: 13 }}>{fmt(r.unit_price_cents)}</span>
        </div>
      ))}
      <div style={s.totals}>
        <div style={s.totRow}><span>Subtotal</span><span>{fmt(subtotal)}</span></div>
        <div style={s.totRow}><span>VAT ({vatRate}%)</span><span>{fmt(vat)}</span></div>
        <div style={{ ...s.totRow, ...s.totFinal }}><span>Credit total</span><span>{fmt(total)}</span></div>
      </div>
      {invoice.yoco_checkout_id && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'system-ui', fontSize: 12, color: '#444', margin: '0 0 10px' }}>
          <input type="checkbox" checked={autoRefund} onChange={e => setAutoRefund(e.target.checked)} />
          This invoice was paid online via Yoco — also refund {fmt(total)} to the customer through Yoco now
        </label>
      )}
      <button onClick={save} disabled={saving || creditItems.length === 0} style={s.saveInvBtn}>
        {saving ? 'Creating…' : `Create credit note (${fmt(total)})`}
      </button>
    </div>
  )
}

// ── Products & services catalog: quick-add items for line items ──────────────
// A tenant-wide list of products/services with a default price. A service's description can
// carry a literal {project} token — InvoiceCreate substitutes it with whichever project is
// selected on the invoice, and re-substitutes it if the project is changed afterwards.

function InvoiceItemsCatalog({ tenantId, onDone }) {
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState({ kind: 'service', name: '', description: '', unit: '', unit_price: '' })
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-items`).then(r => r.json()).catch(() => ({}))
    setList(d.items || [])
    setLoading(false)
  }, [tenantId])
  useEffect(() => { load() }, [load])

  async function save() {
    if (!form.name.trim()) return
    setSaving(true)
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-items`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: form.kind, name: form.name.trim(), description: form.description.trim(),
          unit: form.unit.trim(), unit_price_cents: Math.round((parseFloat(form.unit_price) || 0) * 100),
        }),
      })
      setForm({ kind: 'service', name: '', description: '', unit: '', unit_price: '' })
      load()
    } finally {
      setSaving(false)
    }
  }

  async function del(id) {
    if (!confirm('Remove this item from the catalog?')) return
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-items/${id}`, { method: 'DELETE' })
    load()
  }

  return (
    <div>
      <div style={s.topBar}>
        <button onClick={onDone} style={s.backBtn}>← Back</button>
        <h3 style={s.formTitle}>Products &amp; services</h3>
      </div>
      <p style={s.muted}>
        Quick-add items for line items. In a description, write <code>{'{project}'}</code> and it'll
        be swapped for whichever project the invoice is for — and re-swapped if you change the project later.
      </p>

      <div style={s.formSection}>
        <div style={s.fRow}>
          <select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })} style={{ ...s.fInput, flex: '0 0 110px' }}>
            <option value="service">🛠 Service</option>
            <option value="product">📦 Product</option>
          </select>
          <input placeholder="Name (quick-pick label)" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} style={s.fInput} />
        </div>
        <input placeholder={'Invoice description, e.g. "Site supervision — {project}"'} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} style={s.fInput} />
        <div style={s.fRow}>
          <input type="number" step="0.01" placeholder="Unit price R" value={form.unit_price} onChange={e => setForm({ ...form, unit_price: e.target.value })} style={s.fInput} />
          <input list="cat-unit-list" placeholder="unit (ea/hr/day/m²…)" value={form.unit} onChange={e => setForm({ ...form, unit: e.target.value })} style={s.fInput} />
          <datalist id="cat-unit-list">{['ea', 'no.', 'item', 'kg', 'm', 'm²', 'm³', 'lin.m', 'hr', 'day', '%'].map(u => <option key={u} value={u} />)}</datalist>
        </div>
        <button onClick={save} disabled={saving || !form.name.trim()} style={s.saveInvBtn}>
          {saving ? 'Saving…' : '+ Add to catalog'}
        </button>
      </div>

      <p style={s.sectionLabel}>Catalog</p>
      {loading ? <p style={s.muted}>Loading…</p> : list.length === 0 ? <p style={s.muted}>No products or services yet.</p> : (
        <div style={s.list}>
          {list.map(it => (
            <div key={it.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', border: '1px solid #DDD8CE', borderRadius: 8, marginBottom: 8 }}>
              <div>
                <b>{it.kind === 'product' ? '📦' : '🛠'} {it.name}</b>
                <div style={{ fontSize: 12, color: '#8A8680' }}>
                  R{(it.unit_price_cents / 100).toFixed(2)}{it.unit ? `/${it.unit}` : ''}
                  {it.description ? ` · ${it.description}` : ''}
                </div>
              </div>
              <button onClick={() => del(it.id)} style={{ color: '#A23B2D', background: 'none', border: '1px solid #DDD8CE', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 12 }}>Remove</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Invoice settings: onboarding wizard + look-and-feel ──────────────────────

const TEMPLATES = [
  { id: 'classic', name: 'Classic', desc: 'Accent header, filled table — the original Vula look.', accent: 'var(--accent)' },
  { id: 'minimal', name: 'Minimal', desc: 'Monochrome, hairline rules, ink-light.', accent: '#222222' },
  { id: 'modern',  name: 'Modern',  desc: 'Bold colour band, rounded cards, high-contrast totals.', accent: '#0077b6' },
  { id: 'digg',    name: 'Certified payment', desc: 'JBCC-style: description/amount table, bold running subtotals, banking details box. Built for construction certified-payment invoices.', accent: '#2C5545' },
  { id: 'branded', name: 'Branded', desc: 'Logo-forward: centred logo, accent rules, your brand front-and-centre.', accent: '#2C5545' },
]

function RecurringManager({ tenantId, onCancel }) {
  const [list, setList] = useState([])
  const [form, setForm] = useState({ label: '', customer_name: '', customer_phone: '', customer_email: '', cadence: 'monthly', next_run_at: new Date().toISOString().slice(0, 10) })
  const [items, setItems] = useState([{ description: '', quantity: 1, unit_price: '' }])
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/recurring-invoices`).then(r => r.json())
    setList(d.recurring || [])
  }, [tenantId])
  useEffect(() => { load() }, [load])

  const upd = (i, f, v) => setItems(items.map((it, idx) => idx === i ? { ...it, [f]: v } : it))
  async function save() {
    if (!form.customer_name) return
    setSaving(true)
    const lineItems = items.map(it => { const c = Math.round((parseFloat(it.unit_price) || 0) * 100); return { description: it.description, quantity: parseFloat(it.quantity) || 0, unit_price_cents: c, total_cents: Math.round(c * (parseFloat(it.quantity) || 0)) } })
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/recurring-invoices`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, line_items: lineItems, vat_rate: 15 }) })
    setSaving(false); setForm({ ...form, label: '', customer_name: '', customer_phone: '', customer_email: '' }); setItems([{ description: '', quantity: 1, unit_price: '' }]); load()
  }
  async function del(id) { await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/recurring-invoices/${id}`, { method: 'DELETE' }); load() }

  return (
    <div>
      <div style={s.topBar}><button onClick={onCancel} style={s.backBtn}>← Back</button><h3 style={s.formTitle}>Recurring invoices</h3></div>
      <p style={s.muted}>Vula auto-generates these as <b>draft</b> invoices on the chosen cadence — you review and send.</p>
      <div style={s.formSection}>
        <input placeholder="Label (e.g. Monthly retainer — Sporty)" value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} style={s.fInput} />
        <input placeholder="Customer name" value={form.customer_name} onChange={e => setForm({ ...form, customer_name: e.target.value })} style={s.fInput} />
        <div style={s.fRow}>
          <input placeholder="Phone" value={form.customer_phone} onChange={e => setForm({ ...form, customer_phone: e.target.value })} style={s.fInput} />
          <input placeholder="Email" value={form.customer_email} onChange={e => setForm({ ...form, customer_email: e.target.value })} style={s.fInput} />
        </div>
        <div style={s.fRow}>
          <select value={form.cadence} onChange={e => setForm({ ...form, cadence: e.target.value })} style={s.fInput}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select>
          <input type="date" value={form.next_run_at} onChange={e => setForm({ ...form, next_run_at: e.target.value })} style={s.fInput} />
        </div>
      </div>
      <p style={s.sectionLabel}>Line items</p>
      {items.map((it, i) => (
        <div key={i} style={s.itemRow}>
          <input placeholder="Description" value={it.description} onChange={e => upd(i, 'description', e.target.value)} style={{ ...s.fInput, flex: 2 }} />
          <input type="number" placeholder="Qty" value={it.quantity} onChange={e => upd(i, 'quantity', e.target.value)} style={{ ...s.fInput, width: 60 }} />
          <input type="number" step="0.01" placeholder="R" value={it.unit_price} onChange={e => upd(i, 'unit_price', e.target.value)} style={{ ...s.fInput, width: 80 }} />
        </div>
      ))}
      <button onClick={() => setItems([...items, { description: '', quantity: 1, unit_price: '' }])} style={s.brandBtn}>+ line</button>
      <button onClick={save} disabled={saving} style={{ ...s.saveInvBtn, marginTop: 12 }}>{saving ? 'Saving…' : 'Add recurring invoice'}</button>

      <p style={s.sectionLabel}>Active recurring</p>
      {list.length === 0 ? <p style={s.muted}>None yet.</p> : list.map(r => (
        <div key={r.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', border: '1px solid #DDD8CE', borderRadius: 8, marginBottom: 8 }}>
          <div><b>{r.label || r.customer_name}</b><div style={{ fontSize: 12, color: '#8A8680' }}>{r.customer_name} · {r.cadence} · next {String(r.next_run_at).slice(0, 10)}</div></div>
          <button onClick={() => del(r.id)} style={{ color: '#A23B2D', background: 'none', border: '1px solid #DDD8CE', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 12 }}>Remove</button>
        </div>
      ))}
    </div>
  )
}

function InvoiceSettings({ tenantId, settings, firstRun, onDone, onCancel }) {
  const [form, setForm] = useState({
    company_name:       settings?.company_name || '',
    trading_as:         settings?.trading_as || '',
    logo_url:           settings?.logo_url || '',
    company_email:      settings?.company_email || '',
    company_phone:      settings?.company_phone || '',
    company_reg:        settings?.company_reg || '',
    vat_registered:     settings?.vat_registered ?? true,
    prices_include_vat: settings?.prices_include_vat ?? false,
    vat_number:         settings?.vat_number || '',
    registered_address: settings?.registered_address || '',
    account_name:       settings?.account_name || '',
    bank_name:          settings?.bank_name || '',
    branch_code:        settings?.branch_code || '',
    account_number:     settings?.account_number || '',
    template_choice:    settings?.template_choice || 'classic',
    menu_header_image_url: settings?.menu_header_image_url || '',
    accent_color:       /^#[0-9a-fA-F]{6}$/.test(settings?.accent_color || '') ? settings.accent_color : '#2C5545',
    ink_color:          /^#[0-9a-fA-F]{6}$/.test(settings?.ink_color || '') ? settings.ink_color : '#1E1E1E',
    font_pairing:       settings?.font_pairing || 'vula',
    footer_text:        settings?.footer_text || '',
    show_vat_breakdown: settings?.show_vat_breakdown ?? true,
    show_company_reg:   settings?.show_company_reg ?? true,
    logo_size:          settings?.logo_size || 'md',
    logo_align:         settings?.logo_align || 'left',
  })
  const [saving, setSaving] = useState(false)
  const [uploadingLogo, setUploadingLogo] = useState(false)
  const [uploadingMenuImage, setUploadingMenuImage] = useState(false)
  const [showCloneUpload, setShowCloneUpload] = useState(false)
  const [cloning, setCloning] = useState(false)
  const [cloneError, setCloneError] = useState(null)
  const [cloneApplied, setCloneApplied] = useState(null) // string[] of human labels, or null
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  async function uploadLogo(e) {
    const file = (e.target.files || [])[0]
    if (!file) return
    setUploadingLogo(true)
    try {
      const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
      const path = `${tenantId}/logo/${Date.now()}-${clean}`
      const { error } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
      if (!error) {
        const { data } = supabase.storage.from('product-images').getPublicUrl(path)
        if (data?.publicUrl) set('logo_url', data.publicUrl)
      }
    } finally { setUploadingLogo(false) }
  }

  async function uploadMenuImage(e) {
    const file = (e.target.files || [])[0]
    if (!file) return
    setUploadingMenuImage(true)
    try {
      const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
      const path = `${tenantId}/menu-header/${Date.now()}-${clean}`
      const { error } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
      if (!error) {
        const { data } = supabase.storage.from('product-images').getPublicUrl(path)
        if (data?.publicUrl) set('menu_header_image_url', data.publicUrl)
      }
    } finally { setUploadingMenuImage(false) }
  }

  const CLONE_FIELD_LABELS = {
    accent_color: 'Accent colour', ink_color: 'Text colour', logo_align: 'Logo position',
    logo_size: 'Logo size', font_pairing: 'Heading font', template_choice: 'Template',
    show_company_reg: 'Reg. number visibility', footer_text: 'Footer text',
  }

  async function cloneFromUpload(e) {
    const file = (e.target.files || [])[0]
    if (!file) return
    setCloning(true); setCloneError(null); setCloneApplied(null)
    try {
      const clean = file.name.replace(/[^a-zA-Z0-9.-]/g, '-').toLowerCase()
      const path = `${tenantId}/invoice-clone-source/${Date.now()}-${clean}`
      const { error: uploadErr } = await supabase.storage.from('product-images').upload(path, file, { cacheControl: '3600', upsert: true })
      if (uploadErr) { setCloneError('Upload failed — try again.'); return }
      const { data } = supabase.storage.from('product-images').getPublicUrl(path)
      if (!data?.publicUrl) { setCloneError('Upload failed — try again.'); return }

      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-settings/clone-from-upload`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_url: data.publicUrl }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setCloneError(d.detail || "Couldn't read that file — try a clearer photo or scan."); return }
      const suggested = d.suggested || {}
      Object.entries(suggested).forEach(([k, v]) => set(k, v))
      setCloneApplied(Object.keys(suggested).map(k => CLONE_FIELD_LABELS[k] || k))
      setShowCloneUpload(false)
    } catch {
      setCloneError("Couldn't read that file — try a clearer photo or scan.")
    } finally {
      setCloning(false)
    }
  }

  async function save() {
    setSaving(true)
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, onboarded: true }),
      })
    } finally {
      setSaving(false)
      onDone()
    }
  }

  return (
    <div>
      <div style={s.topBar}>
        <button onClick={onCancel} style={s.backBtn}>← Back</button>
        <h3 style={s.formTitle}>Invoice branding</h3>
      </div>

      {firstRun && (
        <p style={s.wizardIntro}>
          👋 Let's set up your invoices. Add your business and banking details so
          every invoice and quote you send is complete and ready for EFT payment.
        </p>
      )}

      <p style={s.sectionLabel}>Business details</p>
      <div style={s.formSection}>
        <input placeholder="Registered company name (appears on every invoice)" value={form.company_name}
          onChange={e => set('company_name', e.target.value)} style={s.fInput} />
        <input placeholder="Trading as (optional, e.g. brand name)" value={form.trading_as}
          onChange={e => set('trading_as', e.target.value)} style={s.fInput} />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ padding: '8px 14px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 8, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui' }}>
            {uploadingLogo ? 'Uploading…' : (form.logo_url ? '↻ Replace logo' : '📷 Upload logo')}
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={uploadLogo} style={{ display: 'none' }} />
          </label>
          {form.logo_url && <img src={form.logo_url} alt="logo" style={{ maxHeight: 44, maxWidth: 160, objectFit: 'contain' }} />}
          {form.logo_url && <button type="button" onClick={() => set('logo_url', '')} style={{ background: 'none', border: 'none', color: '#8A8680', cursor: 'pointer', fontSize: 18 }}>×</button>}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input placeholder="Business email" value={form.company_email}
            onChange={e => set('company_email', e.target.value)} style={s.fInput} />
          <input placeholder="Business phone" value={form.company_phone}
            onChange={e => set('company_phone', e.target.value)} style={s.fInput} />
        </div>
        <input placeholder="Company registration no. (optional)" value={form.company_reg}
          onChange={e => set('company_reg', e.target.value)} style={s.fInput} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text, #2A2A2A)', fontFamily: 'system-ui' }}>
          <input type="checkbox" checked={!!form.vat_registered} onChange={e => set('vat_registered', e.target.checked)} />
          We are VAT-registered (issue Tax Invoices @ 15%)
        </label>
        {form.vat_registered && <>
          <input placeholder="VAT / Tax registration number" value={form.vat_number}
            onChange={e => set('vat_number', e.target.value)} style={s.fInput} />
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--muted, #8A8680)', fontFamily: 'system-ui' }}>
            <input type="checkbox" checked={!!form.prices_include_vat} onChange={e => set('prices_include_vat', e.target.checked)} />
            My prices already include VAT
          </label>
        </>}
        <textarea placeholder="Registered physical address" value={form.registered_address}
          onChange={e => set('registered_address', e.target.value)} style={{ ...s.fInput, minHeight: 60, resize: 'vertical' }} />
      </div>

      <p style={s.sectionLabel}>Banking details (for EFT payments)</p>
      <div style={s.formSection}>
        <input placeholder="Account name" value={form.account_name}
          onChange={e => set('account_name', e.target.value)} style={s.fInput} />
        <div style={s.fRow}>
          <input placeholder="Bank" value={form.bank_name}
            onChange={e => set('bank_name', e.target.value)} style={s.fInput} />
          <input placeholder="Branch code" value={form.branch_code}
            onChange={e => set('branch_code', e.target.value)} style={s.fInput} />
        </div>
        <input placeholder="Account number" value={form.account_number}
          onChange={e => set('account_number', e.target.value)} style={s.fInput} />
      </div>

      <p style={s.sectionLabel}>WhatsApp menu</p>
      <div style={s.formSection}>
        <p style={{ fontFamily: 'system-ui', fontSize: 12, color: '#8A8680', margin: '0 0 4px' }}>
          Optional hero image sent right before the product menu when a customer messages you on WhatsApp.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ padding: '8px 14px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 8, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui' }}>
            {uploadingMenuImage ? 'Uploading…' : (form.menu_header_image_url ? '↻ Replace image' : '📷 Upload menu image')}
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={uploadMenuImage} style={{ display: 'none' }} />
          </label>
          {form.menu_header_image_url && <img src={form.menu_header_image_url} alt="menu header" style={{ maxHeight: 60, maxWidth: 200, objectFit: 'cover', borderRadius: 6 }} />}
          {form.menu_header_image_url && <button type="button" onClick={() => set('menu_header_image_url', '')} style={{ background: 'none', border: 'none', color: '#8A8680', cursor: 'pointer', fontSize: 18 }}>×</button>}
        </div>
      </div>

      <p style={s.sectionLabel}>Look &amp; feel</p>

      <div style={{ margin: '4px 0 14px' }}>
        {!showCloneUpload ? (
          <button type="button" onClick={() => { setShowCloneUpload(true); setCloneError(null); setCloneApplied(null) }}
            style={{ padding: '8px 14px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 8, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui' }}>
            ✨ Clone from an old invoice
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ padding: '8px 14px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', color: 'var(--accent, #2C5545)', border: '1px solid var(--accent, #2C5545)', borderRadius: 8, fontSize: 13, cursor: cloning ? 'default' : 'pointer', fontFamily: 'system-ui', opacity: cloning ? 0.6 : 1 }}>
              {cloning ? 'Analyzing…' : '📄 Upload a PDF or photo'}
              <input type="file" accept="application/pdf,image/png,image/jpeg,image/webp" disabled={cloning} onChange={cloneFromUpload} style={{ display: 'none' }} />
            </label>
            <button type="button" onClick={() => setShowCloneUpload(false)} disabled={cloning}
              style={{ background: 'none', border: '1px solid #DDD8CE', borderRadius: 8, padding: '8px 12px', fontSize: 13, cursor: 'pointer', color: '#8A8680', fontFamily: 'system-ui' }}>
              Cancel
            </button>
          </div>
        )}
        <p style={{ fontFamily: 'system-ui', fontSize: 11.5, color: '#8A8680', margin: '6px 0 0' }}>
          Upload an invoice you used before — Vula suggests matching colours, fonts and layout below. Nothing saves until you click "Save branding".
        </p>
        {cloneError && <div style={{ marginTop: 8, padding: '8px 12px', background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, fontSize: 12.5, color: '#A23B2D', fontFamily: 'system-ui' }}>{cloneError}</div>}
        {cloneApplied && (
          <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 12px', background: 'var(--accent-soft, rgba(44,85,69,0.1))', border: '1px solid var(--accent, #2C5545)', borderRadius: 6, fontSize: 12.5, color: 'var(--accent, #2C5545)', fontFamily: 'system-ui' }}>
            <span>{cloneApplied.length > 0 ? `Applied: ${cloneApplied.join(', ')} — review below and Save branding to keep them.` : "Couldn't confidently match anything from that file."}</span>
            <button type="button" onClick={() => setCloneApplied(null)} style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: 15, flexShrink: 0 }}>×</button>
          </div>
        )}
      </div>

      <div style={s.tplGrid}>
        {TEMPLATES.map(t => {
          const active = form.template_choice === t.id
          return (
            <div key={t.id} onClick={() => set('template_choice', t.id)}
              style={{ ...s.tplCard, ...(active ? s.tplCardActive : {}) }}>
              <div style={{ ...s.tplSwatch, background: t.accent }} />
              <div style={s.tplName}>{t.name}{active ? ' ✓' : ''}</div>
              <div style={s.tplDesc}>{t.desc}</div>
            </div>
          )
        })}
      </div>

      {/* Colour/font used to live only in general Settings' Brand Kit — same underlying row,
          duplicated here so the template swatches above actually mean something without leaving
          this screen. Editing either place stays in sync (both read/write commerce_invoice_settings). */}
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', margin: '10px 0' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#8A8680' }}>Accent</span>
          <input type="color" value={form.accent_color} onChange={e => set('accent_color', e.target.value)} style={{ width: 34, height: 30, padding: 2, border: '1px solid #DDD8CE', borderRadius: 6 }} />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#8A8680' }}>Text</span>
          <input type="color" value={form.ink_color} onChange={e => set('ink_color', e.target.value)} style={{ width: 34, height: 30, padding: 2, border: '1px solid #DDD8CE', borderRadius: 6 }} />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#8A8680' }}>Heading font</span>
          <select value={form.font_pairing} onChange={e => set('font_pairing', e.target.value)} style={{ ...s.fInput, flex: 'none', width: 180 }}>
            {Object.entries(FONT_PAIRINGS).map(([key, p]) => <option key={key} value={key}>{p.label}</option>)}
          </select>
        </div>
      </div>

      <p style={s.sectionLabel}>Logo</p>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', margin: '4px 0 14px' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#8A8680' }}>Size</span>
          <select value={form.logo_size} onChange={e => set('logo_size', e.target.value)} style={{ ...s.fInput, flex: 'none', width: 110 }}>
            <option value="sm">Small</option><option value="md">Medium</option><option value="lg">Large</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#8A8680' }}>Position</span>
          <select value={form.logo_align} onChange={e => set('logo_align', e.target.value)} style={{ ...s.fInput, flex: 'none', width: 110 }}>
            <option value="left">Left</option><option value="center">Centered</option>
          </select>
        </div>
      </div>

      <p style={s.sectionLabel}>Footer &amp; visibility</p>
      <div style={s.formSection}>
        <textarea placeholder="Custom footer note (blank = &quot;Thank you for your business.&quot;)" value={form.footer_text}
          onChange={e => set('footer_text', e.target.value)} style={{ ...s.fInput, minHeight: 50, resize: 'vertical' }} />
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#2A2A2A', fontFamily: 'system-ui' }}>
          <input type="checkbox" checked={!!form.show_vat_breakdown} onChange={e => set('show_vat_breakdown', e.target.checked)} />
          Show VAT breakdown on the invoice
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#2A2A2A', fontFamily: 'system-ui' }}>
          <input type="checkbox" checked={!!form.show_company_reg} onChange={e => set('show_company_reg', e.target.checked)} />
          Show company registration number
        </label>
      </div>

      <InvoicePreviewPane tenantId={tenantId} form={form} />

      <button onClick={save} disabled={saving} style={s.saveInvBtn}>
        {saving ? 'Saving…' : firstRun ? 'Save & start invoicing' : 'Save branding'}
      </button>
    </div>
  )
}

// ── Live PDF preview (2026-07-22 branding depth) — debounced re-render of the in-progress,
// UNSAVED settings against a fixed sample invoice, so a tenant sees the effect of every change
// (template/colour/font/logo/footer/visibility) before committing to Save. ────────────────────
function InvoicePreviewPane({ tenantId, form }) {
  const [url, setUrl] = useState(null)
  const [loading, setLoading] = useState(false)
  const urlRef = useRef(null)
  const timerRef = useRef(null)

  useEffect(() => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/invoice-settings/preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(form),
        })
        if (!r.ok) return
        const blob = await r.blob()
        if (urlRef.current) URL.revokeObjectURL(urlRef.current)
        const next = URL.createObjectURL(blob)
        urlRef.current = next
        setUrl(next)
      } catch { /* preview is best-effort — never blocks saving */ }
      setLoading(false)
    }, 600)
    return () => clearTimeout(timerRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, JSON.stringify(form)])

  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current) }, [])

  return (
    <div style={{ margin: '4px 0 18px' }}>
      <p style={s.sectionLabel}>Live preview {loading && <span style={{ color: '#8A8680', fontWeight: 400 }}>— updating…</span>}</p>
      <div style={{ border: '1px solid #DDD8CE', borderRadius: 8, overflow: 'hidden', height: 420, background: '#FAF9F6' }}>
        {url
          ? <embed src={url} type="application/pdf" style={{ width: '100%', height: '100%' }} />
          : <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#8A8680', fontSize: 13 }}>Preview loading…</div>}
      </div>
    </div>
  )
}

const s = {
  dirTabs:    { display: 'flex', gap: 8, marginBottom: 12 },
  dirTab:     { padding: '8px 16px', background: '#F7F4EE', border: '1px solid #DDD8CE', borderRadius: 20, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  dirTabActive:{ background: 'var(--accent, var(--accent))', borderColor: 'var(--accent, var(--accent))', color: '#fff' },
  dirFilterChip:{ padding: '8px 14px', background: 'rgba(212,160,23,0.12)', border: '1px solid rgba(212,160,23,0.35)', borderRadius: 20, fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', color: '#a8780a' },
  supplierBadge:{ marginLeft: 8, padding: '1px 8px', borderRadius: 10, fontSize: 10, fontWeight: 600, background: 'rgba(44,85,69,0.08)', color: 'var(--accent, var(--accent))' },
  tabs:       { display: 'flex', gap: 4, marginBottom: 16, borderBottom: '1px solid #DDD8CE', paddingBottom: 8 },
  tab:        { padding: '6px 16px', background: 'transparent', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  tabActive:  { background: 'rgba(44,85,69,0.1)', color: 'var(--accent, var(--accent))' },
  topBar:     { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 },
  count:      { fontFamily: 'system-ui', fontSize: 13, color: '#8A8680', margin: 0 },
  newBtn:     { padding: '8px 16px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  brandBtn:   { marginLeft: 'auto', padding: '8px 14px', background: 'transparent', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  backBtn:    { padding: '6px 12px', background: 'transparent', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 13, cursor: 'pointer', fontFamily: 'system-ui', color: '#8A8680' },
  formTitle:  { fontFamily: "'Cormorant Garamond', serif", fontSize: 20, fontWeight: 700, color: '#1E1E1E', margin: 0 },
  muted:      { color: '#8A8680', fontSize: 13, fontFamily: 'system-ui', textAlign: 'center', padding: '24px 0' },
  list:       { display: 'flex', flexDirection: 'column', gap: 8 },
  card:       { background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: '14px 16px' },
  cardTop:    { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  invNum:     { fontFamily: "'Source Code Pro', monospace", fontSize: 13, fontWeight: 600, color: '#1E1E1E', marginRight: 8 },
  amount:     { fontFamily: 'system-ui', fontSize: 15, fontWeight: 700, color: 'var(--accent, var(--accent))' },
  badge:      { padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600 },
  cust:       { fontFamily: 'system-ui', fontSize: 13, color: '#444', margin: '2px 0' },
  dates:      { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', margin: '0 0 8px' },
  cardActions:{ display: 'flex', gap: 6 },
  actWa:      { padding: '5px 10px', background: 'rgba(37,211,102,0.1)', color: '#1da851', border: '1px solid rgba(37,211,102,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actPdf:     { padding: '5px 10px', background: 'rgba(0,119,182,0.08)', color: '#0077b6', border: '1px solid rgba(0,119,182,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actEmail:   { padding: '5px 10px', background: 'rgba(212,160,23,0.1)', color: '#a8780a', border: '1px solid rgba(212,160,23,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actPaid:    { padding: '5px 10px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui', fontWeight: 600 },
  actDisabled:{ padding: '5px 10px', background: '#F0EDE5', color: '#9C978C', border: '1px solid #DDD8CE', borderRadius: 6, fontSize: 12, cursor: 'not-allowed', fontFamily: 'system-ui' },
  balanceTag: { padding: '5px 10px', fontSize: 12, color: '#a8780a', fontFamily: 'system-ui', fontWeight: 600, alignSelf: 'center' },
  actDel:     { padding: '5px 10px', background: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  actMatch:   { padding: '5px 10px', background: 'rgba(44,85,69,0.08)', color: 'var(--accent, var(--accent))', border: '1px solid rgba(44,85,69,0.25)', borderRadius: 6, fontSize: 12, cursor: 'pointer', fontFamily: 'system-ui' },
  matchBanner:{ marginTop: 8, padding: '8px 10px', background: 'rgba(44,85,69,0.07)', border: '1px solid rgba(44,85,69,0.2)', borderRadius: 6, fontSize: 12, fontFamily: 'system-ui', color: 'var(--accent)' },
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
  totFinal:   { borderTop: '1px solid #DDD8CE', marginTop: 6, paddingTop: 8, fontWeight: 700, fontSize: 15, color: 'var(--accent, var(--accent))' },
  saveInvBtn: { width: '100%', padding: '12px', background: 'var(--accent, var(--accent))', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', fontFamily: 'system-ui' },
  wizardIntro:{ background: 'rgba(44,85,69,0.07)', border: '1px solid rgba(44,85,69,0.2)', borderRadius: 8, padding: '12px 14px', fontFamily: 'system-ui', fontSize: 13, lineHeight: 1.6, color: 'var(--accent)', margin: '0 0 16px' },
  tplGrid:    { display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' },
  tplCard:    { flex: '1 1 150px', minWidth: 150, background: '#fff', border: '1px solid #DDD8CE', borderRadius: 8, padding: 12, cursor: 'pointer' },
  tplCardActive:{ borderColor: 'var(--accent, var(--accent))', boxShadow: '0 0 0 2px rgba(44,85,69,0.2)' },
  tplSwatch:  { height: 28, borderRadius: 4, marginBottom: 8 },
  tplName:    { fontFamily: 'system-ui', fontSize: 13, fontWeight: 700, color: '#1E1E1E', marginBottom: 4 },
  tplDesc:    { fontFamily: 'system-ui', fontSize: 11, color: '#8A8680', lineHeight: 1.5 },
}
