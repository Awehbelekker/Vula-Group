/**
 * TenantDetailV2.jsx — the redesigned Master › tenant drill-in (2026-10-19, UX redesign Phase F).
 * Same data and actions as VulaMasterTenantDetail (master/useTenantDetail.js): overview with the
 * manage form, linking a WhatsApp number and pushing the catalogue; usage; audit; open
 * escalations; conversations (thread → messages); errors. Times in SAST.
 */
import { ArrowLeft, ArrowRight, Building2, CircleAlert, Coins, MessageCircleQuestion, MessagesSquare, ScrollText, Send, Smartphone } from 'lucide-react'
import { useState } from 'react'
import { SA_TZ } from '../../lib/dates'
import { Button, Card, DataTable, EmptyState, Field, Input, PageHeader, PillTabs, Skeleton, StatCard, StatusChip } from '../ui/index.jsx'
import { ManageTenantV2 } from './TenantsV2'
import { useLinkWhatsAppNumber, useTenantAudit, useTenantConversations, useTenantErrors, useTenantEscalations, useTenantOverview,
  useTenantUsage, useWhatsAppCatalogue } from './useTenantDetail'
import './master.css'

const TABS = [
  { id: 'overview', label: 'Overview', Icon: Building2 },
  { id: 'usage', label: 'Usage', Icon: Coins },
  { id: 'audit', label: 'Audit', Icon: ScrollText },
  { id: 'escalations', label: 'Escalations', Icon: MessageCircleQuestion },
  { id: 'conversations', label: 'Conversations', Icon: MessagesSquare },
  { id: 'errors', label: 'Errors', Icon: CircleAlert },
]
export const when = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: SA_TZ })
}
const loading = <Card><Skeleton lines={3} /></Card>

export default function TenantDetailV2({ tenantId, onOpenTenant, onBack }) {
  const [tab, setTab] = useState('overview')
  const [err, setErr] = useState('')
  return (
    <section className="vms" aria-labelledby="vms-detail-h">
      {onBack && <div><Button size="sm" variant="quiet" Icon={ArrowLeft} onClick={onBack}>Tenants</Button></div>}
      <div className="vms-head">
        <h2 id="vms-detail-h" className="vms-title vms-mono">{tenantId}</h2>
        {onOpenTenant && <Button Icon={ArrowRight} onClick={() => onOpenTenant(tenantId)}>Open as tenant</Button>}
      </div>
      <PillTabs label="Tenant" tabs={TABS} active={tab} onChange={(id) => { setTab(id); setErr('') }} />
      {err && <p className="vms-error" role="alert">{err}</p>}
      {tab === 'overview' && <Overview tenantId={tenantId} onError={setErr} />}
      {tab === 'usage' && <Usage tenantId={tenantId} onError={setErr} />}
      {tab === 'audit' && <Audit tenantId={tenantId} onError={setErr} />}
      {tab === 'escalations' && <Escalations tenantId={tenantId} onError={setErr} />}
      {tab === 'conversations' && <Conversations tenantId={tenantId} onError={setErr} />}
      {tab === 'errors' && <Errors tenantId={tenantId} onError={setErr} />}
    </section>
  )
}

function Overview({ tenantId, onError }) {
  const { tenant: t, registry, save } = useTenantOverview({ tenantId, onError })
  if (!t) return loading
  return (
    <>
      <Card className="vms-form">
        <div className="vms-row">
          <span className="vms-name">{t.display_name || t.tenant_id}</span>
          <span className="vms-muted">{t.business_type || '—'}</span>
          <StatusChip tone={t.paid ? 'ok' : 'warn'}>{t.paid ? 'Paid' : (t.signup_status || '—')}</StatusChip>
          <StatusChip tone={t.active === false ? 'danger' : 'ok'}>{t.active === false ? 'Suspended' : 'Active'}</StatusChip>
          <span className="vms-muted">{t.logins} logins</span>
        </div>
        <ManageTenantV2 key={t.tenant_id} tenant={t} registry={registry} onSave={save} />
      </Card>
      <LinkNumber tenantId={tenantId} />
      <Catalogue tenantId={tenantId} />
    </>
  )
}

function LinkNumber({ tenantId }) {
  const x = useLinkWhatsAppNumber({ tenantId })
  return (
    <Card className="vms-form">
      <h3 className="vms-h"><Smartphone size={16} aria-hidden="true" className="vms-ic" />Link a WhatsApp number on Vula’s account</h3>
      <p className="vms-muted">For a number added in WhatsApp Manager on Vula’s WhatsApp Business Account. Paste its phone number ID; add the 6-digit PIN only if Meta still needs the number registered. The PIN isn’t saved.</p>
      <form className="vms-grid" onSubmit={(e) => { e.preventDefault(); x.link() }}>
        <Field label="Phone number ID"><Input value={x.pnid} onChange={(e) => x.setPnid(e.target.value)} inputMode="numeric" /></Field>
        <Field label="PIN (optional)"><Input value={x.pin} onChange={(e) => x.setPin(e.target.value)} inputMode="numeric" type="password" autoComplete="off" /></Field>
        <div className="vms-row vms-end"><Button type="submit" disabled={x.busy || !x.pnid.trim()}>{x.busy ? 'Checking with Meta…' : 'Link number'}</Button></div>
      </form>
      {x.msg && <p className="vms-error" role="alert">{x.msg}</p>}
      {x.res && (
        <p className="vms-ok" role="status">
          Linked <b>{x.res.phone_number || x.res.phone_number_id}</b>{x.res.verified_name ? ` (${x.res.verified_name})` : ''} to {tenantId} ·
          Meta status {x.res.meta_status || '—'} · display name {x.res.name_status || '—'}
          {x.res.registered_now ? ' · registered for messaging now' : ''} · replies go out as {x.res.route_mode}.
        </p>)}
    </Card>
  )
}

function Catalogue({ tenantId }) {
  const x = useWhatsAppCatalogue({ tenantId })
  return (
    <Card className="vms-form">
      <h3 className="vms-h"><Send size={16} aria-hidden="true" className="vms-ic" />WhatsApp catalogue</h3>
      <p className="vms-muted">Customers browse photos, pick a colour and add to cart inside WhatsApp. Create the catalogue in Meta Commerce Manager, connect it to this business’s number (WhatsApp Manager › Catalogue), give Vula’s system user access, then paste its ID here. Leave it empty to re-push the saved one. Products re-push after every website import or Shopify sync.</p>
      <form className="vms-grid" onSubmit={(e) => { e.preventDefault(); x.push() }}>
        <Field label="Catalogue ID (optional)"><Input value={x.cid} onChange={(e) => x.setCid(e.target.value)} inputMode="numeric" /></Field>
        <div className="vms-row vms-end"><Button type="submit" disabled={x.busy}>{x.busy ? 'Sending to WhatsApp…' : 'Push products'}</Button></div>
      </form>
      {x.msg && <p className="vms-error" role="alert">{x.msg}</p>}
      {x.res && <p className="vms-ok" role="status">{x.res.synced} products sent to catalogue {x.res.catalog_id} · {x.res.colour_groups} ranges with a colour picker.</p>}
    </Card>
  )
}

function Usage({ tenantId, onError }) {
  const { u } = useTenantUsage({ tenantId, onError })
  if (!u) return loading
  const t = (u.per_tenant || {})[tenantId]
  if (!t) return <EmptyState Icon={Coins} title="No usage">No usage recorded for this tenant in the last 14 days.</EmptyState>
  const usd = (n) => `US$${(n || 0).toFixed(2)}`
  return (
    <div className="vms-stats">
      <StatCard variant="tile" label="AI calls (14 days)" value={t.calls} />
      <StatCard variant="tile" label="AI cost" value={usd(t.ai_cost_usd)} />
      <StatCard variant="tile" label="Infrastructure / day" value={usd(t.infra_cost_usd)} />
      <StatCard variant="tile" label="Vectors" value={t.vectors ?? '—'} />
      <StatCard variant="tile" label="Storage" value={t.storage_mb != null ? `${Number(t.storage_mb).toFixed(0)} MB` : '—'} />
    </div>
  )
}

function Audit({ tenantId, onError }) {
  const { events } = useTenantAudit({ tenantId, onError })
  if (!events) return loading
  return (
    <Card pad={0}>
      <DataTable caption="Admin actions" rows={events} empty="No admin actions recorded for this tenant yet." columns={[
        { key: 'created_at', label: 'When', render: (e) => <span className="vms-muted">{when(e.created_at)}</span> },
        { key: 'who', label: 'Who', render: (e) => e.actor_email || e.actor_id },
        { key: 'action', label: 'Action', render: (e) => <b>{e.action}</b> },
        { key: 'detail', label: 'Detail', render: (e) => <code className="vms-code">{JSON.stringify(e.detail)}</code> },
      ]} stack={{ title: (e) => e.action, detail: (e) => `${e.actor_email || e.actor_id} · ${when(e.created_at)}` }} />
    </Card>
  )
}

function Escalations({ tenantId, onError }) {
  const { rows } = useTenantEscalations({ tenantId, onError })
  if (!rows) return loading
  if (!rows.length) return <EmptyState Icon={MessageCircleQuestion} title="Nothing open">No open escalations for this tenant.</EmptyState>
  return (
    <ul className="vms-list" aria-label="Open escalations">
      {rows.map((e) => (
        <li key={e.id}><Card className="vms-form">
          <div className="vms-row"><span className="vms-mono vms-muted">{e.customer_phone}</span><span className="vms-muted vms-push">{when(e.created_at)}</span></div>
          <p className="vms-body">{e.question}</p>
        </Card></li>))}
    </ul>
  )
}

function Conversations({ tenantId, onError }) {
  const x = useTenantConversations({ tenantId, onError })
  if (x.openPhone) {
    return (
      <div className="vms-form">
        <div className="vms-row"><Button size="sm" variant="quiet" Icon={ArrowLeft} onClick={() => x.setOpenPhone(null)}>Threads</Button>
          <span className="vms-mono vms-muted">{x.openPhone}</span></div>
        {!x.messages ? loading : !x.messages.length ? <p className="vms-muted">No messages in this thread yet.</p> : (
          <ol className="vms-thread" aria-label={`Messages with ${x.openPhone}`}>
            {x.messages.map((m, i) => (
              <li key={i} className={`vms-msg vms-msg--${m.role === 'user' ? 'in' : 'out'}`}>
                <span className="vms-muted">{m.role === 'user' ? 'Client' : 'Vula AI'} · {when(m.created_at)}</span>
                <span className="vms-body">{m.text}</span>
              </li>))}
          </ol>)}
      </div>
    )
  }
  if (!x.threads) return loading
  if (!x.threads.length) return <EmptyState Icon={MessagesSquare} title="No conversations">No conversations recorded for this tenant yet.</EmptyState>
  return (
    <Card pad={0}>
      <DataTable caption="Conversations" rows={x.threads} rowKey="phone" onRowClick={(t) => x.openThread(t.phone)} columns={[
        { key: 'phone', label: 'Phone', render: (t) => <span className="vms-mono">{t.phone || '(portal chat)'}</span> },
        { key: 'last_message', label: 'Last message', render: (t) => <span className={t.last_role === 'assistant' ? 'vms-muted' : undefined}>{t.last_message}</span> },
        { key: 'last_at', label: 'When', render: (t) => <span className="vms-muted">{when(t.last_at)}</span> },
      ]} stack={{ title: (t) => t.phone || '(portal chat)', detail: (t) => t.last_message, trailing: (t) => when(t.last_at) }} />
    </Card>
  )
}

function Errors({ tenantId, onError }) {
  const { d } = useTenantErrors({ tenantId, onError })
  if (!d) return loading
  const failures = Array.isArray(d.webhook_failures) ? d.webhook_failures : []
  const flags = Array.isArray(d.verification_flags) ? d.verification_flags : []
  if (!failures.length && !flags.length) return <EmptyState Icon={CircleAlert} title="No errors">No errors or verification flags in the last {d.window_hours}h.</EmptyState>
  return (
    <div className="vms-form">
      {!!failures.length && <h3 className="vms-h">Webhook failures</h3>}
      {!!failures.length && (
        <Card pad={0}>
          <DataTable caption="Webhook failures" rows={failures} columns={[
            { key: 'created_at', label: 'When', render: (f) => <span className="vms-muted">{when(f.created_at)}</span> },
            { key: 'phone', label: 'Phone', render: (f) => <span className="vms-mono">{f.phone || '—'}</span> },
            { key: 'msg_type', label: 'Type', render: (f) => f.msg_type || '—' },
            { key: 'error', label: 'Error', render: (f) => <span className="vms-danger">{f.error}</span> },
          ]} stack={{ title: (f) => f.error, detail: (f) => `${f.msg_type || '—'} · ${when(f.created_at)}` }} />
        </Card>)}
      {!!flags.length && <h3 className="vms-h">Verification flags</h3>}
      {!!flags.length && (
        <Card pad={0}>
          <DataTable caption="Verification flags" rows={flags.map((f, i) => ({ ...f, id: i }))} columns={[
            { key: 'created_at', label: 'When', render: (f) => <span className="vms-muted">{when(f.created_at)}</span> },
            { key: 'task', label: 'Skill' },
            { key: 'outcome', label: 'Outcome', render: (f) => <StatusChip tone={f.outcome === 'defect_found' ? 'warn' : 'danger'}>{f.outcome}</StatusChip> },
          ]} stack={{ title: (f) => f.task, detail: (f) => when(f.created_at), trailing: (f) => f.outcome }} />
        </Card>)}
    </div>
  )
}
