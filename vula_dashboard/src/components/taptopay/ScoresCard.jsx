/**
 * ScoresCard.jsx — Money › Tap to Pay › Team board (24 Nov 2026): each person's sales, tables
 * answered and points for the period. Backend: vula_mind/vula/tap/scoreboard.py. Every figure is
 * the server's. Tips show only when the server sends them (to an owner or a manager).
 */
import { useCallback, useEffect, useState } from 'react'
import { Trophy } from 'lucide-react'
import { api } from '../../lib/api'
import { formatMoney } from '../../lib/money'
import { Card, Skeleton, StatusChip } from '../ui/index.jsx'

const money = (c) => formatMoney(c, { cents: 'always' })
const wait = (s) => (s == null ? '–' : s < 60 ? `${s}s` : `${Math.round(s / 60)} min`)
const PERIODS = [{ id: 'today', label: 'Today' }, { id: 'week', label: '7 days' }, { id: 'month', label: '30 days' }]

export default function ScoresCard({ tenantId }) {
  const [period, setPeriod] = useState('week')
  const [d, setD] = useState(null)
  const [error, setError] = useState('')
  const load = useCallback(() => {
    setError('')
    api(`/v1/tap/${tenantId}/scores?period=${period}`).then(setD).catch(e => setError(e.message))
  }, [tenantId, period])
  useEffect(() => { load() }, [load])
  const tips = d?.board?.some(r => r.tips_cents != null)
  return (
    <Card className="vtp-card" role="group" aria-labelledby="vtp-scores">
      <div className="vtp-row">
        <h2 className="vtp-h vtp-grow" id="vtp-scores"><Trophy size={18} aria-hidden="true" />Team board</h2>
        <div role="group" aria-label="Period" className="vtp-row">
          {PERIODS.map(p => <button key={p.id} type="button" className="vtp-pill" aria-pressed={period === p.id} onClick={() => setPeriod(p.id)}>{p.label}</button>)}
        </div>
      </div>
      {error ? <p className="vtp-error" role="alert">{error}</p> : !d ? <Skeleton lines={3} />
        : d.board.length === 0 ? <p className="vtp-muted">Nobody has a paid bill or an answered table in this period yet.</p> : (
          <div className="vtp-scroll">
            <table className="vtp-table">
              <thead><tr><th>#</th><th>Name</th><th className="vtp-num">Points</th><th className="vtp-num">Sales</th>{tips && <th className="vtp-num">Tips</th>}<th className="vtp-num">Bills</th><th className="vtp-num">Tables answered</th><th className="vtp-num">Wait</th></tr></thead>
              <tbody>
                {d.board.map(r => (
                  <tr key={r.member_id}>
                    <td>{r.rank}</td>
                    <td><strong>{r.name}</strong>{r.badges.map(b => <StatusChip key={b} tone="ok" style={{ marginLeft: 6 }}>{b}</StatusChip>)}</td>
                    <td className="vtp-num"><strong>{r.points}</strong></td>
                    <td className="vtp-num vula-money">{money(r.sales_cents)}</td>
                    {tips && <td className="vtp-num vula-money">{money(r.tips_cents)}</td>}
                    <td className="vtp-num">{r.bills}</td><td className="vtp-num">{r.calls}</td><td className="vtp-num">{wait(r.avg_wait_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {d && <p className="vtp-muted">Points: {d.points_rule} Your team sees this board in the staff app without tips; each person sees only their own.</p>}
    </Card>
  )
}
