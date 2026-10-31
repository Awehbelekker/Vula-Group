/**
 * ToolsV2.jsx — the redesigned Master › Onboard, Knowledge, Audit, Benchmark (+ go-live test),
 * Conversations (+ owner-advisor preview, retitle documents) and Models (2026-10-19, UX redesign
 * Phase F). Same data and actions as VulaMasterPanel's panels (master/useMasterTools.js). Tenant
 * ids are picked from a list instead of typed; states in words; times in SAST; no emoji.
 */
import { ArrowRight, Check, ChevronRight, ClipboardCopy, FileText, FlaskConical, Mail, MessagesSquare, Play, Rocket, Sparkles, ThumbsDown, UserPlus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { authFetch } from '../../lib/authFetch'
import { todaySA } from '../../lib/dates'
import { Button, Card, DataTable, EmptyState, Field, Input, PageHeader, Select, Skeleton, StatusChip } from '../ui/index.jsx'
import { when } from './TenantDetailV2'
import { useAdvisorPreview, useBenchmark, useConversationCheck, useGoLiveTest, useKnowledgeQueue, useMasterAudit, useMasterOnboard,
  useModelBakeoff, useRetitle } from './useMasterTools'
import './master.css'

const loading = <Card><Skeleton lines={4} /></Card>
const TenantLink = ({ id, label, onViewDetail }) => (onViewDetail && id
  ? <button type="button" className="vms-link" onClick={(e) => { e.stopPropagation(); onViewDetail(id) }}>{label || id}</button>
  : <span>{label || id || '—'}</span>)
const pctTone = (pct) => (pct >= 85 ? 'ok' : pct >= 60 ? 'warn' : 'danger')

/** The tenants as a labelled select (the go-live, advisor and retitle tools took a typed id). */
function useTenantOptions() {
  const [list, setList] = useState([])
  useEffect(() => { authFetch('/v1/master/tenants').then(d => setList(d.tenants || [])).catch(() => {}) }, [])
  return list
}
function TenantSelect({ label = 'Tenant', value, onChange, tenants }) {
  return (
    <Field label={label}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a tenant…</option>
        {tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name || t.tenant_id}</option>)}
      </Select>
    </Field>
  )
}

/* ── Onboard ── */
export function OnboardV2({ onError, onOpenTenant, onProvision, selectTenantId, onConsumeSelect }) {
  const x = useMasterOnboard({ onError, selectTenantId, onConsumeSelect })
  return (
    <section className="vms" aria-label="Onboard">
      {x.unprovisioned.length > 0 && (
        <Card className="vms-form vms-attn">
          <h3 className="vms-h"><UserPlus size={16} aria-hidden="true" className="vms-ic" />New signups — not yet provisioned ({x.unprovisioned.length})</h3>
          <ul className="vms-list" aria-label="Signups to provision">
            {x.unprovisioned.map((s, i) => (
              <li key={i} className="vms-row vms-item">
                <span className="vms-grow"><span className="vms-name">{s.company_name || s.contact_name || s.email}</span>
                  <span className="vms-muted"> · {s.contact_name} · {s.email}{s.whatsapp ? ` · ${s.whatsapp}` : ''} · {s.industry || '—'} · {s.plan || 'starter'}</span></span>
                <Button size="sm" Icon={ArrowRight} onClick={() => onProvision({
                  tenant_id: (s.company_name || s.contact_name || 'tenant').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, ''),
                  display_name: s.company_name || s.contact_name || '',
                  business_type: s.industry || 'retail', plan: s.plan || 'starter',
                })} aria-label={`Provision ${s.company_name || s.contact_name || s.email}`}>Provision</Button>
              </li>))}
          </ul>
        </Card>)}

      <div className="vms-filters">
        <Field label="Tenant" style={{ flex: '1 1 220px' }}>
          <Select value={x.selected} onChange={e => x.setSelected(e.target.value)}>
            {x.tenants.map(t => <option key={t.tenant_id} value={t.tenant_id}>{t.display_name || t.tenant_id}</option>)}
          </Select>
        </Field>
        {x.setup && <StatusChip tone={x.setup.progress_pct === 100 ? 'ok' : 'warn'}>{x.setup.done} of {x.setup.total} · {x.setup.progress_pct}%</StatusChip>}
        {onOpenTenant && x.selected && <Button variant="secondary" Icon={ArrowRight} onClick={() => onOpenTenant(x.selected)}>Open as tenant</Button>}
      </div>

      {!x.setup ? loading : (
        <Card className="vms-form">
          <div className="vms-meter" role="progressbar" aria-label="Go-live checklist" aria-valuenow={x.setup.progress_pct} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${x.setup.progress_pct}%` }} />
          </div>
          <ol className="vms-steps">
            {x.setup.steps.map((s, i) => (
              <li key={s.id} className={`vms-step${s.done ? ' vms-step--done' : ''}`}>
                <span className="vms-step-n" aria-hidden="true">{s.done ? <Check size={14} /> : i + 1}</span>
                <span className="vms-grow"><span className="vms-name">{s.label}</span>{s.done && <span className="vms-sr"> (done)</span>}
                  <span className="vms-muted vms-block">{s.detail}</span></span>
                {!s.done && onOpenTenant && <Button size="sm" variant="secondary" onClick={() => onOpenTenant(x.selected)} aria-label={`Do this: ${s.label}`}>Do this</Button>}
              </li>))}
          </ol>
          <p className="vms-muted">Computed live from the tenant's real state — connect or configure things and this updates itself.</p>
        </Card>)}
    </section>
  )
}

/* ── Knowledge ── */
export function KnowledgeV2({ onError, onViewDetail }) {
  const x = useKnowledgeQueue({ onError })
  if (!x.rows) return loading
  return (
    <section className="vms" aria-label="Knowledge">
      <PageHeader sub="Candidates for Vula's shared knowledge — Vula's own web research that already passed the accuracy check, plus tenant-approved answers. Promoting puts it in a collection every tenant's assistant can draw on; Shared network needs that tenant's opt-in." />
      {!x.rows.length ? <EmptyState Icon={Sparkles} title="Nothing to review">Nothing waiting for review right now.</EmptyState> : (
        <ul className="vms-list" aria-label="Answers to review">
          {x.rows.map(r => (
            <li key={r.id}><Card className="vms-form">
              <div className="vms-row">
                <TenantLink id={r.tenant_id} label={r.tenant_display_name || r.tenant_id} onViewDetail={onViewDetail} />
                <StatusChip tone={r.source === 'web_research' ? 'accent' : 'muted'}>{r.source === 'web_research' ? 'Vula research' : (r.source || '—')}</StatusChip>
              </div>
              <p className="vms-body"><b>{r.question}</b></p>
              <p className="vms-body vms-muted">{(r.answer || '').slice(0, 260)}</p>
              <div className="vms-filters">
                <Field label="Promote to">
                  <Select value={x.targets[r.id] || 'business'} onChange={e => x.setTargets(t => ({ ...t, [r.id]: e.target.value }))}>
                    <option value="business">General business KB</option>
                    <option value="construction">Construction KB</option>
                    <option value="network">Shared network (opt-in)</option>
                  </Select>
                </Field>
                <Button Icon={Sparkles} disabled={x.busy === r.id} onClick={() => x.promote(r)}>{x.busy === r.id ? 'Promoting…' : 'Promote'}</Button>
              </div>
            </Card></li>))}
        </ul>)}
    </section>
  )
}

/* ── Audit ── */
export function AuditV2({ onError, onViewDetail }) {
  const { events } = useMasterAudit({ onError })
  if (!events) return loading
  return (
    <Card pad={0}>
      <DataTable caption="Admin actions" rows={events} empty="No admin actions recorded yet — what you do here (suspend, edit modules, create users) shows up in this trail."
        columns={[
          { key: 'created_at', label: 'When', render: (e) => <span className="vms-muted">{when(e.created_at)}</span> },
          { key: 'who', label: 'Who', render: (e) => e.actor_email || e.actor_id },
          { key: 'action', label: 'Action', render: (e) => <b>{e.action}</b> },
          { key: 'tenant_id', label: 'Tenant', render: (e) => (e.tenant_id ? <TenantLink id={e.tenant_id} onViewDetail={onViewDetail} /> : '—') },
          { key: 'detail', label: 'Detail', render: (e) => <code className="vms-code">{JSON.stringify(e.detail)}</code> },
        ]} stack={{ title: (e) => e.action, detail: (e) => `${e.actor_email || e.actor_id} · ${e.tenant_id || '—'} · ${when(e.created_at)}` }} />
    </Card>
  )
}

/* ── Benchmark (+ go-live test) ── */
export function BenchmarkV2({ onError }) {
  return <section className="vms" aria-label="Benchmark"><GoLiveV2 onError={onError} /><Benchmark onError={onError} />
    <DecisionBench onError={onError} /></section>
}

/* ── Decision benchmark: Jev against today's classifiers (vula/decision_bench.py) ── */
const DECISION_LABEL = { skill: 'Which skill answers', escalation: 'Wants a person / upset', expense_purpose: 'Expense purpose' }
function DecisionBench({ onError }) {
  const [state, setState] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = () => authFetch('/v1/master/decision-bench').then(setState).catch(e => onError?.(e.message))
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!state?.running) return undefined
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [state?.running])
  const start = async () => {
    setBusy(true)
    try {
      const r = await authFetch('/v1/master/decision-bench', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) })
      if (r.detail) onError?.(r.detail)
      await load()
    } catch (e) { onError?.(e.message) } finally { setBusy(false) }
  }
  const s = state?.summary
  const rows = s ? Object.entries(s.results || {}).flatMap(([d, r]) => ['jev', 'current'].map(p => ({
    key: `${d}-${p}`, decision: DECISION_LABEL[d] || d, provider: p === 'jev' ? 'Jev' : 'Today', verdict: p === 'jev' ? r.verdict : '', ...(r[p] || {}) }))) : []
  const ms = (v) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)
  return (
    <>
      <Card className="vms-form">
        <h3 className="vms-h"><FlaskConical size={16} aria-hidden="true" className="vms-ic" />Decision benchmark — Jev against today</h3>
        <p className="vms-muted">About 600 made-up, labelled messages (which skill answers, wants a person / upset in seven languages, expense purpose) through Jev and through today's classifiers. Nothing is sent to anyone and no business data is used. Jev is recommended for a decision only within 2 points of today's accuracy and with under 20% fallback.</p>
        <Button Icon={Play} disabled={busy || state?.running} onClick={start}>
          {state?.running ? 'Running… (about 10 minutes)' : busy ? 'Starting…' : 'Run decision benchmark'}</Button>
        {state?.failed && <p className="vms-muted" role="alert">The last run stopped with an error — see the server log.</p>}
      </Card>
      {s && (
        <Card pad={0}>
          <DataTable caption={`Decision benchmark — ${when(s.finished_at)} · ${s.decision_model}`} rows={rows} rowKey="key" columns={[
            { key: 'decision', label: 'Decision' },
            { key: 'provider', label: 'Model' },
            { key: 'accuracy', label: 'Accuracy', render: (r) => (r.accuracy == null ? '—' : <StatusChip tone={pctTone(r.accuracy)}>{r.accuracy}%</StatusChip>) },
            { key: 'p50_ms', label: 'p50 / p95', render: (r) => `${ms(r.p50_ms)} / ${ms(r.p95_ms)}` },
            { key: 'fallback_rate', label: 'Fallback', render: (r) => (r.fallback_rate == null ? '—' : `${Math.round(r.fallback_rate * 100)}%`) },
            { key: 'cost_per_1k_usd', label: 'Cost / 1k', render: (r) => (r.cost_per_1k_usd == null ? '—' : `$${r.cost_per_1k_usd}`) },
            { key: 'by_lang', label: 'By language', render: (r) => <span className="vms-muted">{Object.entries(r.by_lang || {}).map(([l, a]) => `${l} ${a}%`).join(' · ')}{r.lagging_languages?.length ? ` — behind in ${r.lagging_languages.join(', ')}` : ''}</span> },
            { key: 'verdict', label: 'Verdict', render: (r) => (r.verdict ? <StatusChip tone={r.verdict === 'flip' ? 'ok' : 'warn'}>{r.verdict === 'flip' ? 'Jev ready' : r.verdict === 'keep current' ? 'Keep today' : r.verdict}</StatusChip> : '') },
          ]} />
        </Card>)}
    </>
  )
}

function GoLiveV2({ onError }) {
  const x = useGoLiveTest({ onError })
  const tenants = useTenantOptions()
  const r = x.res
  return (
    <Card className="vms-form">
      <h3 className="vms-h"><Rocket size={16} aria-hidden="true" className="vms-ic" />Go-live test (one tenant)</h3>
      <p className="vms-muted">Questions every business must get right — no invented figures, no delete or customer message without a confirm, answers from the owner's own profile or “I'll check”, hand-over to a person. Read-only. Ready at 85%.</p>
      <div className="vms-filters">
        <TenantSelect value={x.tenant} tenants={tenants} onChange={(v) => { x.setTenant(v); x.setRes(null); if (v) x.load(v) }} />
        <Button Icon={Play} disabled={!x.tenant || r?.status === 'running'} onClick={x.run}>{r?.status === 'running' ? 'Running…' : 'Run go-live test'}</Button>
      </div>
      {r?.status === 'done' && (
        <>
          <div className="vms-row"><StatusChip tone={r.ready ? 'ok' : 'danger'}>{r.ready ? 'Ready' : 'Not ready'}</StatusChip><b>{r.passed}/{r.total} ({r.pass_pct}%)</b></div>
          <Misses items={r.failures} />
        </>)}
      {r?.status === 'failed' && <p className="vms-error" role="alert">{r.error}</p>}
      {r?.status === 'none' && <p className="vms-muted">Not run yet for this tenant.</p>}
    </Card>
  )
}

function Misses({ items }) {
  if (!items?.length) return null
  return (
    <ul className="vms-list" aria-label="What it got wrong">
      {items.map((f, i) => (
        <li key={f.id || i} className="vms-miss">
          <b>“{f.prompt}”</b>
          <span className="vms-danger">{(f.why || []).join(' · ')}</span>
          {f.answer && <span className="vms-body vms-muted">{f.answer}</span>}
        </li>))}
    </ul>
  )
}

function Benchmark({ onError }) {
  const x = useBenchmark({ onError })
  if (!x.data) return loading
  const latest = x.data.latest
  const gapsFor = (comp) => (latest?.gaps || []).filter(g => g.component === comp)
  return (
    <>
      <Card className="vms-form">
        <h3 className="vms-h"><FlaskConical size={16} aria-hidden="true" className="vms-ic" />Capability benchmark</h3>
        <p className="vms-muted">Real questions on DIGG, Off the Hook and Gerflor through the same path WhatsApp uses, plus routing, WhatsApp commands and filing accuracy on real documents. Read-only: anything that would change data or send a message is recorded, not done. Rules first, then a separate judge model scores 1–5; a case passes on all rules and 4+.</p>
        <form className="vms-filters" onSubmit={(e) => { e.preventDefault(); x.run() }}>
          <Field label="Only components containing" hint="Blank = everything" style={{ flex: '1 1 240px' }}>
            <Input value={x.only} onChange={e => x.setOnly(e.target.value)} />
          </Field>
          <Button type="submit" Icon={Play} disabled={x.busy || x.running}>{x.running ? 'Running… (a few minutes)' : x.busy ? 'Starting…' : 'Run benchmark'}</Button>
        </form>
      </Card>

      {latest && (
        <>
          <h3 className="vms-h">Latest scorecard — worst first</h3>
          <ul className="vms-list" aria-label="Scorecard">
            {latest.components.map(c => {
              const gaps = gapsFor(c.component)
              const open = x.open === c.component
              return (
                <li key={c.component} className="vms-proj">
                  <button type="button" className="vms-proj-head" aria-expanded={gaps.length ? open : undefined} disabled={!gaps.length}
                    onClick={() => x.setOpen(open ? null : c.component)}>
                    {gaps.length > 0 ? <ChevronRight size={18} className="vms-chev" aria-hidden="true" /> : <span className="vms-chev-sp" />}
                    <span className="vms-grow vms-name">{c.component}</span>
                    <StatusChip tone={pctTone(c.pass_pct)}>{c.passed}/{c.total} ({c.pass_pct}%)</StatusChip>
                    <span className="vms-muted">judge {c.judge_avg != null ? `${c.judge_avg}/5` : '—'} · p50 {c.p50_secs != null ? `${c.p50_secs}s` : '—'}{gaps.length ? ` · ${gaps.length} gap${gaps.length === 1 ? '' : 's'}` : ''}</span>
                  </button>
                  {open && <div className="vms-detail"><Misses items={gaps} /></div>}
                </li>)
            })}
          </ul>
        </>)}

      <h3 className="vms-h">Runs</h3>
      <Card pad={0}>
        <DataTable caption="Benchmark runs" rows={x.data.runs} empty="No runs yet — press Run benchmark." columns={[
          { key: 'created_at', label: 'When', render: (r) => <span className="vms-muted">{when(r.created_at)}</span> },
          { key: 'skill', label: 'Scope', render: (r) => r.skill || 'everything' },
          { key: 'result', label: 'Result', render: (r) => r.status === 'running' ? <StatusChip tone="warn">Running</StatusChip>
            : r.status === 'failed' ? <span className="vms-danger">{r.error || 'failed'}</span>
              : `${r.passed}/${r.total} (${r.total ? Math.round(100 * r.passed / r.total) : 0}%)` },
          { key: 'secs', label: 'Time', align: 'right', render: (r) => (r.secs != null ? `${Math.round(r.secs)}s` : '—') },
          { key: 'judge_cost_usd', label: 'Judge cost', align: 'right', render: (r) => (r.judge_cost_usd != null ? `US$${r.judge_cost_usd}` : '—') },
        ]} stack={{ title: (r) => r.skill || 'everything', detail: (r) => when(r.created_at), trailing: (r) => (r.status === 'done' ? `${r.passed}/${r.total}` : r.status) }} />
      </Card>
    </>
  )
}

/* ── Conversations (daily check, owner advisor, retitle) ── */
const KIND_LABEL = {
  repeat: 'Same reply twice', leak: 'Raw output / false claim', cant_answer: "Couldn't answer",
  promise: 'Promised a follow-up', caveat: 'Checker flagged', pushback: 'Person said wrong',
}

export function ConversationsCheckV2({ onError }) {
  const x = useConversationCheck({ onError })
  const d = x.data
  return (
    <section className="vms" aria-label="Conversations">
      <Card className="vms-form">
        <h3 className="vms-h"><MessagesSquare size={16} aria-hidden="true" className="vms-ic" />Daily conversation check</h3>
        <p className="vms-muted">Every reply Vula sent that day, checked without a model. Emailed to the team each morning for yesterday.</p>
        <div className="vms-filters">
          <Field label="Day"><Input type="date" value={x.day} max={todaySA()} onChange={e => x.setDay(e.target.value)} /></Field>
          <Button variant="secondary" Icon={Mail} onClick={x.send} disabled={x.sending}>{x.sending ? 'Sending…' : 'Email yesterday now'}</Button>
          {d && <StatusChip tone={d.totals.flagged ? 'warn' : 'ok'}>{d.totals.flagged} of {d.totals.replies} replies need a look</StatusChip>}
        </div>
      </Card>
      {!d ? loading : !d.tenants.length ? <EmptyState Icon={MessagesSquare} title="No conversations">No conversations that day.</EmptyState> : (
        <ul className="vms-list" aria-label="Flagged replies per tenant">
          {d.tenants.map(t => (
            <li key={t.tenant_id}><Card className="vms-form">
              <div className="vms-row"><span className="vms-name vms-mono vms-grow">{t.tenant_id}</span>
                <StatusChip tone={t.flagged ? 'warn' : 'muted'}>{t.flagged}/{t.replies} flagged</StatusChip></div>
              {t.unfollowed_promises > 0 && <p className="vms-danger vms-body">{t.unfollowed_promises} follow-up promise{t.unfollowed_promises === 1 ? '' : 's'} with no escalation raised</p>}
              <div className="vms-row">
                {Object.entries(t.counts).filter(([, n]) => n).map(([k, n]) => {
                  const on = x.open === t.tenant_id + k
                  return <Button key={k} size="sm" variant={on ? 'primary' : 'secondary'} aria-pressed={on}
                    onClick={() => x.setOpen(on ? null : t.tenant_id + k)}>{KIND_LABEL[k] || k}: {n}</Button>
                })}
              </div>
              {Object.entries(t.examples).filter(([k]) => x.open === t.tenant_id + k).map(([k, ex]) => (
                <ul key={k} className="vms-list" aria-label={KIND_LABEL[k] || k}>
                  {ex.map((e, i) => (
                    <li key={i} className="vms-miss">
                      <span><b>Q:</b> {e.question || '—'}</span>
                      <span className="vms-body"><b>A:</b> {e.reply}</span>
                      {e.next && <span className="vms-danger"><b>Then:</b> {e.next}</span>}
                    </li>))}
                </ul>))}
            </Card></li>))}
        </ul>)}
      <AdvisorV2 onError={onError} />
      <RetitleV2 onError={onError} />
    </section>
  )
}

function AdvisorV2({ onError }) {
  const x = useAdvisorPreview({ onError })
  const tenants = useTenantOptions()
  return (
    <Card className="vms-form">
      <h3 className="vms-h"><Sparkles size={16} aria-hidden="true" className="vms-ic" />Weekly owner advisor — preview</h3>
      <p className="vms-muted">The Monday WhatsApp each owner gets: overdue invoices, spending jumps, low stock, unanswered questions, documents waiting and profile gaps — from real rows only. Sending is {x.res ? (x.res.enabled ? <b>on</b> : <b>off</b>) : 'off until OWNER_ADVISOR_ENABLED=true'}.</p>
      <div className="vms-filters">
        <TenantSelect value={x.tenant} tenants={tenants} onChange={x.setTenant} />
        <Button variant="secondary" disabled={!x.tenant} onClick={x.load}>Preview</Button>
      </div>
      {x.res && <pre className="vms-pre vms-quote">{x.res.text || 'Nothing to say this week — no message would be sent.'}</pre>}
    </Card>
  )
}

function RetitleV2({ onError }) {
  const x = useRetitle({ onError })
  const tenants = useTenantOptions()
  const st = x.state
  return (
    <Card className="vms-form">
      <h3 className="vms-h"><FileText size={16} aria-hidden="true" className="vms-ic" />Name documents from their content</h3>
      <p className="vms-muted">Reports, data sheets and brochures filed as “Report 20260827-1001.pdf” get a title from their own summary (e.g. “Mipolam Affinity – slip resistance test (R10)”). Money documents are never renamed. Preview first; nothing changes until you apply.</p>
      <div className="vms-filters">
        <TenantSelect value={x.tenant} tenants={tenants} onChange={(v) => { x.setTenant(v); x.setState(null) }} />
        <Button variant="secondary" disabled={!x.tenant || st?.status === 'running'} onClick={() => x.start(false)}>Preview</Button>
        <Button disabled={!st?.items?.length || st?.applied || st?.status === 'running'} onClick={() => x.start(true)}>Apply</Button>
      </div>
      {st?.status === 'running' && <p className="vms-muted" role="status">Working…</p>}
      {st?.status === 'failed' && <p className="vms-error" role="alert">{st.error}</p>}
      {st?.status === 'done' && (
        <>
          <p className="vms-body" role="status">{st.applied ? `Renamed ${st.written} document${st.written === 1 ? '' : 's'}.`
            : `${st.proposed} of ${st.generic} generic names can be improved (${st.skipped} skipped — no grounded title).`}</p>
          {(st.items || []).length > 0 && (
            <ul className="vms-renames" aria-label="Proposed names">
              {st.items.map(p => <li key={p.id}><span className="vms-muted">{p.old}</span><ArrowRight size={14} aria-hidden="true" className="vms-ic" /><b>{p.new}</b></li>)}
            </ul>)}
        </>)}
    </Card>
  )
}

/* ── Models ── */
export function ModelsV2({ onError }) {
  const x = useModelBakeoff({ onError })
  if (!x.info) return loading
  const toggle = (m) => x.setPicked(p => p.includes(m) ? p.filter(y => y !== m) : [...p, m])
  const pct = (r) => (r.total ? Math.round(100 * r.passed / r.total) : null)
  const reports = x.reports || []
  return (
    <section className="vms" aria-label="Models">
      <Card className="vms-form">
        <h3 className="vms-h"><FlaskConical size={16} aria-hidden="true" className="vms-ic" />Model bake-off</h3>
        <p className="vms-muted">
          Scores each model's first move on Vula's own admin, customer and email cases (tools stubbed — no tenant data, nothing sent).
          Today: cloud fallback <b>{x.info.current.model_worker_cloud}</b>, cheap <b>{x.info.current.model_worker_cheap}</b>
          {x.info.current.cloud_model_by_task ? <>, per-task map <code className="vms-code">{x.info.current.cloud_model_by_task}</code></> : ', no per-task map'}.
        </p>
        {!x.info.openrouter_configured && <p className="vms-error" role="alert">OPENROUTER_API_KEY isn't set on the server.</p>}
        <fieldset className="vms-fieldset">
          <legend className="vms-h">Models</legend>
          <div className="vms-mods">
            {x.info.candidates.map(m => (
              <label key={m} className="vms-mod"><input type="checkbox" checked={x.picked.includes(m)} onChange={() => toggle(m)} />{m.replace('openrouter/', '')}</label>))}
          </div>
        </fieldset>
        <form className="vms-filters" onSubmit={(e) => { e.preventDefault(); x.run() }}>
          <Field label="More models" hint="Comma-separated, openrouter/vendor/model" style={{ flex: '1 1 240px' }}>
            <Input value={x.custom} onChange={e => x.setCustom(e.target.value)} />
          </Field>
          <Field label="Skill">
            <Select value={x.skill} onChange={e => x.setSkill(e.target.value)}>
              <option value="">All skills</option>
              <option value="commerce_admin">Owner / staff admin</option>
              <option value="commerce_assistant">Customer assistant</option>
              <option value="email_admin">Email</option>
            </Select>
          </Field>
          <Button type="submit" Icon={Play} disabled={x.busy || x.running}>{x.running ? 'Running…' : x.busy ? 'Starting…' : 'Run'}</Button>
        </form>
      </Card>

      <h3 className="vms-h">Runs</h3>
      {!reports.length ? <EmptyState Icon={FlaskConical} title="No runs yet">Pick models above and press Run.</EmptyState> : (
        <ul className="vms-list" aria-label="Bake-off runs">
          {reports.map(r => {
            const open = x.open === r.id
            const misses = r.failures || []
            return (
              <li key={r.id} className="vms-proj">
                <button type="button" className="vms-proj-head" aria-expanded={misses.length ? open : undefined} disabled={!misses.length}
                  onClick={() => x.setOpen(open ? null : r.id)}>
                  {misses.length > 0 ? <ChevronRight size={18} className="vms-chev" aria-hidden="true" /> : <span className="vms-chev-sp" />}
                  <span className="vms-grow"><span className="vms-name">{(r.model || '').replace('openrouter/', '')}</span>
                    <span className="vms-muted vms-block">{r.skill || 'all skills'} · {when(r.created_at)}</span></span>
                  {r.status === 'running' ? <StatusChip tone="warn">Running</StatusChip>
                    : r.status === 'failed' ? <StatusChip tone="danger">Failed</StatusChip>
                      : <StatusChip tone={pctTone(pct(r))}>{r.passed}/{r.total} ({pct(r)}%)</StatusChip>}
                  <span className="vms-muted">p50 {r.p50_secs ?? '—'}s · p95 {r.p95_secs ?? '—'}s · {r.cost_per_100_usd != null ? `US$${r.cost_per_100_usd}/100 turns` : '—'} · {r.errors ?? '—'} errors</span>
                </button>
                {r.status === 'failed' && r.error && <p className="vms-error vms-pad">{r.error}</p>}
                {open && (
                  <ul className="vms-detail vms-plain" aria-label={`Misses for ${r.model}`}>
                    {misses.map((f, i) => <li key={i}>“{f.prompt}” — expected <b>{f.expect || 'no tool'}</b>, got <b>{f.error ? `error: ${f.error}` : (f.got || 'no tool')}</b></li>)}
                  </ul>)}
              </li>)
          })}
        </ul>)}

      <Card className="vms-form">
        <div className="vms-head">
          <h3 className="vms-h vms-title-sm"><ThumbsDown size={16} aria-hidden="true" className="vms-ic" />Test cases from thumbs-down feedback</h3>
          {(x.fbCases || []).length > 0 && <Button size="sm" variant="secondary" Icon={ClipboardCopy} onClick={() => x.copy(x.fbCases.map(c => c.yaml).join('\n'), 'all')}>{x.copied === 'all' ? 'Copied' : 'Copy all'}</Button>}
        </div>
        <p className="vms-muted">Replies people rated thumbs-down in the Inbox, as routing cases for <code className="vms-code">evals/cases/routing.yaml</code>. Phones and emails are masked. <b>expect</b> is where the question routes today — change it to the right skill before adding it, so the mistake can't come back.</p>
        {x.fbCases === null ? <Skeleton lines={2} /> : !x.fbCases.length ? <p className="vms-muted">No thumbs-down ratings yet.</p> : (
          <ul className="vms-list" aria-label="Feedback cases">
            {x.fbCases.map(c => (
              <li key={c.id} className="vms-row vms-item">
                <span className="vms-grow">“{c.question}” <span className="vms-muted">· {c.tenant_id} · routes to <b>{c.routes_to}</b></span>
                  {c.correction && <span className="vms-muted vms-block">Should have said: {c.correction}</span>}</span>
                <Button size="sm" variant="secondary" Icon={ClipboardCopy} onClick={() => x.copy(c.yaml, c.id)} aria-label={`Copy the YAML for “${c.question}”`}>{x.copied === c.id ? 'Copied' : 'Copy YAML'}</Button>
              </li>))}
          </ul>)}
      </Card>
    </section>
  )
}

