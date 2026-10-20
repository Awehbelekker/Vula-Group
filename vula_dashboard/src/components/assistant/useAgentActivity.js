/**
 * useAgentActivity.js — Assistant › Agent activity, shared by VulaAgentActivity (current look) and
 * assistant/AgentActivityV2 (the redesign, 2026-10-20). Moved verbatim: what the AI did (tools it
 * called, local vs cloud), how Vula sounds (persona, learned from the owner's own replies) and
 * teaching it a correct answer.
 */
import { useState, useEffect, useCallback } from "react";
import { VULA_API } from "../../lib/authFetch"

export function useAgentActivity(tenantId) {
  const [events, setEvents] = useState(null);
  const [q, setQ] = useState("");
  const [a, setA] = useState("");
  const [msg, setMsg] = useState("");

  const [persona, setPersona] = useState("");
  const [suggested, setSuggested] = useState("");
  const [voiceMsg, setVoiceMsg] = useState("");
  const [analyzing, setAnalyzing] = useState(false);

  const load = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/agent-activity?limit=80`)
      .then(r => r.json()).catch(() => ({ events: [] }));
    setEvents(d.events || []);
  }, [tenantId]);
  useEffect(() => { load(); }, [load]);

  const loadPersona = useCallback(async () => {
    const d = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/persona`)
      .then(r => r.json()).catch(() => ({}));
    setPersona(d.persona_prompt || "");
    setSuggested(d.persona_prompt_suggested || "");
  }, [tenantId]);
  useEffect(() => { loadPersona(); }, [loadPersona]);

  const savePersona = async (text) => {
    setVoiceMsg("Saving…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/persona`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_prompt: text }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) setVoiceMsg(r.error);
    else { setPersona(text); setSuggested(""); setVoiceMsg("Saved ✓"); }
    setTimeout(() => setVoiceMsg(""), 4000);
  };

  const analyzeVoice = async () => {
    setAnalyzing(true);
    setVoiceMsg("Reading your recent replies…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/persona/analyze`, { method: "POST" })
      .then(r => r.json()).catch(() => ({ error: "network" }));
    setAnalyzing(false);
    if (r.error) setVoiceMsg(r.error);
    else { setSuggested(r.suggested); setVoiceMsg(`Learned from ${r.sample_count} of your replies ✓`); }
  };

  const teach = async () => {
    if (!q.trim() || !a.trim()) { setMsg("Fill in both the question and the correct answer."); return; }
    setMsg("Teaching…");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/agent-teach`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, answer: a }),
    }).then(r => r.json()).catch(() => ({ error: "network" }));
    if (r.error) setMsg(r.error);
    else { setMsg("Learned ✓ — your AI assistant will use this from now on."); setQ(""); setA(""); }
    setTimeout(() => setMsg(""), 4000);
  };

  return { events, q, setQ, a, setA, msg, persona, setPersona, suggested, voiceMsg, analyzing, load, savePersona, analyzeVoice, teach }
}
