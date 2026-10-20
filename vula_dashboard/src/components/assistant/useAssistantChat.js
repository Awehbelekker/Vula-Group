/**
 * useAssistantChat.js — "Chat to Vula", shared by VulaAssistant (current look) and assistant/AssistantV2
 * (the redesign, 2026-10-20). Moved verbatim: the same commerce_admin agent the owner uses on
 * WhatsApp, one session per mount, with the starter suggestions.
 */
import { useState, useRef, useEffect } from 'react'
import { VULA_API } from '../../lib/authFetch'

export const SUGGESTIONS = [
  "What were today's sales?",
  "Which orders need dispatching?",
  "What's low on stock?",
  "What invoices are outstanding?",
]

export function useAssistantChat(tenantId) {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: "Hi! I'm your Vula assistant. Ask me about sales, orders, stock, invoices or expenses — or tell me to update something." },
  ])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const sessionId = useRef(`${tenantId}-${Math.random().toString(36).slice(2, 9)}`)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, sending])

  async function send(text) {
    const msg = (text ?? input).trim()
    if (!msg || sending) return
    setInput('')
    setMessages(m => [...m, { role: 'user', text: msg }])
    setSending(true)
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/assistant`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, session_id: sessionId.current }),
      })
      const d = await r.json()
      setMessages(m => [...m, { role: 'assistant', text: d.answer || "Sorry, I couldn't process that." }])
    } catch {
      setMessages(m => [...m, { role: 'assistant', text: 'Connection error — please try again.' }])
    } finally {
      setSending(false)
    }
  }

  return { messages, input, setInput, sending, endRef, send }
}
