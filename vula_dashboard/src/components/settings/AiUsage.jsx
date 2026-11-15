/**
 * AiUsage.jsx — Settings › AI usage (14 Nov 2026). This month's cloud AI allowance (Starter R100,
 * Growth R300, Business R750 — vula/ai_allowance.py), what's used, any top-ups left, and what
 * pauses when it runs out: only Ask anything and research, never customer replies. An owner or a
 * manager can request a top-up (R100, R250 or R500); Vula invoices it and it's added once paid.
 */
import { useCallback, useEffect, useState } from 'react'
import { Button, Card, ErrorCard, Skeleton, StatusChip, confirmDialog, toast } from '../ui/index.jsx'
import { VULA_API } from '../../lib/authFetch'
import './settings.css'

export const rand = (c) => {
  const v = Math.round(c || 0)
  const whole = Math.floor(Math.abs(v) / 100).toLocaleString('en-ZA').replace(/,/g, ' ')
  const part = Math.abs(v) % 100
  return `${v < 0 ? '-' : ''}R${whole}${part ? `.${String(part).padStart(2, '0')}` : ''}`
}

export function useAiUsage(tenantId) {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  const reload = useCallback(() => {
    setFailed(false)
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/ai/usage`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error('failed'))))
      .then(setData).catch(() => setFailed(true))
  }, [tenantId])
  useEffect(() => { if (tenantId) reload() }, [tenantId, reload])
  async function topUp(cents) {
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/ai/topup`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ amount_cents: cents }),
    }).catch(() => null)
    const d = r ? await r.json().catch(() => ({})) : {}
    if (!r || !r.ok) { toast(typeof d.detail === 'string' ? d.detail : "That request didn't go through — try again.", 'danger'); return }
    toast(d.message || 'Top-up requested.')
    reload()
  }
  return { data, failed, reload, topUp }
}

export default function AiUsage({ tenantId }) {
  const x = useAiUsage(tenantId)
  if (x.failed) return <ErrorCard what="your AI usage" onRetry={x.reload} />
  if (!x.data) return <Card><Skeleton lines={4} /></Card>
  const d = x.data
  const budget = (d.allowance || 0) + (d.topups_left || 0) + Math.max(0, (d.used || 0) - (d.allowance || 0))
  const pct = Math.min(100, d.pct ?? 0)
  const tone = d.paused ? 'danger' : pct >= 80 ? 'warn' : 'ok'
  const peak = Math.max(1, ...(d.days || []).map(v => v.cents))
  const ask = async (cents) => {
    if (await confirmDialog(`Request a ${rand(cents)} top-up? We'll send you an invoice; it's added as soon as it's paid.`,
      { confirmLabel: 'Request top-up' })) x.topUp(cents)
  }
  return (
    <>
      <Card>
        <div className="vst-card">
          <div className="vai-head">
            <p className="vai-big"><strong>{rand(d.used)}</strong> <span className="vai-muted">of {rand(budget)} used this month</span></p>
            <StatusChip tone={tone}>{d.paused ? 'Used up — extras paused' : pct >= 80 ? `${pct}% used` : 'Plenty left'}</StatusChip>
          </div>
          <div className="vai-bar" role="meter" aria-label="AI allowance used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
            aria-valuetext={`${rand(d.used)} of ${rand(budget)}`}>
            <span className={`vai-fill vai-fill--${tone}`} style={{ width: `${pct}%` }} />
          </div>
          <dl className="vai-facts">
            <div><dt>Included in your plan</dt><dd>{rand(d.allowance)} a month</dd></div>
            <div><dt>Top-ups left</dt><dd>{rand(d.topups_left)}</dd></div>
            <div><dt>Left this month</dt><dd>{rand(d.remaining)}</dd></div>
          </dl>
          <p className="vst-body">
            This is cloud AI only — Vula's own AI is free and doesn't count. When it's used up, only <em>Ask anything</em> and
            research switch to Vula's own AI until next month or a top-up. Replies to your customers, reading your documents and
            the business assistant never stop.
          </p>
          {(d.days || []).length > 0 && (
            <div className="vai-days" role="img" aria-label={`Use by day this month: ${d.days.map(v => `${v.day.slice(8)} ${rand(v.cents)}`).join(', ')}`}>
              {d.days.map(v => <span key={v.day} className="vai-day" style={{ height: `${Math.max(4, Math.round(40 * v.cents / peak))}px` }} />)}
            </div>)}
        </div>
      </Card>
      {d.can_top_up && (
        <Card>
          <div className="vst-card">
            <h3 className="vai-h3">Add a top-up</h3>
            <p className="vst-body">A top-up lasts until it's used, across months. We invoice you; it's added once paid.</p>
            <div className="vai-packs" role="group" aria-label="Top-up packs">
              {(d.packs || []).map(c => (
                <Button key={c} variant="secondary" onClick={() => ask(c)}>{rand(c)}</Button>))}
            </div>
            {(d.requested || []).length > 0 && (
              <ul className="vai-req" aria-label="Top-ups waiting for payment">
                {d.requested.map(r => (
                  <li key={r.id}><StatusChip tone="warn">Waiting for payment</StatusChip> {rand(r.amount)} · requested {String(r.requested_at || '').slice(0, 10)}</li>))}
              </ul>)}
          </div>
        </Card>)}
    </>
  )
}
