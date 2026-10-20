/**
 * ImportV2.jsx — the redesigned Sell › Import (2026-10-20, UX redesign Phase F). Same three modes
 * and calls as VulaImport (import/useImport.js): past orders pulled out of pasted / uploaded chats
 * and emails, an old invoice list so bank payments match past invoice numbers, and learning tone +
 * common answers from WhatsApp chat exports. Nothing is created until the owner commits, and every
 * found order or invoice can be edited or removed first. Extracted amounts (rands) show through
 * formatMoney.
 */
import { ArrowLeft, BookOpenCheck, CheckCircle2, FileText, MessagesSquare, Receipt, Sparkles, Trash2, Upload } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, DataTable, EmptyState, Field, IconButton, Input, PillTabs, StatusChip, Textarea } from '../ui/index.jsx'
import { useChatLearn, useImport } from './useImport'
import './import.css'

const MODES = [
  { id: 'orders', label: 'Orders (chats / emails)', Icon: MessagesSquare },
  { id: 'invoices', label: 'Old invoices (Xero export)', Icon: Receipt },
  { id: 'learn', label: 'Learn from WhatsApp chats', Icon: Sparkles },
]
const rands = (n) => formatMoney(Math.round((Number(n) || 0) * 100), { cents: 'always' })
const plain = (t) => String(t || '').replace(/^✓\s*/, '')

export default function ImportV2({ tenantId }) {
  const x = useImport(tenantId)
  return (
    <section className="vim" aria-label="Import your history">
      <PillTabs label="What to import" tabs={MODES} active={x.mode} onChange={x.switchMode} />
      {x.mode === 'learn' ? <ChatLearnV2 tenantId={tenantId} /> : (
        <>
          <p className="vim-sub">
            {x.mode === 'invoices'
              ? 'Paste or upload your old invoice list (Xero: Business › Invoices › Export, or any pasted table). Vula registers your past invoice numbers so bank payments like “INVOICE 003983” match up. Unpaid ones stay open, paid ones go straight to history.'
              : 'Paste an old WhatsApp chat or emails, or upload a WhatsApp chat export. Vula pulls out the past orders, and you review them before anything is added. Nothing is created until you commit.'}
          </p>

          {!x.orders && !x.invoices && (
            <Card className="vim-card">
              <Field label={x.mode === 'invoices' ? 'Invoice list' : 'Chat or emails'}>
                <Textarea className="vim-text" value={x.text} onChange={e => x.setText(e.target.value)}
                  placeholder={x.mode === 'invoices' ? 'Paste your invoice export here…' : 'Paste WhatsApp chat / emails here…'} />
              </Field>
              <div className="vim-row">
                <input id="vim-file" className="vim-file" type="file" accept=".txt,.csv,text/plain,text/csv" onChange={e => x.onFile(e.target.files[0])} />
                <label htmlFor="vim-file" className="vim-upload"><Upload size={18} aria-hidden="true" />Upload {x.mode === 'invoices' ? '.csv export' : '.txt export'}</label>
                <Button Icon={FileText} disabled={x.busy} onClick={x.extract}>
                  {x.busy ? 'Reading…' : x.mode === 'invoices' ? 'Extract invoices' : 'Extract orders'}</Button>
              </div>
              {x.msg && <p className="vim-note" role="status">{plain(x.msg)}</p>}
            </Card>)}

          {x.orders && (
            <>
              <div className="vim-row">
                <Button variant="quiet" size="sm" Icon={ArrowLeft} onClick={() => x.setOrders(null)}>Back to text</Button>
                <span className="vim-count">{x.orders.length} order{x.orders.length === 1 ? '' : 's'} found</span>
              </div>
              {x.msg && <p className="vim-note" role="status">{plain(x.msg)}</p>}
              {x.orders.length === 0
                ? <EmptyState Icon={MessagesSquare} title="No orders detected in that text" />
                : <ul className="vim-list" aria-label="Orders found">
                    {x.orders.map((o, i) => (
                      <li key={i}>
                        <Card className="vim-card" role="group" aria-label={`Order ${i + 1}`}>
                          <div className="vim-grid">
                            <Field label="Customer"><Input value={o.customer_name || ''} onChange={e => x.upd(i, { customer_name: e.target.value })} /></Field>
                            <Field label="Phone"><Input type="tel" value={o.customer_phone || ''} onChange={e => x.upd(i, { customer_phone: e.target.value })} /></Field>
                            <Field label="Date"><Input type="date" value={o.date || ''} onChange={e => x.upd(i, { date: e.target.value })} /></Field>
                          </div>
                          <p className="vim-items">
                            {(o.items || []).map(it => `${it.quantity || 1}× ${it.name}${it.unit_price_rands ? ` @ ${rands(it.unit_price_rands)}` : ''}`).join(', ') || '—'}
                            {o.total_rands != null && <b className="vula-money"> · {rands(o.total_rands)}</b>}
                            {o.delivery_address && ` · ${o.delivery_address}`}
                          </p>
                          <div className="vim-row">
                            <label className="vim-check"><input type="checkbox" checked={!!o.paid} onChange={e => x.upd(i, { paid: e.target.checked })} />Paid</label>
                            <label className="vim-check"><input type="checkbox" checked={!!o.delivered} onChange={e => x.upd(i, { delivered: e.target.checked })} />Delivered</label>
                            <Button variant="secondary" size="sm" Icon={Trash2} className="vim-danger vim-push" onClick={() => x.drop(i)}>Remove</Button>
                          </div>
                        </Card>
                      </li>))}
                  </ul>}
              <div className="vim-actions vim-actions--sticky">
                <Button Icon={CheckCircle2} disabled={x.busy || !x.orders.length} onClick={x.commit}>
                  {x.busy ? 'Adding…' : `Commit ${x.orders.length} order${x.orders.length === 1 ? '' : 's'}`}</Button>
              </div>
            </>)}

          {x.invoices && (
            <>
              <div className="vim-row">
                <Button variant="quiet" size="sm" Icon={ArrowLeft} onClick={() => x.setInvoices(null)}>Back</Button>
                <span className="vim-count">{x.invoices.length} invoice{x.invoices.length === 1 ? '' : 's'} found</span>
              </div>
              {x.msg && <p className="vim-note" role="status">{plain(x.msg)}</p>}
              <DataTable caption="Invoices found" rows={x.invoices.map((v, i) => ({ ...v, _i: i }))} rowKey="_i" empty="No invoices detected in that text."
                columns={[
                  { key: 'invoice_number', label: 'Invoice #', render: v => <b>{v.invoice_number}</b> },
                  { key: 'customer_name', label: 'Customer', render: v => v.customer_name || '—' },
                  { key: 'date', label: 'Date', render: v => v.date || '—' },
                  { key: 'total_rands', label: 'Total', align: 'right', render: v => <span className="vula-money">{rands(v.total_rands)}</span> },
                  { key: 'status', label: 'Status', render: v => <StatusChip tone={v.status === 'paid' ? 'ok' : 'warn'}>{v.status || 'unpaid'}</StatusChip> },
                  { key: 'remove', label: '', align: 'right', render: v => (
                    <IconButton quiet Icon={Trash2} className="vim-danger" label={`Remove invoice ${v.invoice_number}`} onClick={() => x.dropInvoice(v._i)} />) },
                ]}
                stack={{
                  title: v => `${v.invoice_number}${v.customer_name ? ` · ${v.customer_name}` : ''}`,
                  detail: v => `${v.date || '—'} · ${v.status || 'unpaid'}`,
                  trailing: v => <span className="vim-trail"><span className="vula-money">{rands(v.total_rands)}</span>
                    <IconButton quiet Icon={Trash2} className="vim-danger" label={`Remove invoice ${v.invoice_number}`} onClick={() => x.dropInvoice(v._i)} /></span>,
                }} />
              <div className="vim-actions vim-actions--sticky">
                <Button Icon={CheckCircle2} disabled={x.busy || !x.invoices.length} onClick={x.commitInvoices}>
                  {x.busy ? 'Registering…' : `Register ${x.invoices.length} invoice${x.invoices.length === 1 ? '' : 's'}`}</Button>
              </div>
            </>)}
        </>)}
    </section>
  )
}

/** Learn tone + common answers from WhatsApp exports; answers wait for review in Settings. */
function ChatLearnV2({ tenantId }) {
  const l = useChatLearn(tenantId)
  return (
    <>
      <p className="vim-sub">
        Upload exported WhatsApp chats with your customers (in WhatsApp: open a chat › More › Export chat ›
        Without media). Vula learns how you write and the questions you answer most. You review every answer
        before it's used, the tone is only a suggestion, and the chats themselves aren't kept: customers'
        names and numbers are taken out.
      </p>
      <Card className="vim-card">
        <div className="vim-row">
          <input id="vim-chats" className="vim-file" type="file" multiple accept=".txt,.zip,text/plain,application/zip" onChange={e => l.pick(e.target.files)} />
          <label htmlFor="vim-chats" className="vim-upload"><Upload size={18} aria-hidden="true" />Choose chat exports (.txt or .zip)</label>
          <span className="vim-muted" role="status">{l.files.length ? `${l.files.length} chat${l.files.length === 1 ? '' : 's'} ready` : 'No chats chosen yet'}</span>
        </div>
        <div><Button Icon={BookOpenCheck} disabled={l.busy || !l.files.length} onClick={() => l.learn()}>
          {l.busy ? 'Reading your chats… (about a minute)' : 'Learn from these chats'}</Button></div>
        {l.err && <p className="vim-error" role="alert">{l.err}</p>}
      </Card>

      {l.res?.need_sender && (
        <Card className="vim-card" role="group" aria-label="Which of these is you?">
          <p className="vim-body">Which of these is <b>you</b> (the business)?</p>
          <div className="vim-row">
            {l.res.need_sender.map(s => <Button key={s} variant="secondary" size="sm" disabled={l.busy} onClick={() => l.learn(s)}>{s}</Button>)}
          </div>
        </Card>)}

      {l.res && !l.res.need_sender && (
        <Card className="vim-card" role="status">
          <h3 className="vim-h"><CheckCircle2 size={18} aria-hidden="true" className="vim-ok" />Read {l.res.chats} chat{l.res.chats === 1 ? '' : 's'}, {l.res.messages} messages</h3>
          <ul className="vim-facts">
            <li>You're “{l.res.business}”.</li>
            <li>{l.res.voice_samples} of your messages added to learn your tone.
              {l.res.tone_suggested ? ' A new tone suggestion is waiting in Settings › What Vula has learned.' : ' Not enough yet for a tone suggestion: add more chats.'}</li>
            <li>{l.res.answers_to_review} answer{l.res.answers_to_review === 1 ? '' : 's'} to review (from {l.res.pairs_found} questions customers asked).
              Keep the right ones in <b>Settings › What Vula has learned</b>. Only kept answers are used.</li>
          </ul>
        </Card>)}
    </>
  )
}
