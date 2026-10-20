/**
 * ClientOnboardingV2.jsx — the redesigned Marketing › Client onboarding (2026-10-20, UX redesign
 * Phase F). Same calls as VulaClientOnboarding (marketing/useClientOnboarding.js): the opt-in
 * funnel for imported clients, a preview to the owner's own number, and sending the approved
 * intro template in small batches. The redesign asks before a batch goes to customers.
 */
import { Send, Smartphone, UserPlus } from 'lucide-react'
import { Button, Card, Field, Input, Skeleton, StatCard, confirmDialog } from '../ui/index.jsx'
import { useClientOnboarding } from './useClientOnboarding'
import './marketing.css'

const plain = (t) => String(t || '').replace(/^[^A-Za-z0-9]+\s*/, '')
const STATS = [['Total', 'total'], ['Not sent', 'invited'], ['Awaiting reply', 'awaiting_reply'], ['In progress', 'in_progress'],
  ['Opted in', 'opted_in'], ['Complete', 'complete'], ['Opted out', 'opted_out']]

export default function ClientOnboardingV2({ tenantId }) {
  const x = useClientOnboarding(tenantId)
  if (!x.stats) return <Card><Skeleton lines={3} /></Card>
  const n = Number(x.batch) || 0
  async function sendBatch() {
    if (!(await confirmDialog(`Send the WhatsApp introduction to the next ${n} client${n === 1 ? '' : 's'}? Only clients who reply opt in to future specials.`,
      { confirmLabel: `Send to ${n}` }))) return
    x.send({ batch_size: n }, 'Sending batch')
  }
  const warn = x.msg.startsWith('⚠️')
  return (
    <section className="vmk" aria-label="Client onboarding">
      <p className="vmk-sub">Introduce the WhatsApp line to your existing clients in small batches. They reply to opt in and share their delivery details; only opted-in clients get future specials.</p>
      <div className="vmk-stats">
        {STATS.map(([label, k]) => <StatCard key={k} variant="tile" label={label} value={x.stats[k] ?? 0} />)}
      </div>
      <Card className="vmk-card">
        <form className="vmk-grid" onSubmit={e => { e.preventDefault(); x.send({ test_phone: x.test }, 'Sending test') }}>
          <Field label="Preview to my number"><Input type="tel" value={x.test} onChange={e => x.setTest(e.target.value)} placeholder="27821234567" /></Field>
          <div className="vmk-end"><Button type="submit" variant="secondary" Icon={Smartphone} disabled={x.busy || !x.test}>Send test</Button></div>
        </form>
        <form className="vmk-grid" onSubmit={e => { e.preventDefault(); sendBatch() }}>
          <Field label="Batch size" hint="Start small (10–25) on day one so WhatsApp sees healthy engagement, then increase.">
            <Input type="number" min={1} max={200} value={x.batch} onChange={e => x.setBatch(e.target.value)} />
          </Field>
          <div className="vmk-end"><Button type="submit" Icon={x.stats.invited ? Send : UserPlus} disabled={x.busy || !x.stats.invited || !n}>Send next {x.batch}</Button></div>
        </form>
        {x.msg && <p className={warn ? 'vmk-error' : 'vmk-note'} role={warn ? 'alert' : 'status'}>{plain(x.msg)}</p>}
      </Card>
    </section>
  )
}
