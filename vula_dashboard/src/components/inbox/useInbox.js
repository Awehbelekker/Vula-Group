/**
 * useInbox.js — the Inbox's data and actions (2026-10-18, UX redesign Phase F). Shared by the
 * current Inbox and the redesigned one so the two can't drift: same endpoints, same polling
 * (list 15s, thread 8s), same writes. Moved out of VulaInbox.jsx unchanged.
 */
import { useCallback, useEffect, useState } from 'react'
import { promptDialog, toast } from '../ui/index.jsx'
import { VULA_API } from '../../lib/authFetch'
import { api, tryApi } from '../../lib/api'

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
  const [mineOnly, setMineOnly] = useState(false)   // the server sent only chats assigned to this login
  const [escalations, setEscalations] = useState([])
  const [loading, setLoading] = useState(true)
  // 23 Nov 2026 audit: a failed load showed "No chats yet". Now it says it couldn't load; a later
  // refresh that works clears it. What was already loaded stays on screen.
  const [error, setError] = useState(null)
  const [escError, setEscError] = useState(null)

  const load = useCallback(async () => {
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/conversations`)
    if (res.ok) {
      setConvos(res.data.conversations || [])
      setMineOnly(!!res.data.mine_only)
      setError(null)
    } else {
      setError(res.error)
    }
    setLoading(false)
  }, [tenantId])

  const loadEscalations = useCallback(async () => {
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/escalations`)
    if (res.ok) { setEscalations(res.data.escalations || []); setEscError(null) }
    else setEscError(res.error)
  }, [tenantId])

  useEffect(() => {
    load()
    loadEscalations()
    const t = setInterval(load, 15000)   // refresh list every 15s
    const te = setInterval(loadEscalations, 15000)
    return () => { clearInterval(t); clearInterval(te) }
  }, [load, loadEscalations])

  return { convos, escalations, loading, load, loadEscalations, mineOnly, error, escError }
}

/** Answer or dismiss one escalation. */
export function useEscalation(tenantId, esc, onDone) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  async function send() {
    const text = answer.trim()
    if (!text || busy) return
    setBusy(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations/${esc.id}/answer`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: text }),
    }).catch(() => null)
    setBusy(false)
    if (!r || !r.ok) {
      const d = r ? await r.json().catch(() => ({})) : {}
      toast(d.detail || "That answer didn't send. It's still in the box — try again.", 'danger')
      return
    }
    onDone()
  }

  async function dismiss() {
    if (busy) return
    setBusy(true)
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/escalations/${esc.id}/dismiss`, { method: 'POST' }).catch(() => null)
    setBusy(false)
    if (!r || !r.ok) { toast("That didn't go through. Try again.", 'danger'); return }
    onDone()
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
    try {
      await api(`/v1/commerce/${tenantId}/admin/conversations/${sessionId}/handoff`, {
        method: 'POST', body: { paused },
      })
    } catch (e) {
      toast(paused ? `Couldn't take over the chat: ${e.message}` : `Couldn't hand the chat back to Vula: ${e.message}`, 'danger')
    }
    try { await load() } finally { setBusy(false) }
  }

  async function sendReply(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setBusy(true); setInput('')
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${sessionId}/reply`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    }).catch(() => null)
    if (!r || !r.ok) {
      // 23 Nov 2026 audit: the box was cleared and the reply lost when WhatsApp refused it
      const d = r ? await r.json().catch(() => ({})) : {}
      setInput(text)
      toast(d.detail || "That reply didn't go to the customer. It's still in the box — try again.", 'danger')
    }
    await load()
    setBusy(false)
  }

  async function saveMeta(patch) {
    const res = await tryApi(`/v1/commerce/${tenantId}/admin/conversations/${sessionId}/meta`, {
      method: 'POST', body: patch,
    })
    if (!res.ok) toast(`That change wasn't saved: ${res.error}`, 'danger')
    load()
    return res.ok
  }

  return { data, msgs, paused, input, setInput, busy, canned, rated, rate, setHandoff, sendReply, saveMeta }
}

/** The team (for "Assign to" and the person filter) and who is signed in. 12 Nov 2026. */
export function useInboxTeam(tenantId) {
  const [members, setMembers] = useState([])
  const [me, setMe] = useState(null)
  useEffect(() => {
    if (!tenantId) return
    fetch(`${VULA_API}/v1/team/${tenantId}`).then(r => (r.ok ? r.json() : {}))
      .then(d => setMembers((d.members || []).filter(m => m.active !== false))).catch(() => {})
    fetch(`${VULA_API}/v1/team/${tenantId}/me`).then(r => (r.ok ? r.json() : null)).then(setMe).catch(() => {})
  }, [tenantId])
  return { members, me }
}
