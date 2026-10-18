/**
 * useInbox.js — the Inbox's data and actions (2026-10-18, UX redesign Phase F). Shared by the
 * current Inbox and the redesigned one so the two can't drift: same endpoints, same polling
 * (list 15s, thread 8s), same writes. Moved out of VulaInbox.jsx unchanged.
 */
import { useCallback, useEffect, useState } from 'react'
import { promptDialog } from '../ui/index.jsx'
import { VULA_API } from '../../lib/authFetch'

/** "now", "5m", "3h", "2d" (agoSuffix adds " ago" and says "just now"). */
export function since(ts, agoSuffix = false) {
  if (!ts) return ''
  const mins = Math.floor((Date.now() - new Date(ts)) / 60000)
  if (mins < 1) return agoSuffix ? 'just now' : 'now'
  const v = mins < 60 ? `${mins}m` : mins < 1440 ? `${Math.floor(mins / 60)}h` : `${Math.floor(mins / 1440)}d`
  return agoSuffix ? `${v} ago` : v
}

/** The conversation list and the open escalations, refreshed every 15s. */
export function useInboxList(tenantId) {
  const [convos, setConvos] = useState([])
  const [escalations, setEscalations] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations`)
      const d = await r.json()
      setConvos(d.conversations || [])
    } catch {}
    setLoading(false)
  }, [tenantId])

  const loadEscalations = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations`)
      const d = await r.json()
      setEscalations(d.escalations || [])
    } catch {}
  }, [tenantId])

  useEffect(() => {
    load()
    loadEscalations()
    const t = setInterval(load, 15000)   // refresh list every 15s
    const te = setInterval(loadEscalations, 15000)
    return () => { clearInterval(t); clearInterval(te) }
  }, [load, loadEscalations])

  return { convos, escalations, loading, load, loadEscalations }
}

/** Answer or dismiss one escalation. */
export function useEscalation(tenantId, esc, onDone) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  async function send() {
    const text = answer.trim()
    if (!text || busy) return
    setBusy(true)
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations/${esc.id}/answer`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: text }),
      })
      onDone()
    } catch {}
    setBusy(false)
  }

  async function dismiss() {
    if (busy) return
    setBusy(true)
    try {
      await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations/${esc.id}/dismiss`, { method: 'POST' })
      onDone()
    } catch {}
    setBusy(false)
  }

  return { answer, setAnswer, busy, send, dismiss }
}

/** One open conversation: messages (every 8s), canned replies, take over / hand back, reply,
 *  assignment/tags/note, and 👍/👎 on an AI reply. */
export function useThread(tenantId, sessionId) {
  const [data, setData] = useState(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [canned, setCanned] = useState([])
  const [rated, setRated] = useState({})   // message id -> 'up' | 'down' (this visit)

  const msgs = data?.messages || []
  const paused = data?.paused

  // 👍/👎 on an AI reply. A 👎 can carry "what it should have said", which is also taught to
  // the assistant for next time (backend: /admin/conversations/{id}/feedback, migration 176).
  const rate = async (m, i, rating) => {
    if (!m.id) return
    let correction = null
    if (rating === 'down') {
      correction = await promptDialog('What should the assistant have said? (optional — it will learn this)') || null
    }
    const question = [...msgs.slice(0, i)].reverse().find(x => x.role === 'user')?.content || null
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/feedback`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: m.id, rating, question, answer: m.content, correction }),
      })
      if (r.ok) setRated(prev => ({ ...prev, [m.id]: rating }))
    } catch { /* rating is best-effort; the thread still works */ }
  }

  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/canned-replies`)
      .then(r => r.json()).then(d => setCanned(d.canned_replies || [])).catch(() => {})
  }, [tenantId])

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/messages`)
      setData(await r.json())
    } catch {}
  }, [tenantId, sessionId])

  useEffect(() => {
    load()
    const t = setInterval(load, 8000)    // refresh thread every 8s
    return () => clearInterval(t)
  }, [load])

  async function setHandoff(paused) {
    setBusy(true)
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/handoff`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused }),
    })
    await load()
    setBusy(false)
  }

  async function sendReply(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setBusy(true); setInput('')
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/reply`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    })
    await load()
    setBusy(false)
  }

  async function saveMeta(patch) {
    await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/meta`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
    }).catch(() => {})
    load()
  }

  return { data, msgs, paused, input, setInput, busy, canned, rated, rate, setHandoff, sendReply, saveMeta }
}
