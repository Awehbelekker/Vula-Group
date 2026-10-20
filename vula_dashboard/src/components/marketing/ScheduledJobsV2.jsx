/**
 * ScheduledJobsV2.jsx — the redesigned Marketing › Scheduling (2026-10-20, UX redesign Phase F).
 * Same calls as VulaScheduledJobs (marketing/useScheduledJobs.js): each automatic WhatsApp job's
 * on/off, its time (SAST) or interval, and which approved template it uses; saved per job.
 */
import { CheckCircle2, Clock } from 'lucide-react'
import { SA_TZ } from '../../lib/dates'
import { Button, Card, EmptyState, Field, Select, Skeleton, StatusChip } from '../ui/index.jsx'
import { DAYS, pad, useJobForm, useScheduledJobs } from './useScheduledJobs'
import './marketing.css'

const sast = (iso) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString('en-ZA', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: SA_TZ })
}

export default function ScheduledJobsV2({ tenantId }) {
  const x = useScheduledJobs(tenantId)
  return (
    <section className="vmk" aria-label="Scheduled messages">
      <p className="vmk-sub">When each automatic WhatsApp goes out, and which approved template it uses. Times are South African (SAST); changes take effect within about 5 minutes.</p>
      {x.msg && <p className="vmk-note" role="status">{x.msg}</p>}
      {x.busy && !x.jobs.length && <Card><Skeleton lines={4} /></Card>}
      {!x.busy && !x.jobs.length && <EmptyState Icon={Clock} title="No scheduled messages" />}
      <ul className="vmk-list">
        {x.jobs.map(j => <li key={j.job_type}><Job job={j} templates={x.templates} onSave={(patch) => x.save(j.job_type, patch)} /></li>)}
      </ul>
      <p className="vmk-muted">"Default template" uses the standard one for this business. Only templates Meta has approved can be picked; create new ones in the Templates tab first.</p>
    </section>
  )
}

function Job({ job, templates, onSave }) {
  const f = useJobForm(job, onSave)
  const id = `vmk-job-${job.job_type}`
  return (
    <Card className={`vmk-card${f.enabled ? '' : ' vmk-off'}`} role="group" aria-labelledby={id}>
      <label className="vmk-switch">
        <input type="checkbox" checked={f.enabled} onChange={e => f.setEnabled(e.target.checked)} />
        <span className="vmk-grow"><b id={id}>{job.label}</b><span className="vmk-muted">{job.description}</span></span>
        <StatusChip tone={f.enabled ? 'ok' : 'muted'}>{f.enabled ? 'On' : 'Off'}</StatusChip>
      </label>
      <form className="vmk-grid" onSubmit={e => { e.preventDefault(); f.submit() }}>
        {job.kind === 'weekly' && (
          <Field label="Day">
            <Select value={f.dow} onChange={e => f.setDow(e.target.value)}>{DAYS.map((d, i) => <option key={d} value={i}>{d}s</option>)}</Select>
          </Field>)}
        {f.timed && (
          <div className="vmk-time">
            <Field label="Hour"><Select value={f.hour} onChange={e => f.setHour(e.target.value)}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{pad(h)}</option>)}</Select></Field>
            <Field label="Minute"><Select value={f.minute} onChange={e => f.setMinute(e.target.value)}>
              {[0, 15, 30, 45].map(m => <option key={m} value={m}>{pad(m)}</option>)}</Select></Field>
          </div>)}
        {job.kind === 'interval' && (
          <Field label="Every">
            <Select value={f.interval} onChange={e => f.setIntervalMin(e.target.value)}>
              {[15, 30, 60, 120, 240].map(m => <option key={m} value={m}>{m} min</option>)}</Select>
          </Field>)}
        <Field label="Template">
          <Select className="vmk-mono" value={f.tpl} onChange={e => f.setTpl(e.target.value)}>
            <option value="">Default template</option>
            {templates.map(t => <option key={t} value={t}>{t}</option>)}
          </Select>
        </Field>
        <div className="vmk-end"><Button type="submit" Icon={CheckCircle2} disabled={f.saving} aria-label={`Save ${job.label}`}>{f.saving ? 'Saving…' : 'Save'}</Button></div>
      </form>
      {job.last_fired_at && <p className="vmk-muted">Last sent {sast(job.last_fired_at)}</p>}
    </Card>
  )
}
