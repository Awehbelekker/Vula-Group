/**
 * useXeroMove.js — Settings › Move from Xero (5 Nov 2026, step 1: connect and preview). GET
 * /admin/xero, POST /admin/xero/connect (sign in at Xero in a window), POST /admin/xero/preview
 * (read Xero at a cut-over date, in the background), GET /admin/xero/runs/{id} (polled).
 * Nothing here writes to the books (vula/api/xero.py).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { VULA_API } from '../../../lib/authFetch'
import { confirmDialog, toast } from '../../ui/index.jsx'

function lastMonthEnd() {
  const d = new Date()
  const end = new Date(d.getFullYear(), d.getMonth(), 0)
  return `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, '0')}-${String(end.getDate()).padStart(2, '0')}`
}

export function useXeroMove(tenantId) {
  const base = `${VULA_API}/v1/commerce/${tenantId}/admin/xero`
  const [info, setInfo] = useState(null)
  const [failed, setFailed] = useState(false)
  const [cutover, setCutover] = useState(lastMonthEnd())
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const poll = useRef(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(base)
      if (!r.ok) throw new Error()
      const d = await r.json()
      setInfo(d); setFailed(false)
      const last = (d.runs || [])[0]
      if (last && !run) openRun(last.id)
    } catch { setFailed(true) }
  }, [base])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (tenantId) load() }, [tenantId, load])
  useEffect(() => {
    const onMsg = e => { if (e.data === 'xero-connected') load() }
    window.addEventListener('message', onMsg)
    return () => { window.removeEventListener('message', onMsg); if (poll.current) clearTimeout(poll.current) }
  }, [load])

  async function openRun(id) {
    try {
      const r = await fetch(`${base}/runs/${id}`)
      if (!r.ok) return
      const d = await r.json()
      setRun(d.run)
      if (['reading', 'importing'].includes(d.run?.status)) poll.current = setTimeout(() => openRun(id), 4000)
    } catch { /* keep the last view */ }
  }

  async function connect() {
    setError(null)
    // open the window inside the tap (a window opened after an await is blocked on phones)
    const popup = window.open('', 'xero-oauth', 'width=620,height=760')
    try {
      const r = await fetch(`${base}/connect`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const d = await r.json().catch(() => ({}))
      if (!r.ok || !d.url) throw new Error(d.detail || d.error || 'Xero can’t be connected just now.')
      if (popup && !popup.closed) popup.location.href = d.url
      else window.location.href = d.url
    } catch (err) {
      if (popup && !popup.closed) popup.close()
      setError(err.message)
    }
  }

  async function disconnect() {
    if (!(await confirmDialog('Disconnect Xero? Vula stops reading it; nothing already in Vula changes.'))) return
    const r = await fetch(`${base}/disconnect`, { method: 'POST' })
    if (r.ok) { toast('Xero disconnected'); load() }
  }

  async function preview(e) {
    e?.preventDefault()
    setBusy(true); setError(null)
    try {
      const r = await fetch(`${base}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cutover }) })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'That couldn’t be started')
      setRun(d.run)
      poll.current = setTimeout(() => openRun(d.run.id), 3000)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function bringIn() {
    if (!run) return
    const ok = await confirmDialog(
      `Bring everything in from Xero at ${run.cutover_date}? Vula adds the opening balances, every invoice and bill, ` +
      'your customers and suppliers, checks it all against Xero to the cent, then disconnects from Xero. ' +
      'It can be undone until a payment is recorded on a moved invoice.')
    if (!ok) return
    setBusy(true); setError(null)
    try {
      const r = await fetch(`${base}/runs/${run.id}/commit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'That couldn’t be started')
      setRun({ ...run, ...d.run, status: 'importing' })
      poll.current = setTimeout(() => openRun(run.id), 3000)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function undo() {
    if (!run) return
    if (!(await confirmDialog('Undo this move? Its invoices and bills are cancelled (kept on record) and the opening balances reversed.'))) return
    const r = await fetch(`${base}/runs/${run.id}/undo`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) { setError(d.detail || 'It couldn’t be undone'); return }
    toast('Move undone'); openRun(run.id)
  }

  return { info, failed, cutover, setCutover, run, busy, error, connect, disconnect, preview, bringIn, undo, reload: load }
}
