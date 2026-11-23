/**
 * useBookings.js — appointments' data and actions (2026-10-18, UX redesign Phase F). Shared by
 * VulaBookings.jsx and bookings/BookingsV2.jsx; moved out unchanged — same endpoints
 * (/v1/bookings/{tenant}, migration 050), same bodies, times in Africa/Johannesburg.
 */
import { useCallback, useEffect, useState } from "react";
import { tryApi } from "../../lib/api";
import { toast } from "../ui/index.jsx";
import { isOnSADay, todaySA } from "../../lib/dates";

export const DAYNAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; // ISO 1..7
export const today = () => todaySA();
export function shift(d, n) { const x = new Date(d + "T12:00:00"); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); }
export function mondayOf(d) {
  const x = new Date(d + "T12:00:00");
  const dow = x.getDay() || 7;   // Sun=0 → 7
  x.setDate(x.getDate() - (dow - 1));
  return x.toISOString().slice(0, 10);
}
export const timeOf = (iso) => new Date(iso).toLocaleTimeString("en-ZA", { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Johannesburg" });
export const onDay = (b, d) => isOnSADay(b.start_at, d);

// The server's answer on success; on any refusal `{ error: "<the server's words>" }` — callers
// already check `.error` (a 4xx `{detail}` used to come back looking like success).
const result = (res) => (res.ok ? res.data : { ...(res.data || {}), error: res.error });

/** POST a booking into a free slot (f.start is the slot's label, e.g. "09:30"). */
export function bookSlot(tenantId, date, f, services) {
  const svc = services.find(s => s.id === f.service_id);
  return tryApi(`/v1/bookings/${tenantId}`, {
    method: "POST",
    body: {
      start: `${date}T${f.start}`, service_id: f.service_id || null,
      service_name: svc?.name, customer_name: f.customer_name,
      customer_phone: f.customer_phone, notes: f.notes, channel: "dashboard",
    },
  }).then(result);
}
export function saveService(tenantId, body) {
  return tryApi(`/v1/bookings/${tenantId}/services`, {
    method: "POST", body,
  }).then(result);
}
export async function deleteService(tenantId, id) {
  const res = await tryApi(`/v1/bookings/${tenantId}/services/${id}`, { method: "DELETE" });
  if (!res.ok) toast(`That service wasn't deleted: ${res.error}`, "danger");
  return result(res);
}
export async function saveRules(tenantId, s) {
  const res = await tryApi(`/v1/bookings/${tenantId}/settings`, {
    method: "PUT",
    body: {
      slot_minutes: +s.slot_minutes, workdays: s.workdays, open_time: s.open_time,
      close_time: s.close_time, buffer_min: +s.buffer_min, lead_hours: +s.lead_hours,
      horizon_days: +s.horizon_days,
    },
  });
  if (!res.ok) toast(`Your booking hours weren't saved: ${res.error}`, "danger");
  return result(res);
}

export function useBookings(tenantId) {
  const [view, setView] = useState("day");     // day | week | services | settings
  const [date, setDate] = useState(today());
  const [bookings, setBookings] = useState([]);
  const [weekBookings, setWeekBookings] = useState([]);
  const [services, setServices] = useState([]);
  const [avail, setAvail] = useState(null);
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const loadDay = useCallback(async () => {
    if (!tenantId) return;
    const from = `${date}T00:00:00+02:00`;
    const [b, a] = await Promise.all([
      tryApi(`/v1/bookings/${tenantId}?from=${encodeURIComponent(from)}`),
      tryApi(`/v1/bookings/${tenantId}/availability?date=${date}`),
    ]);
    if (!b.ok || !a.ok) toast(`Couldn't load the day's bookings: ${(!b.ok ? b : a).error}`, "danger");
    setBookings(((b.ok && b.data.bookings) || []).filter(x => onDay(x, date)));
    setAvail(a.ok ? a.data : {});
  }, [tenantId, date]);

  const weekStart = mondayOf(date);
  const loadWeek = useCallback(async () => {
    if (!tenantId) return;
    const from = `${weekStart}T00:00:00+02:00`;
    const res = await tryApi(`/v1/bookings/${tenantId}?from=${encodeURIComponent(from)}`);
    if (!res.ok) toast(`Couldn't load the week's bookings: ${res.error}`, "danger");
    const weekEnd = shift(weekStart, 7);
    setWeekBookings(((res.ok && res.data.bookings) || []).filter(x => (x.start_at || "").slice(0, 10) < weekEnd));
  }, [tenantId, weekStart]);

  const loadServices = useCallback(async () => {
    const res = await tryApi(`/v1/bookings/${tenantId}/services?all=true`);
    if (!res.ok) { toast(`Couldn't load your services: ${res.error}`, "danger"); return; }
    setServices(res.data.services || []);
  }, [tenantId]);

  const loadSettings = useCallback(async () => {
    const res = await tryApi(`/v1/bookings/${tenantId}/settings`);
    if (!res.ok) toast(`Couldn't load your booking hours: ${res.error}`, "danger");
    setSettings((res.ok && res.data.settings) || null);
  }, [tenantId]);

  useEffect(() => { loadDay(); }, [loadDay]);
  useEffect(() => { if (view === "week") loadWeek(); }, [view, loadWeek]);
  useEffect(() => { loadServices(); }, [loadServices]);
  useEffect(() => { if (view === "settings") loadSettings(); }, [view, loadSettings]);

  const setStatus = async (id, status) => {
    const res = await tryApi(`/v1/bookings/${tenantId}/${id}/status`, {
      method: "POST",
      body: { status },
    });
    if (!res.ok) toast(`That booking wasn't changed: ${res.error}`, "danger");
    loadDay();
  };

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 3000); };

  return {
    view, setView, date, setDate, bookings, weekBookings, weekStart, services, avail, settings, setSettings,
    busy, setBusy, msg, flash, loadDay, loadServices, setStatus,
  };
}
