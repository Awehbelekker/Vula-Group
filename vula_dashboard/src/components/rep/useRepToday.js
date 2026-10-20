/**
 * useRepToday.js — a sales rep's day (shared by VulaRepToday and RepTodayV2): today's appointments,
 * minutes on a meeting (typed or dictated) and adding an appointment. Moved verbatim out of
 * VulaRepToday.jsx; see that file's header for what saving minutes does.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

export const API = (t) => `${VULA_API}/v1/commerce/${t}/admin`;
export const todayLocal = () => new Date().toLocaleDateString("en-CA");        // YYYY-MM-DD

export function useDictation(onText) {
  const rec = useRef(null);
  const [on, setOn] = useState(false);
  const SR = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);
  const start = () => {
    if (!SR) return;
    const r = new SR();
    r.lang = "en-ZA"; r.continuous = true; r.interimResults = false;
    r.onresult = (e) => {
      const text = Array.from(e.results).slice(e.resultIndex).map((x) => x[0].transcript).join(" ");
      if (text.trim()) onText(text.trim());
    };
    r.onend = () => setOn(false);
    r.onerror = () => setOn(false);
    rec.current = r; r.start(); setOn(true);
  };
  const stop = () => { rec.current?.stop(); setOn(false); };
  return { supported: !!SR, on, start, stop };
}

export function useMinutesForm({ open, onClose, tenantId, repPhone, appointment, onSaved }) {
  const [notes, setNotes] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const dict = useDictation((t) => setNotes((n) => (n ? n + " " : "") + t));
  useEffect(() => { if (open) { setNotes(""); setContact(""); } }, [open]);
  const save = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${API(tenantId)}/meetings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes, rep_phone: repPhone, booking_id: appointment?.id, contact: contact || undefined }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(d.detail || "Couldn't save the minutes.", "danger"); return; }
      const n = (d.action_items || []).length;
      toast(`Minutes saved${n ? ` · ${n} action item${n === 1 ? "" : "s"} set as reminders` : ""}${d.pdf_sent ? " · PDF sent to your WhatsApp" : ""}.`);
      onSaved?.(); onClose();
    } finally { setBusy(false); }
  };
  return { notes, setNotes, contact, setContact, busy, dict, save };
}

export function useAppointmentForm({ open, onClose, tenantId, repPhone, onSaved }) {
  const blank = { customer_name: "", customer_phone: "", date: todayLocal(), time: "10:00", duration_min: 60, location: "", notes: "" };
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setF(blank); }, [open]);  // eslint-disable-line
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${API(tenantId)}/rep/appointments`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...f, start: `${f.date}T${f.time}`, rep_phone: repPhone }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(d.detail || "Couldn't book that.", "danger"); return; }
      toast("Appointment added."); onSaved?.(); onClose();
    } finally { setBusy(false); }
  };
  return { f, set, busy, save };
}

export function useRepToday(tenantId, repPhone) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [minutesFor, setMinutesFor] = useState(undefined);   // undefined = closed, null = unscheduled
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    if (!tenantId) return;
    setFailed(false);
    try {
      const q = repPhone ? `?rep_phone=${encodeURIComponent(repPhone)}` : "";
      const r = await fetch(`${API(tenantId)}/rep/today${q}`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
    } catch { setFailed(true); }
  }, [tenantId, repPhone]);
  useEffect(() => { load(); }, [load]);

  const aps = data?.appointments || [];
  const notes = data?.notes || [];
  const now = new Date().toTimeString().slice(0, 5);
  return { data, failed, load, aps, notes, now, minutesFor, setMinutesFor, adding, setAdding };
}
