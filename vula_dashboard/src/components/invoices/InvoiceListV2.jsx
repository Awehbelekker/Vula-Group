/**
 * InvoiceListV2.jsx — the redesigned invoice / quote / credit-note list (2026-10-18, UX redesign
 * Phase F). VulaInvoices keeps all the state, the actions and the other screens (create, edit,
 * branding, recurring, partial credit) — this only draws the list, calling the same functions
 * through `api`. A table on desktop; one card per document on a tablet or phone. The occasional
 * actions (PDF, pay link, credit notes, match supplier, cancel, delete) sit behind "More", in a
 * Sheet (side panel / bottom sheet) instead of a popover.
 */
import { useState } from 'react'
import {
  Ban, Banknote, Check, CircleCheck, CreditCard, FileText, Link2, Mail, MessageCircle, MoreHorizontal,
  Paperclip, Plus, ReceiptText, Repeat, Scissors, Send, Settings, Trash2, Undo2, X,
} from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, DataTable, EmptyState, IconButton, ListRow, PageHeader, PillTabs, Select, Sheet, Skeleton, StatusChip } from '../ui/index.jsx'
import './invoices.css'

const STATUS = {
  draft:     { label: 'Draft',     tone: 'muted' },
  sent:      { label: 'Sent',      tone: 'info' },
  part_paid: { label: 'Part paid', tone: 'highlight' },
  paid:      { label: 'Paid',      tone: 'ok' },
  overdue:   { label: 'Overdue',   tone: 'warm' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
  accepted:  { label: 'Accepted',  tone: 'ok' },
  declined:  { label: 'Declined',  tone: 'danger' },
  expired:   { label: 'Expired',   tone: 'muted' },
}
const SENT_CHANNEL_LABEL = { email: 'via Email', whatsapp: 'via WhatsApp', manual: 'manually' }
const DOC_LABEL = { invoice: 'invoice', quote: 'quote', credit_note: 'credit note' }
const stop = (e) => e.stopPropagation()

function Chips({ inv }) {
  const st = STATUS[inv.status] || STATUS.draft
  return (
    <span className="vin2-chips">
      <StatusChip tone={st.tone}>{st.label}</StatusChip>
      {inv.requires_approval && (inv.approved_at
        ? <StatusChip tone="ok">Approved{inv.approved_by ? ` by ${inv.approved_by}` : ''}</StatusChip>
        : <StatusChip tone="warn">Awaiting approval</StatusChip>)}
      {inv.direction === 'inbound' && (inv.supplier_id
        ? <StatusChip outline tone="brand">Supplier matched</StatusChip>
        : <StatusChip tone="warn">Needs review</StatusChip>)}
    </span>
  )
}

function who(inv) {
  const name = inv.direction === 'inbound' ? (inv.supplier || inv.customer_name) : inv.customer_name
  return [name, inv.customer_phone].filter(Boolean).join(' · ')
}

function dates(inv) {
  return [
    `${inv.doc_type === 'quote' ? 'Quoted' : 'Issued'} ${inv.issue_date}`,
    inv.due_date && `Due ${inv.due_date}`,
    inv.valid_until && `Valid until ${inv.valid_until}`,
    inv.project,
    inv.sent_channel && `Sent ${SENT_CHANNEL_LABEL[inv.sent_channel] || inv.sent_channel}`,
  ].filter(Boolean).join(' · ')
}

/** The everyday actions, as on the current list; occasional ones open "More". */
function Actions({ inv, api, onMore }) {
  const invoiced = api.quoteInvoicedCents(inv)
  if (inv.direction === 'inbound') {
    return (
      <span className="vin2-actions" onClick={stop} onKeyDown={stop}>
        <Button size="sm" variant="secondary" Icon={Paperclip} onClick={() => api.openFiledDocument(inv)}>Original</Button>
        <Button size="sm" variant="secondary" Icon={Link2} disabled={api.matchingId === inv.id} onClick={() => api.matchSupplier(inv)}>
          {api.matchingId === inv.id ? 'Matching…' : 'Match supplier'}
        </Button>
        {inv.status !== 'paid' && <Button size="sm" Icon={Check} onClick={() => api.markPaid(inv)}>Mark paid</Button>}
        {api.projects?.length > 0 && (
          <Select value="" aria-label={`Move ${inv.invoice_number} to another project`} onChange={(e) => api.moveProject(inv, e.target.value)}>
            <option value="" disabled>{inv.project || 'No project'} · Move to…</option>
            {api.projects.filter((p) => p !== inv.project).map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
        )}
      </span>
    )
  }
  return (
    <span className="vin2-actions" onClick={stop} onKeyDown={stop}>
      {inv.customer_phone && <Button size="sm" variant="secondary" Icon={MessageCircle} onClick={() => api.sendWhatsApp(inv)}>WhatsApp</Button>}
      {inv.customer_email && (api.emailConfigured
        ? <Button size="sm" variant="secondary" Icon={Mail} onClick={() => api.emailInvoice(inv)}>Email</Button>
        : <Button size="sm" variant="secondary" Icon={Mail} disabled
            title="Email sending isn't set up for this account yet — ask your Vula admin to configure it. Use WhatsApp or download the PDF instead.">Email — not set up</Button>)}
      {inv.doc_type === 'invoice' && inv.status === 'draft' && (
        <Button size="sm" variant="secondary" Icon={Send} onClick={() => api.setStatus(inv, 'sent', { sent_channel: 'manual' })}
          title="Already sent this outside Vula (email/WhatsApp/in person)? Mark it sent so it shows up correctly on your dashboard and gets payment reminders.">Mark as sent</Button>
      )}
      {inv.doc_type === 'quote' && !['accepted', 'declined', 'expired'].includes(inv.status) && (
        <Button size="sm" variant="secondary" Icon={CircleCheck} onClick={() => api.setStatus(inv, 'accepted')}>Mark accepted</Button>
      )}
      {inv.doc_type === 'quote' && invoiced < (inv.total_cents || 0) && (inv.status === 'accepted'
        ? <Button size="sm" Icon={ReceiptText} onClick={() => api.convertToInvoice(inv)}>{invoiced > 0 ? 'Invoice remaining balance' : 'Convert to invoice'}</Button>
        : <Button size="sm" variant="secondary" disabled title="Mark this quote accepted before converting it to an invoice">Convert — mark accepted first</Button>)}
      {inv.doc_type === 'quote' && invoiced > 0 && invoiced < (inv.total_cents || 0) && (
        <StatusChip tone="highlight">Balance {formatMoney((inv.total_cents || 0) - invoiced)}</StatusChip>
      )}
      {inv.doc_type === 'invoice' && !['paid', 'cancelled'].includes(inv.status) && (
        <>
          <Button size="sm" variant="secondary" Icon={Banknote} onClick={() => api.recordPayment(inv)}>Record payment</Button>
          <Button size="sm" Icon={Check} onClick={() => api.markPaid(inv)}>Mark paid</Button>
        </>
      )}
      <IconButton Icon={MoreHorizontal} label={`More for ${inv.invoice_number}`} quiet className="vin2-more" onClick={() => onMore(inv)} />
    </span>
  )
}

function MoreSheet({ inv, api, onClose }) {
  const run = (fn) => () => { onClose(); fn() }
  return (
    <Sheet open={!!inv} title={inv ? `More for ${inv.invoice_number}` : ''} onClose={onClose}>
      {inv && (
        <div>
          <ListRow Icon={FileText} title="PDF" onClick={run(() => api.downloadPdf(inv))} />
          {inv.doc_type === 'invoice' && inv.status !== 'paid' &&
            <ListRow Icon={CreditCard} title="Pay link" detail="Creates a card-payment link and copies it" onClick={run(() => api.payLink(inv))} />}
          {inv.doc_type === 'invoice' && <ListRow Icon={Undo2} title="Credit note" onClick={run(() => api.creditNote(inv))} />}
          {inv.doc_type === 'invoice' && Array.isArray(inv.line_items) && inv.line_items.length > 1 &&
            <ListRow Icon={Scissors} title="Partial credit" onClick={run(() => api.setCreditingInvoice(inv))} />}
          {inv.doc_type === 'invoice' &&
            <ListRow Icon={Link2} title={api.matchingId === inv.id ? 'Matching…' : 'Match supplier'} onClick={run(() => api.matchSupplier(inv))} />}
          {inv.doc_type === 'invoice' && !['paid', 'part_paid', 'cancelled'].includes(inv.status) &&
            <ListRow Icon={Ban} title="Cancel" onClick={run(() => api.cancelInvoice(inv))} />}
          <ListRow Icon={Trash2} title="Delete" className="vin2-danger" onClick={run(() => api.del(inv.id))} />
        </div>
      )}
    </Sheet>
  )
}

function MatchBanner({ inv, api }) {
  const m = api.matchResults[inv.id]
  if (!m?.matched) return null
  return (
    <div className="vin2-match" role="status">
      {inv.invoice_number}: matched <strong>{m.supplier?.name}</strong> · {m.supplier?.payment_terms_days ?? 30} day terms · via {m.tier} ({Math.round((m.confidence || 0) * 100)}%)
      {m.auto_apply ? ' — applied automatically' : (
        <> — not confident enough to apply automatically.{' '}
          <Button size="sm" variant="secondary" Icon={Send} onClick={() => api.requestSupplierApproval(inv)}>Ask admin team on WhatsApp</Button>
        </>)}
    </div>
  )
}

/** The number: opens the editor (draft/sent) or the conversion (accepted quote), like the current list. */
function DocNumber({ inv, api }) {
  const editable = ['draft', 'sent'].includes(inv.status)
  const convertible = inv.doc_type === 'quote' && inv.status === 'accepted' && api.quoteInvoicedCents(inv) < (inv.total_cents || 0)
  if (!editable && !convertible) return <span className="vin2-num">{inv.invoice_number}</span>
  return (
    <button type="button" className="vin2-num vin2-num--link" onClick={(e) => { stop(e); editable ? api.edit(inv) : api.convertToInvoice(inv) }}
      title={editable ? 'Edit' : 'Convert to an invoice (full amount or a deposit)'}>{inv.invoice_number}</button>
  )
}

function QuotePick({ inv, api }) {
  if (!(inv.doc_type === 'quote' && (inv.status === 'draft' || inv.status === 'sent'))) return null
  return (
    <input type="checkbox" className="vin2-check" checked={api.selectedQuotes.has(inv.id)} onChange={() => api.toggleQuoteSelect(inv.id)}
      onClick={stop} aria-label={`Select ${inv.invoice_number} to delete`} />
  )
}

export default function InvoiceListV2({ api }) {
  const [more, setMore] = useState(null)
  const { invoices, loading, docType, direction, supplierFilter } = api
  const label = DOC_LABEL[docType] || docType

  const columns = [
    { key: 'pick', label: <span className="vin2-sr">Select</span>, width: 36, render: inv => <QuotePick inv={inv} api={api} /> },
    { key: 'invoice_number', label: 'Number', sortable: true, render: inv => <DocNumber inv={inv} api={api} /> },
    { key: 'who', label: direction === 'inbound' ? 'Supplier' : 'Customer', sortable: true, sortValue: who,
      render: inv => <span className="vin2-who"><span>{who(inv)}</span><span className="vin2-sub">{dates(inv)}</span></span> },
    { key: 'status', label: 'Status', render: inv => <Chips inv={inv} /> },
    { key: 'total_cents', label: 'Amount', align: 'right', sortable: true,
      render: inv => <span className="vula-money">{formatMoney(inv.total_cents, { cents: 'always' })}</span> },
    { key: 'actions', label: <span className="vin2-sr">Actions</span>, align: 'right', render: inv => <Actions inv={inv} api={api} onMore={setMore} /> },
  ]

  return (
    <div>
      <PageHeader sub={direction === 'inbound' ? 'Bills your suppliers sent you — scanned, emailed or WhatsApp\'d in.' : 'Invoices, quotes and credit notes you send.'}
        actions={<>
          {direction === 'outbound' && <Button variant="secondary" size="sm" Icon={Repeat} onClick={() => api.setShowRecurring(true)}>Recurring</Button>}
          <Button variant="secondary" size="sm" Icon={Settings} onClick={() => api.setShowSettings(true)}>Branding</Button>
          {direction === 'outbound' && docType !== 'credit_note' &&
            <Button size="sm" Icon={Plus} onClick={() => api.setShowCreate(docType)}>New {label}</Button>}
        </>} />

      <div className="vin2-switches">
        <PillTabs label="Direction" active={direction}
          tabs={[{ id: 'outbound', label: 'Sent to clients' }, { id: 'inbound', label: 'From suppliers' }]}
          onChange={(id) => { api.setDirection(id); if (id === 'outbound') api.clearSupplierFilter() }} />
        <PillTabs label="Document type" active={docType}
          tabs={[{ id: 'invoice', label: 'Invoices' }, { id: 'quote', label: 'Quotes' },
            ...(direction === 'outbound' ? [{ id: 'credit_note', label: 'Credit notes' }] : [])]}
          onChange={api.setDocType} />
        {supplierFilter && <Button size="sm" variant="secondary" Icon={X} onClick={api.clearSupplierFilter}>One supplier · clear</Button>}
      </div>
      <p className="vin2-count" role="status">{loading ? 'Loading…' : `${invoices.length} ${label}${invoices.length !== 1 ? 's' : ''}`}</p>

      {docType === 'quote' && api.selectedQuotes.size > 0 && (
        <div className="vin2-bulk" role="region" aria-label="Selected quotes">
          <span>{api.selectedQuotes.size} selected</span>
          <Button size="sm" variant="danger" Icon={Trash2} onClick={api.deleteSelectedQuotes} disabled={api.bulkDeleting}>
            {api.bulkDeleting ? 'Deleting…' : `Delete ${api.selectedQuotes.size} selected`}
          </Button>
          <Button size="sm" variant="quiet" onClick={() => api.setSelectedQuotes(new Set())}>Clear</Button>
        </div>
      )}

      {loading ? <Skeleton lines={5} height={18} /> : invoices.length === 0 ? (
        <EmptyState Icon={ReceiptText} title={`No ${label}s yet`}>
          {direction === 'inbound'
            ? `They'll appear here once scanned, emailed, or WhatsApp'd in.`
            : `Create one, or scan an existing document with the Smart Scanner.`}
        </EmptyState>
      ) : (
        <>
          <div className="vin2-table">
            <DataTable columns={columns} rows={invoices} caption={`${label}s`} />
            {invoices.map(inv => <MatchBanner key={inv.id} inv={inv} api={api} />)}
          </div>
          <div className="vin2-cards" role="list" aria-label={`${label}s`}>
            {invoices.map(inv => (
              <Card key={inv.id} pad={0} role="listitem" className="vin2-card">
                <div className="vin2-card-main">
                  <span className="vin2-card-top">
                    <QuotePick inv={inv} api={api} />
                    <DocNumber inv={inv} api={api} />
                    <span className="vula-money vin2-total">{formatMoney(inv.total_cents, { cents: 'always' })}</span>
                  </span>
                  <Chips inv={inv} />
                  <span className="vin2-who-line">{who(inv)}</span>
                  <span className="vin2-sub">{dates(inv)}</span>
                </div>
                <div className="vin2-card-foot"><Actions inv={inv} api={api} onMore={setMore} /></div>
                <MatchBanner inv={inv} api={api} />
              </Card>
            ))}
          </div>
        </>
      )}
      <MoreSheet inv={more} api={api} onClose={() => setMore(null)} />
    </div>
  )
}
