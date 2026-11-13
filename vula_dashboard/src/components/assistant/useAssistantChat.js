/**
 * useAssistantChat.js — "Chat to Vula", shared by VulaAssistant (current look) and assistant/AssistantV2
 * (the redesign, 2026-10-20). Moved verbatim: the same commerce_admin agent the owner uses on
 * WhatsApp, with the starter suggestions.
 *
 * The thread belongs to the signed-in person: the server keys it on who they are, and the earlier
 * messages are loaded when the chat opens. (It used to start a new, anonymous session on every
 * page load, so the chat forgot everything on refresh.) session_id is only a fallback for a
 * caller the server can't identify.
 */
import { useState, useRef, useEffect } from 'react'
import { VULA_API } from '../../lib/authFetch'

export const SUGGESTIONS = [
  "What were today's sales?",
  "Which orders need dispatching?",
  "What's low on stock?",
  "What invoices are outstanding?",
]

const MODE_KEY = 'vula.assistant.mode'
const readMode = () => { try { return localStorage.getItem(MODE_KEY) === 'open' ? 'open' : 'business' } catch { return 'business' } }

export function useAssistantChat(tenantId) {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: "Hi! I'm your Vula assistant. Ask me about sales, orders, stock, invoices or expenses — or tell me to update something." },
  ])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  // "Ask anything" (13 Nov 2026): a general question on Vula's own AI, when the business has it on.
  const [openAvailable, setOpenAvailable] = useState(false)
  // Files attached to the next message (13 Nov 2026): read by Vula, not filed, kept 24 h.
  const [files, setFiles] = useState([])
  const [fileError, setFileError] = useState('')
  const [mode, setModeState] = useState(readMode)
  const setMode = (m) => { setModeState(m); try { localStorage.setItem(MODE_KEY, m) } catch { /* private window */ } }
  const sessionId = useRef(`${tenantId}-${Math.random().toString(36).slice(2, 9)}`)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, sending])

  useEffect(() => {
    if (!tenantId) return
    let cancelled = false
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant/history`)
      .then(r => (r.ok ? r.json() : { messages: [] }))
      .then(d => {
        if (!cancelled) setOpenAvailable(!!d.open_chat)
        const earlier = (d.messages || []).filter(m => m.content)
          .map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', text: m.content,
            id: m.role === 'user' ? null : m.id, sessionId: d.session_id }))
        // Only fill an untouched chat: never overwrite something typed while this loaded.
        if (!cancelled && earlier.length) setMessages(m => (m.length === 1 ? [...m, ...earlier] : m))
      })
      .catch(() => { /* the chat still works without its history */ })
    return () => { cancelled = true }
  }, [tenantId])

  async function attach(list) {
    const picked = Array.from(list || []).slice(0, Math.max(0, 3 - files.length))
    if (!picked.length) return
    setFileError('')
    const temp = picked.map((f, i) => ({ tempId: `t${Date.now()}${i}`, name: f.name, reading: true }))
    setFiles(fs => [...fs, ...temp])
    const body = new FormData()
    picked.forEach(f => body.append('files', f))
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant/attach`, { method: 'POST', body })
      const d = await r.json()
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : "Couldn't attach that.")
      setFiles(fs => [...fs.filter(f => !temp.some(t => t.tempId === f.tempId)), ...(d.attachments || [])])
      if (d.problems?.length) setFileError(d.problems.join(' '))
    } catch (e) {
      setFiles(fs => fs.filter(f => !temp.some(t => t.tempId === f.tempId)))
      setFileError(e.message || "Couldn't attach that.")
    }
  }
  const removeFile = (id) => setFiles(fs => fs.filter(f => f.id !== id))

  async function keepFile(id) {
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant/attach/${id}/file`, { method: 'POST' })
      if (!r.ok) throw new Error('not kept')
      setFiles(fs => fs.map(f => (f.id === id ? { ...f, filed: true } : f)))
    } catch { setFileError("Couldn't add that to your documents.") }
  }

  async function send(text) {
    const ready = files.filter(f => f.id)
    const msg = ((text ?? input).trim()) || (ready.length ? 'What does this file say? Give me the main points.' : '')
    if (!msg || sending || files.some(f => f.reading)) return
    setInput('')
    setFiles([])
    setMessages(m => [...m, { role: 'user', text: msg, files: ready.map(f => f.name) }])
    setSending(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, session_id: sessionId.current, mode: openAvailable ? mode : 'business',
          ...(ready.length ? { attachments: ready.map(f => f.id) } : {}) }),
      })
      const d = await r.json()
      const text = d.answer || (typeof d.detail === 'string' ? d.detail : "Sorry, I couldn't process that.")
      setMessages(m => [...m, { role: 'assistant', text, id: d.message_id || null, sessionId: d.session_id || null,
        answeredBy: d.answered_by || null, confirm: d.confirm_request || null }])
    } catch {
      setMessages(m => [...m, { role: 'assistant', text: 'Connection error — please try again.' }])
    } finally {
      setSending(false)
    }
  }

  // 👍/👎 on a reply — the same record a rating in the Inbox makes. Only the rating is sent: this
  // is the person's own thread, so what was asked and answered is not copied anywhere.
  async function rate(message, rating) {
    if (!message.id || !message.sessionId) return
    const before = message.rating
    setMessages(m => m.map(x => (x.id === message.id ? { ...x, rating, rateError: false } : x)))
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/conversations/${message.sessionId}/feedback`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: message.id, rating }),
      })
      if (!r.ok) throw new Error('not saved')
    } catch {
      setMessages(m => m.map(x => (x.id === message.id ? { ...x, rating: before, rateError: true } : x)))
    }
  }

  // Confirm or cancel an action Vula previewed (a stock change, a refund, a broadcast…). Runs the
  // exact action that was shown, once, for this person only.
  async function decide(message, action) {
    const cid = message.confirm?.id
    if (!cid) return
    const same = (x) => x.confirm?.id === cid
    setMessages(m => m.map(x => (same(x) ? { ...x, confirm: { ...x.confirm, busy: true } } : x)))
    let text
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant/confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: message.confirm.id, action }),
      })
      const d = await r.json()
      text = d.answer || (typeof d.detail === 'string' ? d.detail : 'Something went wrong — please try again.')
    } catch { text = 'Connection error — please try again.' }
    setMessages(m => [...m.map(x => (same(x) ? { ...x, confirm: null } : x)), { role: 'assistant', text }])
  }

  return { messages, input, setInput, sending, endRef, send, rate, decide, mode, setMode, openAvailable,
    files, fileError, attach, removeFile, keepFile }
}
