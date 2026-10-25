/**
 * OpsV2.jsx — the redesigned Master › Health, Usage and Users (2026-10-19, UX redesign Phase F).
 * Same data and actions as VulaMasterPanel's HealthPanel / WeeklyDigestCard / UsagePanel /
 * UsersPanel (master/useMasterOps.js). States in words with a tone (no ✓ / ✗ / emoji), tables
 * as DataTables (stacked on a phone), times in SAST, USD with the rand beside it.
 */
import { CalendarClock, ClipboardCopy, Database, Eye, KeyRound, Mail, Plus, Send, ShieldCheck, Smartphone, Trash2, Waypoints } from 'lucide-react'
import { Button, Card, DataTable, EmptyState, Field, IconButton, Input, PageHeader, Select, Sheet, Skeleton, StatCard, StatusChip } from '../ui/index.jsx'
import { when } from './TenantDetailV2'
import { useLoginTemplate, useMasterHealth, useMasterUsage, useMasterUsers, useWeeklyDigest } from './useMasterOps'
import './master.css'

const loading = <Card><Skeleton lines={4} /></Card>
const TenantLink = ({ id, onViewDetail }) => (onViewDetail
  ? <button type="button" className="vms-link vms-mono" onClick={(e) => { e.stopPropagation(); onViewDetail(id) }}>{id}</button>
  : <span className="vms-mono">{id}</span>)

/* ── The WhatsApp login-code template, per WhatsApp account ── */
const TPL_TONE = { APPROVED: 'ok', PENDING: 'warn', IN_APPEAL: 'warn', REJECTED: 'danger', PAUSED: 'danger', DISABLED: 'danger', missing: 'danger', unknown: 'muted' }
const TPL_WORD = { APPROVED: 'Approved', PENDING: 'Meta is reviewing', IN_APPEAL: 'In appeal', REJECTED: 'Rejected', PAUSED: 'Paused', DISABLED: 'Disabled', missing: 'Not submitted', unknown: "Couldn't read" }
function LoginTemplateCard({ onError }) {
  const x = useLoginTemplate({ onError })
  if (!x.t) return null
  return (
    <Card className="vms-form">
      <div className="vms-head">
        <h3 className="vms-h vms-title-sm"><KeyRound size={16} aria-hidden="true" className="vms-ic" />Login code template</h3>
        {x.missing.length > 0 && <Button size="sm" Icon={Send} disabled={x.busy} onClick={x.submit}>{x.busy ? 'Submitting…' : 'Submit login template'}</Button>}
      </div>
      <p className="vms-muted">The WhatsApp login code reaches someone who hasn't messaged the business in 24 hours only once this template is approved. One approved copy covers every business on that WhatsApp account.</p>
      {x.msg && <p className="vms-ok" role="status">{x.msg}</p>}
      <ul className="vms-plain">
        {x.t.accounts.map(a => (
          <li key={a.account}><span className="vms-mono">{a.account}</span> {a.tenants.join(', ')}{' '}
            <StatusChip tone={TPL_TONE[a.status] || 'muted'}>{TPL_WORD[a.status] || a.status}</StatusChip></li>))}
        {x.t.no_account.length > 0 && <li className="vms-muted">No WhatsApp account on record for {x.t.no_account.join(', ')}</li>}
      </ul>
    </Card>
  )
}

/* ── Health ── */
export function HealthV2({ onError, onViewDetail }) {
  const x = useMasterHealth({ onError })
  const h = x.h
  if (!h) return loading
  const router = h.llm_router_24h || {}
  const localPct = router.total ? Math.round((router.local / router.total) * 100) : null
  const routing = h.skill_routing_24h || {}
  const vrl = h.vrl_health || {}
  const wf = h.webhook_failures_24h || {}
  const pending = (h.migration_state || []).filter(m => !m.applied)
  const lines = Array.isArray(h.whatsapp) ? h.whatsapp : []
  const jobs = Array.isArray(h.scheduled_jobs) ? h.scheduled_jobs.map((j, i) => ({ ...j, id: i })) : []
  return (
    <section className="vms" aria-label="Platform health">
      <div className="vms-stats">
        <StatCard variant="tile" label="LLM requests (24h)" value={router.total || 0}
          sub={localPct != null ? `${localPct}% local · ${router.cloud || 0} cloud` : '—'} />
        <StatCard variant="tile" label="Fell through to reasoning" value={routing.total ? `${routing.fallthrough_to_reasoning_pct}%` : '—'}
          sub={`${routing.total || 0} routed (24h)`} color={(routing.fallthrough_to_reasoning_pct || 0) > 15 ? 'var(--warn-text)' : undefined} />
        <StatCard variant="tile" label="Open escalations" value={h.escalations?.open ?? '—'}
          sub={h.escalations?.oldest ? `oldest ${when(h.escalations.oldest)}` : undefined} color={(h.escalations?.open || 0) > 0 ? 'var(--warn-text)' : undefined} />
        <StatCard variant="tile" label="Webhook failures (24h)" value={wf.count ?? '—'} color={(wf.count || 0) > 0 ? 'var(--danger-text)' : undefined} sub={wf.note} />
        <StatCard variant="tile" label="VRL verify events (30d)" value={vrl.total_events ?? '—'}
          sub={vrl.low_coverage ? 'Low coverage — too few events for a pass-rate to mean anything' : (vrl.last_event_at ? `last ${when(vrl.last_event_at)}` : undefined)}
          color={vrl.low_coverage ? 'var(--warn-text)' : undefined} />
      </div>

      {pending.length > 0 && (
        <Card className="vms-form vms-attn">
          <div className="vms-head">
            <h3 className="vms-h vms-title-sm"><Database size={16} aria-hidden="true" className="vms-ic" />Migrations not yet applied</h3>
            <Button size="sm" Icon={ClipboardCopy} onClick={x.copyPendingSql}>Copy SQL to apply</Button>
          </div>
          <p className="vms-muted">One script of every missing migration, in order. Each one is idempotent, so it's safe to run once in the Supabase SQL editor.</p>
          {x.migMsg && <p className="vms-ok" role="status">{x.migMsg}</p>}
          <ul className="vms-plain">{pending.map(m => <li key={m.migration}><b>{m.migration}</b> — {m.note} <span className="vms-muted">({m.table})</span></li>)}</ul>
        </Card>)}

      <h3 className="vms-h"><Smartphone size={16} aria-hidden="true" className="vms-ic" />WhatsApp lines</h3>
      <Card pad={0}>
        {/* One row per number — a business can have several (2026-10-25) */}
        <DataTable caption="WhatsApp lines" rows={lines.map(w => ({ ...w, id: w.phone_number_id || `${w.tenant_id}:${w.phone_number}` }))} empty="No WhatsApp lines." columns={[
          { key: 'tenant_id', label: 'Tenant', render: (w) => <TenantLink id={w.tenant_id} onViewDetail={onViewDetail} /> },
          { key: 'phone_number', label: 'Number', render: (w) => <span className="vms-mono">{w.phone_number}{w.is_primary ? ' ★' : ''}</span> },
          { key: 'purpose', label: 'For', render: (w) => w.purpose_label || '—' },
          { key: 'status', label: 'Status', render: (w) => <StatusChip tone={w.status === 'connected' ? 'ok' : 'danger'}>{w.status}</StatusChip> },
          { key: 'webhook', label: 'Webhook', render: (w) => <StatusChip tone={w.webhook_registered ? 'ok' : 'warn'}>{w.webhook_registered ? 'Registered' : 'Not registered'}</StatusChip> },
          { key: 'last_error', label: 'Last error', render: (w) => w.last_error ? <span className="vms-danger">{String(w.last_error).slice(0, 60)}</span> : '—' },
        ]} stack={{ title: (w) => w.tenant_id, detail: (w) => `${w.phone_number}${w.is_primary ? ' (main)' : ''} · ${w.purpose_label ? `${w.purpose_label} · ` : ''}${w.status}${w.webhook_registered ? '' : ' · webhook not registered'}` }} />
      </Card>

      <LoginTemplateCard onError={onError} />

      <div className="vms-head">
        <h3 className="vms-h"><CalendarClock size={16} aria-hidden="true" className="vms-ic" />Scheduler</h3>
        <span className="vms-muted">Leader <span className="vms-mono">{h.scheduler?.leader || '—'}</span></span>
        <StatusChip tone={h.scheduler?.lease_expired ? 'danger' : 'ok'}>{h.scheduler?.lease_expired ? 'Lease expired' : 'Lease healthy'}</StatusChip>
      </div>
      <Card pad={0}>
        <DataTable caption="Scheduled jobs" rows={jobs} empty="No scheduled jobs." columns={[
          { key: 'tenant_id', label: 'Tenant', render: (j) => <span className="vms-mono">{j.tenant_id}</span> },
          { key: 'job_type', label: 'Job' },
          { key: 'enabled', label: 'State', render: (j) => <StatusChip tone={j.enabled ? 'ok' : 'muted'}>{j.enabled ? 'On' : 'Off'}</StatusChip> },
          { key: 'time', label: 'Time', render: (j) => j.hour != null ? `${String(j.hour).padStart(2, '0')}:${String(j.minute || 0).padStart(2, '0')}` : '—' },
          { key: 'template_name', label: 'Template', render: (j) => <span className="vms-mono">{j.template_name || 'default'}</span> },
          { key: 'last_fired_at', label: 'Last fired', render: (j) => <span className="vms-muted">{when(j.last_fired_at)}</span> },
        ]} stack={{ title: (j) => `${j.tenant_id} · ${j.job_type}`, detail: (j) => `${j.enabled ? 'On' : 'Off'} · last fired ${when(j.last_fired_at)}` }} />
      </Card>

      <div className="vms-head">
        <h3 className="vms-h"><ShieldCheck size={16} aria-hidden="true" className="vms-ic" />Qdrant backups (DR)</h3>
        <Button size="sm" variant="secondary" disabled={x.qbBusy} onClick={x.runQdrantBackup}>{x.qbBusy ? 'Running…' : 'Run backup now'}</Button>
      </div>
      <Card pad={0}>
        <DataTable caption="Qdrant backups" rows={(x.qb || []).map(r => ({ ...r, id: r.tenant_id }))} empty="No backups recorded yet." columns={[
          { key: 'tenant_id', label: 'Tenant', render: (r) => <span className="vms-mono">{r.tenant_id}</span> },
          { key: 'last_backup_status', label: 'Status', render: (r) => <StatusChip tone={r.last_backup_status === 'ok' ? 'ok' : 'danger'}>{r.last_backup_status || '—'}</StatusChip> },
          { key: 'last_backup_at', label: 'Last run', render: (r) => <span className="vms-muted">{when(r.last_backup_at)}</span> },
          { key: 'last_backup_error', label: 'Error', render: (r) => r.last_backup_error ? <span className="vms-danger">{String(r.last_backup_error).slice(0, 80)}</span> : '—' },
        ]} stack={{ title: (r) => r.tenant_id, detail: (r) => `${r.last_backup_status || '—'} · ${when(r.last_backup_at)}` }} />
      </Card>

      <div className="vms-two">
        <Card className="vms-form">
          <h3 className="vms-h"><Waypoints size={16} aria-hidden="true" className="vms-ic" />Routing detail (24h)</h3>
          <ul className="vms-plain">
            {Object.entries(router.escalation_reasons || {}).map(([r, n]) => <li key={r}>Escalated to cloud {n}× — {r}</li>)}
            {Object.entries(routing.by_match || {}).map(([r, n]) => <li key={r}>Skill match <b>{r}</b>: {n}</li>)}
            {!Object.keys(router.escalation_reasons || {}).length && !Object.keys(routing.by_match || {}).length && <li className="vms-muted">Nothing routed yet.</li>}
          </ul>
        </Card>
        <Card className="vms-form">
          <h3 className="vms-h"><ShieldCheck size={16} aria-hidden="true" className="vms-ic" />Verification by checker (30d)</h3>
          <ul className="vms-plain">
            {Object.entries(vrl.by_verifier || {}).map(([v, s]) => (
              <li key={v}><span className="vms-mono">{v}</span>: {s.accepted}/{s.total} accepted
                {s.defect_found > 0 && <span className="vms-warn"> · {s.defect_found} defect{s.defect_found === 1 ? '' : 's'} caught</span>}</li>))}
            {!Object.keys(vrl.by_verifier || {}).length && <li className="vms-muted">No verify events yet.</li>}
          </ul>
          {(wf.recent || []).length > 0 && <>
            <h4 className="vms-h">Recent webhook failures</h4>
            <ul className="vms-plain">{wf.recent.slice(0, 4).map((f, i) => (
              <li key={i} className="vms-muted">{when(f.created_at)} · {f.tenant_id || '?'} · {f.msg_type} — {String(f.error || '').slice(0, 60)}</li>))}</ul>
          </>}
        </Card>
      </div>

      <DigestV2 onError={onError} />
    </section>
  )
}

function DigestV2({ onError }) {
  const x = useWeeklyDigest({ onError })
  return (
    <Card className="vms-form">
      <div className="vms-head">
        <h3 className="vms-h vms-title-sm"><Mail size={16} aria-hidden="true" className="vms-ic" />Weekly digest</h3>
        <Button size="sm" variant="secondary" Icon={Eye} disabled={x.busy} onClick={x.preview}>Preview</Button>
        <Button size="sm" variant="secondary" Icon={Send} disabled={x.busy} onClick={x.send}>Send now</Button>
      </div>
      <p className="vms-muted">Emailed to TEAM_EMAIL every Monday morning.</p>
      {x.msg && <p className="vms-muted" role="status">{x.msg}</p>}
      {x.text && <pre className="vms-pre">{x.text}</pre>}
    </Card>
  )
}

/* ── Usage & billing ── */
export function UsageV2({ onError, onViewDetail }) {
  const { u } = useMasterUsage({ onError })
  if (!u) return loading
  const rate = u.usd_zar_rate || 18
  // providers bill in USD; the rand beside it is display only (USD_ZAR_RATE on the server)
  const money = (usd) => `US$${Number(usd || 0).toFixed(2)} · R${(Number(usd || 0) * rate).toFixed(2)}`
  const rows = Object.entries(u.per_tenant || {}).map(([id, t]) => ({ id, ...t }))
  if (!rows.length) return <EmptyState Icon={Database} title="No usage">No usage recorded in the last 14 days.</EmptyState>
  const cap = (n, c) => `${n ?? 0} / ${c != null ? c : '∞'}`
  const over = (n, c) => c != null && (n || 0) >= c
  return (
    <section className="vms" aria-label="Usage and billing">
      <PageHeader sub={`The last 14 days per tenant. Dollar amounts as billed, with the rand at R${rate} to the dollar.`} />
      <Card pad={0}>
        <DataTable caption="Usage per tenant" rows={rows} columns={[
          { key: 'id', label: 'Tenant', sortable: true, render: (t) => <TenantLink id={t.id} onViewDetail={onViewDetail} /> },
          { key: 'calls', label: 'AI calls', align: 'right', sortable: true },
          { key: 'ai_cost_usd', label: 'AI cost', sortable: true, sortValue: (t) => t.ai_cost_usd || 0, render: (t) => <span className="vula-money">{money(t.ai_cost_usd)}</span> },
          { key: 'infra_cost_usd', label: 'Infra / day', render: (t) => <span className="vula-money">{money(t.infra_cost_usd)}</span> },
          { key: 'vectors', label: 'Vectors', align: 'right', render: (t) => t.vectors ?? '—' },
          { key: 'storage_mb', label: 'Storage', align: 'right', render: (t) => t.storage_mb != null ? `${Number(t.storage_mb).toFixed(0)} MB` : '—' },
          { key: 'cap', label: 'Spend cap', render: (t) => t.spend_cap_usd != null
            ? <span className="vms-billing"><span className="vula-money">{money(t.spend_cap_usd)}/day</span>{t.capped_today && <StatusChip tone="danger">Capped today</StatusChip>}</span>
            : <span className="vms-muted">uncapped</span> },
          { key: 'docs', label: 'Documents', render: (t) => <span className={over(t.doc_count, t.doc_cap) ? 'vms-danger' : undefined}>{cap(t.doc_count, t.doc_cap)}</span> },
          { key: 'seats', label: 'Seats', render: (t) => <span className={over(t.seat_count, t.seat_cap) ? 'vms-danger' : undefined}>{cap(t.seat_count, t.seat_cap)}</span> },
        ]} stack={{ title: (t) => t.id, detail: (t) => `${t.calls} AI calls · ${money(t.ai_cost_usd)}`, trailing: (t) => (t.capped_today ? 'Capped' : '') }} />
      </Card>
    </section>
  )
}

/* ── Users ── */
export function UsersV2({ onError }) {
  const x = useMasterUsers({ onError })
  if (!x.users) return loading
  const rows = x.users.map((r, i) => ({ ...r, id: `${r.tenant_id}:${r.user_id}:${i}` }))
  return (
    <section className="vms" aria-label="Users">
      <PageHeader sub="Every dashboard login across Vula. Passwords are never shown — a reset gives a one-time temporary one."
        actions={<Button Icon={Plus} onClick={() => x.setCreating(true)}>New login</Button>} />
      {x.notice && (
        <Card className="vms-row vms-notice" role="status">
          <span className="vms-ok vms-grow">{x.notice}</span>
          <Button size="sm" variant="quiet" onClick={() => x.setNotice('')}>Dismiss</Button>
        </Card>)}
      <Card pad={0}>
        <DataTable caption="Logins" rows={rows} empty="No login accounts yet." columns={[
          { key: 'email', label: 'Email', sortable: true, render: (r) => r.email || r.user_id },
          { key: 'tenant_id', label: 'Tenant', sortable: true, render: (r) => <span className="vms-mono">{r.tenant_id}</span> },
          { key: 'role', label: 'Role', render: (r) => <StatusChip tone={r.role === 'master' ? 'accent' : 'muted'}>{r.role}</StatusChip> },
          { key: 'created_at', label: 'Created', render: (r) => <span className="vms-muted">{(r.created_at || '').slice(0, 10)}</span> },
          { key: 'actions', label: 'Actions', render: (r) => (
            <span className="vms-row">
              <Button size="sm" variant="secondary" Icon={KeyRound} disabled={x.busy === r.user_id} onClick={() => x.reset(r)}
                aria-label={`Reset the password for ${r.email || r.user_id}`}>Reset password</Button>
              <Button size="sm" variant="quiet" Icon={Trash2} className="vms-danger" disabled={x.busy === r.user_id} onClick={() => x.remove(r)}
                aria-label={`Remove ${r.email || r.user_id}`}>Remove</Button>
            </span>) },
        ]} stack={{ title: (r) => r.email || r.user_id, detail: (r) => `${r.tenant_id} · ${r.role}`,
          trailing: (r) => (
            <span className="vms-row">
              <IconButton Icon={KeyRound} label={`Reset the password for ${r.email || r.user_id}`} disabled={x.busy === r.user_id} onClick={() => x.reset(r)} />
              <IconButton Icon={Trash2} quiet label={`Remove ${r.email || r.user_id}`} disabled={x.busy === r.user_id} onClick={() => x.remove(r)} />
            </span>) }} />
      </Card>

      <Sheet open={x.creating} title="New login" onClose={() => x.setCreating(false)}>
        {x.creating && (
          <form className="vms-form" onSubmit={(e) => { e.preventDefault(); x.create() }}>
            <Field label="Tenant">
              <Select value={x.form.tenant_id} onChange={e => x.setForm(f => ({ ...f, tenant_id: e.target.value }))}>
                {x.tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name || t.tenant_id}</option>)}
              </Select>
            </Field>
            <Field label="Email"><Input type="email" value={x.form.email} onChange={e => x.setForm(f => ({ ...f, email: e.target.value }))} /></Field>
            <Field label="Name (optional)"><Input value={x.form.name} onChange={e => x.setForm(f => ({ ...f, name: e.target.value }))} /></Field>
            <Field label="Role">
              <Select value={x.form.role} onChange={e => x.setForm(f => ({ ...f, role: e.target.value }))}>
                <option value="owner">Owner</option><option value="manager">Manager</option><option value="staff">Staff</option>
              </Select>
            </Field>
            <div className="vms-row">
              <Button type="submit" disabled={x.busy === 'create'}>{x.busy === 'create' ? 'Creating…' : 'Create login'}</Button>
              <Button type="button" variant="secondary" onClick={() => x.setCreating(false)}>Cancel</Button>
            </div>
          </form>)}
      </Sheet>
    </section>
  )
}
