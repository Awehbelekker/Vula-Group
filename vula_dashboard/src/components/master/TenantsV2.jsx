/**
 * TenantsV2.jsx — the redesigned Master › Tenants (2026-10-19, UX redesign Phase F). Same data and
 * actions as VulaMasterPanel's TenantsPanel (master/useMasterTenants.js): every tenant with its
 * health, last message, plan, billing and state, searchable and sortable; a tenant opens in a
 * Sheet with Details / Open as tenant / Suspend, billing, and the manage form. New tenant is a
 * Sheet too. Stacked rows on a phone (DataTable).
 */
import { ArrowRight, Ban, CalendarPlus, CircleCheck, Eye, Plus, Power, RotateCcw, XCircle } from 'lucide-react'
import { Button, Card, DataTable, Field, Input, PageHeader, Select, Sheet, StatusChip } from '../ui/index.jsx'
import { ago, matchesTenant, sortTenants, useManageTenant, useMasterTenants } from './useMasterTenants'
import './master.css'

const HEALTH = { red: ['danger', 'Needs attention'], amber: ['warn', 'Check'], green: ['ok', 'Fine'], off: ['muted', 'Off'] }
const why = (t) => (t.health_reasons || []).join(' · ') || 'All good'

export default function TenantsV2({ onError, onOpenTenant, onViewDetail, prefill, onConsumePrefill, onCreated }) {
  const x = useMasterTenants({ onError, prefill, onConsumePrefill, onCreated })
  const rows = sortTenants(x.rows.filter(t => matchesTenant(t, x.q)), x.sort)
  const open = x.rows.find(t => t.tenant_id === x.managing)
  const name = (t) => t.display_name || t.tenant_id

  const columns = [
    { key: 'name', label: 'Tenant', sortable: true, sortValue: name, render: (t) => (
      <span className="vms-who"><Dot t={t} /><span><span className="vms-name">{name(t)}</span><span className="vms-id">{t.tenant_id}</span></span></span>) },
    { key: 'business_type', label: 'Type', render: (t) => t.business_type || '—' },
    { key: 'last', label: 'Last message', sortable: true, sortValue: (t) => (t.last_activity ? new Date(t.last_activity).getTime() : 0),
      render: (t) => <span className={t.dormant ? 'vms-warn' : 'vms-muted'}>{ago(t.last_activity)}</span> },
    { key: 'plan', label: 'Plan', render: (t) => <span>{t.plan || 'starter'}<span className="vms-muted"> · {(t.modules || []).length} modules</span></span> },
    { key: 'paid', label: 'Billing', render: (t) => <Billing t={t} /> },
    { key: 'logins', label: 'Logins', align: 'right', sortable: true, sortValue: (t) => t.logins || 0 },
    { key: 'active', label: 'State', render: (t) => <StatusChip tone={t.active === false ? 'danger' : 'ok'}>{t.active === false ? 'Suspended' : 'Active'}</StatusChip> },
  ]

  return (
    <section className="vms" aria-label="Tenants">
      <PageHeader sub="Every business on Vula: health first, then who's quiet, paid or on trial."
        actions={<Button Icon={Plus} onClick={() => x.setCreating(true)}>New tenant</Button>} />
      <div className="vms-filters">
        <Field label="Search tenants" style={{ flex: '1 1 220px' }}>
          <Input value={x.q} onChange={e => x.setQ(e.target.value)} placeholder="Name, slug, type, email or plan" />
        </Field>
        <Field label="Order">
          <Select value={x.sort} onChange={e => x.setSort(e.target.value)}>
            <option value="health">Needs attention first</option>
            <option value="activity">Most recently active</option>
            <option value="name">Name</option>
          </Select>
        </Field>
        <p className="vms-summary">
          <StatusChip tone={x.rows.some(t => t.health === 'red') ? 'danger' : 'muted'}>{x.rows.filter(t => t.health === 'red').length} need attention</StatusChip>
          <StatusChip tone={x.rows.some(t => t.dormant) ? 'warn' : 'muted'}>{x.rows.filter(t => t.dormant).length} quiet 30+ days</StatusChip>
        </p>
      </div>
      <Card pad={0}>
        <DataTable caption="Tenants" rows={rows} rowKey="tenant_id" columns={columns} empty="No tenant matches that search."
          onRowClick={(t) => x.setManaging(t.tenant_id)}
          stack={{ title: name, detail: (t) => `${t.tenant_id} · ${ago(t.last_activity)} · ${t.plan || 'starter'}`, trailing: (t) => <Dot t={t} /> }} />
      </Card>

      <Sheet open={!!open} title={open ? name(open) : ''} onClose={() => x.setManaging(null)}>
        {open && <TenantSheet t={open} x={x} onOpenTenant={onOpenTenant} onViewDetail={onViewDetail} />}
      </Sheet>

      <Sheet open={x.creating} title="New tenant" onClose={() => x.setCreating(false)}>
        {x.creating && (
          <form className="vms-form" onSubmit={(e) => { e.preventDefault(); x.create() }}>
            <Field label="Tenant ID" hint="A slug, e.g. my-shop. It can't be changed later.">
              <Input className="vms-mono" value={x.form.tenant_id} onChange={e => x.setForm(f => ({ ...f, tenant_id: e.target.value }))} />
            </Field>
            <Field label="Display name"><Input value={x.form.display_name} onChange={e => x.setForm(f => ({ ...f, display_name: e.target.value }))} /></Field>
            <Field label="Business type">
              <Select value={x.form.business_type} onChange={e => x.setForm(f => ({ ...f, business_type: e.target.value }))}>
                {(x.registry.business_types || []).map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
              </Select>
            </Field>
            <div className="vms-row">
              <Button type="submit" disabled={x.busy}>{x.busy ? 'Creating…' : 'Create tenant'}</Button>
              <Button type="button" variant="secondary" onClick={() => x.setCreating(false)}>Cancel</Button>
            </div>
          </form>)}
      </Sheet>
    </section>
  )
}

function Dot({ t }) {
  const [tone, label] = HEALTH[t.health] || HEALTH.green
  return <span className={`vms-dot vms-dot--${tone}`} role="img" aria-label={`Health: ${label} — ${why(t)}`} title={why(t)} />
}

function Billing({ t }) {
  if (t.paid) return <StatusChip tone="ok">Paid</StatusChip>
  const s = t.signup_status || '—'
  return (
    <span className="vms-billing">
      <StatusChip tone={s === 'cancelled' ? 'danger' : 'warn'}>{s}</StatusChip>
      {t.trial_ends && <span className="vms-muted">until {String(t.trial_ends).slice(0, 10)}</span>}
    </span>
  )
}

function TenantSheet({ t, x, onOpenTenant, onViewDetail }) {
  const [tone, label] = HEALTH[t.health] || HEALTH.green
  return (
    <div className="vms-form">
      <dl className="vms-rows">
        <div><dt>Health</dt><dd><StatusChip tone={tone}>{label}</StatusChip> <span className="vms-muted">{why(t)}</span></dd></div>
        <div><dt>Tenant ID</dt><dd className="vms-mono">{t.tenant_id}</dd></div>
        <div><dt>Last message</dt><dd>{ago(t.last_activity)}</dd></div>
        <div><dt>Billing</dt><dd><Billing t={t} /></dd></div>
        <div><dt>Logins</dt><dd>{t.logins}</dd></div>
      </dl>
      <div className="vms-row">
        {onViewDetail && <Button variant="secondary" Icon={Eye} onClick={() => onViewDetail(t.tenant_id)}>Details</Button>}
        {onOpenTenant && <Button Icon={ArrowRight} onClick={() => onOpenTenant(t.tenant_id)}>Open as tenant</Button>}
        <Button variant="quiet" Icon={t.active === false ? Power : Ban} className={t.active === false ? undefined : 'vms-danger'}
          onClick={() => x.toggleActive(t)}>{t.active === false ? 'Activate' : 'Suspend'}</Button>
      </div>

      <h3 className="vms-h">Billing</h3>
      <div className="vms-row">
        <Button size="sm" variant="secondary" Icon={CircleCheck} onClick={() => x.markPaid(t)}>Mark paid</Button>
        <Button size="sm" variant="secondary" Icon={CalendarPlus} onClick={() => x.extendTrial(t)}>+14 day trial</Button>
        {t.signup_status === 'cancelled'
          ? <Button size="sm" variant="secondary" Icon={RotateCcw} onClick={() => x.reactivateSub(t)}>Reactivate</Button>
          : <Button size="sm" variant="quiet" Icon={XCircle} className="vms-danger" onClick={() => x.cancelSub(t)}>Cancel subscription</Button>}
      </div>

      <h3 className="vms-h">Manage</h3>
      <ManageTenantV2 key={t.tenant_id} tenant={t} registry={x.registry} onSave={(patch) => x.saveManage(t.tenant_id, patch)} />
    </div>
  )
}

/** The redesign's manage form (plan, store, gateway, spend cap, modules, sharing, design) — same
 * fields and PATCH body as ManageTenantRow (useManageTenant). Also used by the tenant detail. */
export function ManageTenantV2({ tenant, registry, onSave }) {
  const m = useManageTenant(tenant, registry)
  return (
    <form className="vms-form" onSubmit={(e) => { e.preventDefault(); onSave(m.patch()) }}>
      <div className="vms-grid">
        <Field label="Plan">
          <Select value={m.plan} onChange={e => m.setPlan(e.target.value)}>
            <option value="starter">Starter</option><option value="growth">Growth</option><option value="business">Business</option>
          </Select>
        </Field>
        <Field label="Store URL"><Input value={m.storeUrl} onChange={e => m.setStoreUrl(e.target.value)} placeholder="https://…" /></Field>
        <Field label="Default gateway"><Input value={m.gateway} onChange={e => m.setGateway(e.target.value)} placeholder="yoco / payfast / …" /></Field>
        <Field label="Daily LLM spend cap (USD)" hint="Empty = uncapped. Over the cap, generation goes local-only for the day (never blocked) and the team is told on WhatsApp.">
          <Input value={m.spendCap} onChange={e => m.setSpendCap(e.target.value)} placeholder="uncapped" type="number" min="0" step="0.01" />
        </Field>
      </div>
      <fieldset className="vms-fieldset">
        <legend className="vms-h">Modules</legend>
        <div className="vms-mods">
          {m.allModules.map(mod => (
            <label key={mod.id} className="vms-mod"><input type="checkbox" checked={m.modules.includes(mod.id)} onChange={() => m.toggle(mod.id)} />{mod.label}</label>
          ))}
        </div>
      </fieldset>
      <label className="vms-check"><input type="checkbox" checked={m.shareNetwork} onChange={e => m.setShareNetwork(e.target.checked)} />
        Share this tenant's reviewed answers with other Vula businesses (opt-in — see Knowledge)</label>
      <label className="vms-check"><input type="checkbox" checked={m.uiV2} onChange={e => m.setUiV2(e.target.checked)} />
        New dashboard design (untick to put this business back on the old one)</label>
      <div className="vms-row"><Button type="submit">Save changes</Button></div>
    </form>
  )
}

