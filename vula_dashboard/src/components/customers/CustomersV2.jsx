/**
 * CustomersV2.jsx — the redesigned Customers (2026-10-18, UX redesign Phase F). Same data and
 * actions as the current list (customers/useCustomers.js). A table from 768px, stacked rows on a
 * phone (DataTable); a customer opens in a Sheet — a side panel on desktop, a bottom sheet on a
 * phone — instead of expanding under the row. The audience chips are the broadcast segments.
 */
import { Download, FileText, MessageCircle, Megaphone, Package, ReceiptText, Users } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { whatsAppLink } from '../../lib/phone'
import { Button, DataTable, EmptyState, Field, FilterBar, Input, ListRow, PageHeader, Sheet, Skeleton, StatCard, StatusChip, Textarea } from '../ui/index.jsx'
import FiledLibraryV2 from '../documents/FiledLibraryV2'
import { AUDIENCES, LANG_NAMES, seenAgo, useCustomers } from './useCustomers'
import './customers.css'

const initial = (c) => (c.name || c.phone || '?').charAt(0).toUpperCase()
const EVENT_ICONS = { order: Package, message: MessageCircle, quote: FileText }

function WhatsAppLink({ phone, name }) {
  return (
    <a className="vui-icon-btn vui-icon-btn--quiet vcu-wa" href={whatsAppLink(phone)} target="_blank" rel="noreferrer"
      aria-label={`WhatsApp ${name || phone}`} title="WhatsApp" onClick={(e) => e.stopPropagation()}>
      <MessageCircle size={20} strokeWidth={1.75} aria-hidden="true" />
    </a>
  )
}

export default function CustomersV2({ tenantId }) {
  const c = useCustomers(tenantId)
  const open = c.rows.find(r => r.phone === c.openPhone) || null
  const segment = AUDIENCES.find(a => a.id === c.audience)?.label

  const columns = [
    { key: 'name', label: 'Customer', sortable: true, sortValue: r => (r.name || '').toLowerCase(),
      render: r => (
        <span className="vcu-who">
          <span className="vui-disc" aria-hidden="true">{initial(r)}</span>
          <span className="vcu-who-text">
            <span className="vcu-name">{r.name || 'Unknown'}</span>
            <span className="vcu-sub">{r.phone}</span>
          </span>
        </span>) },
    { key: 'channel', label: 'Channel', render: r => (r.channel === 'whatsapp' ? 'WhatsApp' : 'Web') },
    { key: 'area', label: 'Area', render: r => r.area || '—' },
    { key: 'orders', label: 'Orders', align: 'right', sortable: true },
    { key: 'seen', label: 'Last seen', sortable: true, sortValue: r => -new Date(r.last_order_at || r.last_seen_at || 0),
      render: r => seenAgo(r.last_order_at || r.last_seen_at) },
    { key: 'total_spent_cents', label: 'Spent', align: 'right', sortable: true,
      render: r => <span className="vula-money">{formatMoney(r.total_spent_cents || 0)}</span> },
    { key: 'wa', label: <span className="vcu-sr">WhatsApp</span>, align: 'right', render: r => <WhatsAppLink phone={r.phone} name={r.name} /> },
  ]

  return (
    <div>
      {/* the shell's top bar already says "Customers" */}
      <PageHeader
        sub={`Everyone who has ordered or messaged you — captured automatically. ${c.totalAll} total contact${c.totalAll !== 1 ? 's' : ''}.`}
        actions={<Button variant="secondary" size="sm" Icon={Download} onClick={c.exportCsv} disabled={!c.rows.length}>Export CSV</Button>} />
      <FilterBar query={c.search} onQuery={c.setSearch} placeholder="Search name or phone" label="Broadcast segment"
        chips={AUDIENCES.filter(a => a.id !== 'all')} selected={c.audience === 'all' ? [] : [c.audience]}
        onToggle={id => c.setAudience(c.audience === id ? 'all' : id)} />
      <p className="vcu-reach" role="status">
        {c.loading ? 'Loading…' : `${c.count} customer${c.count !== 1 ? 's' : ''} in this segment — this is who a broadcast to "${segment}" reaches.`}
      </p>

      {!c.loading && c.rows.length === 0 ? (
        <EmptyState Icon={Users} title="No customers here yet">
          {c.search ? 'Nobody matches that search.' : 'Customers appear as soon as they order or message you.'}
        </EmptyState>
      ) : (
        <DataTable columns={columns} rows={c.rows} loading={c.loading} rowKey="phone" caption="Customers"
          onRowClick={c.openHistory}
          stack={{
            initials: initial,
            title: r => r.name || 'Unknown',
            detail: r => [r.phone, r.orders > 0 ? `${r.orders} order${r.orders !== 1 ? 's' : ''}` : 'no orders yet',
              `seen ${seenAgo(r.last_order_at || r.last_seen_at)}`].join(' · '),
            trailing: r => <span className="vula-money">{formatMoney(r.total_spent_cents || 0)}</span>,
          }} />
      )}

      <Sheet open={!!open} title={open ? (open.name || open.phone) : ''} onClose={c.closeHistory}
        footer={open ? (
          <div className="vcu-foot">
            <Button variant="secondary" Icon={FileText} onClick={() => c.printStatement(open.phone, open.name)}>Statement</Button>
            <a className="vui-btn vcu-wa-btn" href={whatsAppLink(open.phone)} target="_blank" rel="noreferrer">
              <MessageCircle size={18} strokeWidth={1.75} aria-hidden="true" />WhatsApp
            </a>
          </div>) : null}>
        {open && <CustomerDetail c={c} customer={open} tenantId={tenantId} />}
      </Sheet>
    </div>
  )
}

function CustomerDetail({ c, customer, tenantId }) {
  const d = c.detail
  if (!d) return <Skeleton lines={6} height={18} />
  const st = d.stats || {}
  const p = d.profile || {}
  return (
    <div className="vcu-detail">
      <p className="vcu-sub">{customer.phone}{customer.area ? ` · ${customer.area}` : ''}</p>
      <div className="vcu-stats">
        <StatCard variant="tile" label="Lifetime value" value={formatMoney(st.lifetime_value_cents || 0)} />
        <StatCard variant="tile" label="Paid orders" value={String(st.paid_order_count ?? 0)} />
        <StatCard variant="tile" label="Avg order" value={formatMoney(st.avg_order_cents || 0)} />
        {st.invoice_outstanding_cents > 0 &&
          <StatCard variant="tile" label="Owes (invoices)" value={formatMoney(st.invoice_outstanding_cents)} />}
      </div>

      {(p.email || p.preferred_language || p.first_order_at) && (
        <dl className="vcu-profile">
          {p.email && <><dt>Email</dt><dd>{p.email}</dd></>}
          {p.preferred_language && <><dt>Language</dt><dd>{LANG_NAMES[p.preferred_language] || p.preferred_language}</dd></>}
          {p.first_order_at && <><dt>First order</dt><dd>{String(p.first_order_at).slice(0, 10)}</dd></>}
        </dl>
      )}

      <Field label="Internal note" hint={c.savingNote ? 'Saving…' : 'Private — never sent to the customer.'}>
        <Textarea value={c.noteDraft} onChange={e => c.setNoteDraft(e.target.value)} onBlur={() => c.saveNote(customer.phone)}
          placeholder="Preferences, allergies, delivery quirks…" aria-label="Internal note" />
      </Field>
      <div className="vcu-tags">
        <Field label="Tags" hint={c.savingTags ? 'Saving…' : 'Broadcast segments filter on these.'}>
          <Input value={c.tagsDraft} onChange={e => c.setTagsDraft(e.target.value)} onBlur={() => c.saveTags(customer.phone)}
            placeholder="vip, wholesale, allergy-nuts…" aria-label="Tags" />
        </Field>
        <Field label="Area">
          <Input value={c.areaDraft} onChange={e => c.setAreaDraft(e.target.value)} onBlur={() => c.saveTags(customer.phone)}
            placeholder="e.g. Tableview" aria-label="Area" />
        </Field>
      </div>

      {(d.conversation || []).length > 0 && (
        <section>
          <h3 className="vcu-h">Conversation</h3>
          <div className="vcu-convo">
            {d.conversation.map((m, j) => (
              <div key={j} className={`vcu-bubble${m.role === 'user' ? '' : ' vcu-bubble--out'}`}>
                {m.text}
                <time>{String(m.at || '').slice(5, 16).replace('T', ' ')}</time>
              </div>
            ))}
          </div>
        </section>
      )}

      {(d.engagement || []).length > 0 && (
        <section>
          <h3 className="vcu-h">Broadcast engagement</h3>
          {d.engagement.map((g, j) => (
            <ListRow key={j} Icon={Megaphone} title={g.campaign}
              trailing={<StatusChip tone={g.status === 'clicked' ? 'ok' : g.status === 'failed' ? 'danger' : 'muted'}>{g.status}</StatusChip>} />
          ))}
        </section>
      )}

      <section>
        <h3 className="vcu-h">Interaction history</h3>
        {(d.events || []).length === 0 && <p className="vcu-sub">No recorded orders, invoices or chats yet.</p>}
        {(d.events || []).map((e, j) => (
          <ListRow key={j} Icon={EVENT_ICONS[e.type] || ReceiptText} title={e.title}
            detail={[(e.at || '').slice(0, 10), e.detail].filter(Boolean).join(' · ')}
            trailing={e.amount_cents != null ? <span className="vula-money">{formatMoney(e.amount_cents)}</span> : undefined} />
        ))}
      </section>

      {/* documents & media filed against this customer (migration 109) */}
      <section>
        <FiledLibraryV2 tenantId={tenantId} customerPhone={customer.phone} title="Documents & media" />
      </section>
    </div>
  )
}
