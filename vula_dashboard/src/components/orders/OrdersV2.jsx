/**
 * OrdersV2.jsx — the redesigned Orders list (2026-10-18, UX redesign Phase F). Same data and
 * actions as the current list (orders/useOrders.js). A table on desktop (row opens the order);
 * on a tablet or phone, one card per order so the next status stays one tap away. The order drawer and
 * the manual-order form are the current ones, passed in by VulaMerchantAdmin.
 */
import { ArrowRight, ClipboardList, Download, Package, Plus, Trash2 } from 'lucide-react'
import { formatMoney } from '../../lib/money'
import { Button, Card, DataTable, EmptyState, FilterBar, PageHeader, Skeleton, StatusChip } from '../ui/index.jsx'
import { NEXT_STATUSES, ORDER_DELETABLE, ORDER_FILTERS, useOrders } from './useOrders'
import './orders.css'

export const ORDER_STATUS = {
  pending_payment: { label: 'Awaiting payment', tone: 'warn' },
  paid:            { label: 'Paid',             tone: 'ok' },
  confirmed:       { label: 'Confirmed',        tone: 'info' },
  packing:         { label: 'Packing',          tone: 'info' },
  dispatched:      { label: 'Dispatched',       tone: 'brand' },
  delivered:       { label: 'Delivered',        tone: 'ok' },
  cancelled:       { label: 'Cancelled',        tone: 'danger' },
  refunded:        { label: 'Refunded',         tone: 'muted' },
}
const statusOf = (o) => ORDER_STATUS[o.status] || ORDER_STATUS.pending_payment
const when = (ts) => new Date(ts).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const stop = (e) => e.stopPropagation()

function Actions({ o, c }) {
  const busy = c.updating === o.id
  return (
    <span className="vor-actions" onClick={stop} onKeyDown={stop}>
      {(NEXT_STATUSES[o.status] || []).map(ns => (
        <Button key={ns} size="sm" variant={ns === 'cancelled' ? 'danger' : 'secondary'} disabled={busy} onClick={() => c.move(o, ns)}
          Icon={ns === 'cancelled' ? undefined : ArrowRight} aria-label={`Move ${o.display_id} to ${ORDER_STATUS[ns]?.label || ns}`}>
          {busy ? '…' : (ORDER_STATUS[ns]?.label || ns)}
        </Button>
      ))}
      {ORDER_DELETABLE.has(o.status) && (
        <Button size="sm" variant="danger" Icon={Trash2} disabled={busy} onClick={() => c.deleteOne(o)}>Delete</Button>
      )}
    </span>
  )
}

function Pick({ o, c }) {
  if (!ORDER_DELETABLE.has(o.status)) return null
  return (
    <input type="checkbox" className="vor-check" checked={c.selected.has(o.id)} onChange={() => c.toggleSelect(o.id)}
      onClick={stop} aria-label={`Select ${o.display_id} to delete`} />
  )
}

export default function OrdersV2({ tenantId, DetailDrawer, ManualOrderModal }) {
  const c = useOrders(tenantId)

  const columns = [
    { key: 'pick', label: <span className="vor-sr">Select</span>, width: 36, render: o => <Pick o={o} c={c} /> },
    { key: 'display_id', label: 'Order', sortable: true, render: o => <span className="vor-id">{o.display_id}</span> },
    { key: 'customer_name', label: 'Customer', sortable: true,
      render: o => <span className="vor-who"><span>{o.customer_name}</span><span className="vor-sub">{o.customer_phone}</span></span> },
    { key: 'status', label: 'Status', render: o => <StatusChip tone={statusOf(o).tone}>{statusOf(o).label}</StatusChip> },
    { key: 'delivery_slot', label: 'Slot', render: o => o.delivery_slot || '—' },
    { key: 'created_at', label: 'Placed', sortable: true, sortValue: o => -new Date(o.created_at),
      render: o => <span className="vor-sub">{when(o.created_at)} · {o.channel === 'whatsapp' ? 'WhatsApp' : 'Web'}</span> },
    { key: 'total_cents', label: 'Total', align: 'right', sortable: true,
      render: o => <span className="vula-money">{formatMoney(o.total_cents)}</span> },
    { key: 'actions', label: <span className="vor-sr">Actions</span>, align: 'right', render: o => <Actions o={o} c={c} /> },
  ]

  return (
    <div>
      <PageHeader sub="Paid, packed and sent — move each order on as it goes."
        actions={<>
          <Button variant="secondary" size="sm" Icon={Download} onClick={c.exportCsv} disabled={!c.orders.length}>Export CSV</Button>
          <Button size="sm" Icon={Plus} onClick={() => c.setShowManual(true)}>New order</Button>
        </>} />
      <FilterBar label="Status" chips={ORDER_FILTERS.filter(f => f.id !== 'all')}
        selected={c.filter === 'all' ? [] : [c.filter]} onToggle={id => c.setFilter(c.filter === id ? 'all' : id)} />

      {c.selected.size > 0 && (
        <div className="vor-bulk" role="region" aria-label="Selected orders">
          <span>{c.selected.size} selected</span>
          <Button size="sm" variant="danger" Icon={Trash2} onClick={c.deleteSelected} disabled={c.deleting}>
            {c.deleting ? 'Deleting…' : `Delete ${c.selected.size} selected`}
          </Button>
          <Button size="sm" variant="quiet" onClick={() => c.setSelected(new Set())}>Clear</Button>
        </div>
      )}

      {!c.loading && c.orders.length === 0 ? (
        <EmptyState Icon={Package} title="No orders here">
          {c.filter === 'all' ? 'Orders from WhatsApp and your shop land here.' : 'Nothing with this status right now.'}
        </EmptyState>
      ) : (
        <>
          <div className="vor-table">
            <DataTable columns={columns} rows={c.orders} loading={c.loading} caption="Orders" onRowClick={o => c.setDetailId(o.id)} />
          </div>
          <div className="vor-cards" role="list" aria-label="Orders">
            {c.loading && <Skeleton lines={4} height={18} />}
            {!c.loading && c.orders.map(o => (
              <Card key={o.id} pad={0} role="listitem" className="vor-card">
                <button type="button" className="vor-card-main" onClick={() => c.setDetailId(o.id)}>
                  <span className="vor-card-top">
                    <span className="vor-id">{o.display_id}</span>
                    <StatusChip tone={statusOf(o).tone}>{statusOf(o).label}</StatusChip>
                    <span className="vula-money vor-total">{formatMoney(o.total_cents)}</span>
                  </span>
                  <span className="vor-sub">{[o.customer_name, o.delivery_slot, o.channel === 'whatsapp' ? 'WhatsApp' : 'Web'].filter(Boolean).join(' · ')}</span>
                  <span className="vor-sub">{when(o.created_at)}</span>
                </button>
                <div className="vor-card-foot">
                  <Pick o={o} c={c} />
                  <Button size="sm" variant="quiet" Icon={ClipboardList} onClick={() => c.setDetailId(o.id)}>Details / pack</Button>
                  <Actions o={o} c={c} />
                </div>
              </Card>
            ))}
          </div>
        </>
      )}

      {c.detailId && <DetailDrawer tenantId={tenantId} orderId={c.detailId} onClose={() => c.setDetailId(null)} />}
      {c.showManual && (
        <ManualOrderModal tenantId={tenantId} onClose={() => c.setShowManual(false)}
          onCreated={() => { c.setShowManual(false); c.load() }} />
      )}
    </div>
  )
}
